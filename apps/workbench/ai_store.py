"""Thread-safe persistent operation, artifact, and audit storage."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


TERMINAL_STATES = frozenset({"matched", "succeeded", "timed_out", "cancelled", "source_stopped", "error", "interrupted"})

# list_operations 的摘要字段与 limit 硬上限：列表只出元数据，绝不带 payload / result
# （现网 operations.json 已 485 条 / 1.9 MB，整条返回会把响应撑爆）。
OPERATION_SUMMARY_FIELDS = (
    "operation_id", "kind", "actor", "state", "created_at", "updated_at",
    "client_request_id", "error",
)
LIST_LIMIT_MAX = 100


class IdempotencyConflict(RuntimeError):
    """A client idempotency key was reused for a different request."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationStore:
    STORAGE_VERSION = 1

    def __init__(self, storage_path: Path | str | None = None):
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._storage_path = Path(storage_path) if storage_path else None
        self._operations: dict[str, dict] = {}
        self._request_ids: dict[str, str] = {}
        self._audit: list[dict] = []
        self._artifacts: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._storage_path is None or not self._storage_path.is_file():
            return
        changed = False
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if payload.get("version") != self.STORAGE_VERSION:
                return
            for operation in payload.get("operations", []):
                operation_id = str(operation.get("operation_id") or "")
                if not operation_id:
                    continue
                restored = dict(operation)
                if restored.get("state") not in TERMINAL_STATES:
                    restored["state"] = "interrupted"
                    restored["error"] = "服务重启，未完成操作已中断"
                    restored["updated_at"] = now_iso()
                    restored["version"] = int(restored.get("version") or 0) + 1
                    changed = True
                self._operations[operation_id] = restored
                request_id = restored.get("client_request_id")
                if request_id:
                    self._request_ids[str(request_id)] = operation_id
            self._audit = [dict(item) for item in payload.get("audit", []) if isinstance(item, dict)]
            for artifact in payload.get("artifacts", []):
                artifact_id = str(artifact.get("artifact_id") or "")
                if artifact_id:
                    self._artifacts[artifact_id] = dict(artifact)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._operations = {}
            self._request_ids = {}
            self._audit = []
            self._artifacts = {}
            return
        if changed:
            with self._lock:
                self._persist_locked()

    def _persist_locked(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        payload = {
            "version": self.STORAGE_VERSION,
            "operations": list(self._operations.values()),
            "audit": self._audit,
            "artifacts": list(self._artifacts.values()),
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._storage_path)

    @staticmethod
    def _idempotency_fingerprint(kind: str, payload: dict) -> str:
        canonical = json.dumps(
            {"kind": str(kind), "payload": payload},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create(self, kind: str, actor: str, payload: dict, *, client_request_id: str = "",
               idempotency_fingerprint: str | None = None,
               idempotency_replay_fingerprint: str | None = None) -> dict:
        with self._changed:
            fingerprint = idempotency_fingerprint or self._idempotency_fingerprint(kind, payload)
            if client_request_id and client_request_id in self._request_ids:
                existing = self._operations[self._request_ids[client_request_id]]
                if existing.get("idempotency_fingerprint") != fingerprint:
                    raise IdempotencyConflict("幂等键与既有请求不一致")
                return copy.deepcopy(existing)
            operation_id = "op-" + uuid.uuid4().hex[:16]
            operation = {
                "operation_id": operation_id, "kind": kind, "actor": actor,
                "state": "created", "created_at": now_iso(), "updated_at": now_iso(),
                "version": 1, "payload": copy.deepcopy(payload), "result": None, "error": None,
                "client_request_id": client_request_id or None,
                "idempotency_fingerprint": fingerprint if client_request_id else None,
                "idempotency_replay_fingerprint": (
                    idempotency_replay_fingerprint if client_request_id else None
                ),
            }
            self._operations[operation_id] = operation
            if client_request_id:
                self._request_ids[client_request_id] = operation_id
            self._persist_locked()
            self._changed.notify_all()
            return copy.deepcopy(operation)

    def get(self, operation_id: str) -> dict:
        with self._lock:
            if operation_id not in self._operations:
                raise KeyError(operation_id)
            return copy.deepcopy(self._operations[operation_id])

    def by_client_request_id(self, client_request_id: str) -> dict | None:
        """Return the already-created operation for an idempotency key, if any."""
        if not client_request_id:
            return None
        with self._lock:
            operation_id = self._request_ids.get(str(client_request_id))
            if operation_id is None:
                return None
            return copy.deepcopy(self._operations[operation_id])

    def set_state(self, operation_id: str, state: str, *, result=None, error: str | None = None) -> dict:
        with self._changed:
            operation = self._operations.get(operation_id)
            if operation is None:
                raise KeyError(operation_id)
            if operation["state"] in TERMINAL_STATES:
                return copy.deepcopy(operation)
            operation["state"] = state
            if result is not None:
                operation["result"] = copy.deepcopy(result)
            if error is not None:
                operation["error"] = str(error)
            operation["updated_at"] = now_iso()
            operation["version"] += 1
            self._persist_locked()
            self._changed.notify_all()
            return copy.deepcopy(operation)

    def register_artifact(self, *, operation_id: str, resource: str, kind: str, content: dict) -> dict:
        """Register bounded result content. The caller cannot supply a filesystem path."""
        with self._changed:
            artifact_id = "art-" + uuid.uuid4().hex[:16]
            manifest = {
                "artifact_id": artifact_id, "operation_id": str(operation_id),
                "resource": str(resource), "kind": str(kind), "created_at": now_iso(),
                "content": copy.deepcopy(content),
            }
            self._artifacts[artifact_id] = manifest
            self._persist_locked()
            self._changed.notify_all()
            return self._public_artifact(manifest)

    @staticmethod
    def _public_artifact(artifact: dict) -> dict:
        return {key: copy.deepcopy(value) for key, value in artifact.items() if key != "content"}

    def get_artifact(self, artifact_id: str) -> dict:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise KeyError(artifact_id)
            return self._public_artifact(artifact)

    def read_artifact(self, artifact_id: str) -> dict:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise KeyError(artifact_id)
            return {"manifest": self._public_artifact(artifact), "content": copy.deepcopy(artifact["content"])}

    def audit(self, *, actor: str, action: str, resource: str = "", result: str = "", operation_id: str = "") -> None:
        with self._lock:
            self._audit.append({
                "at": now_iso(), "actor": actor, "action": action, "resource": resource,
                "result": result, "operation_id": operation_id or None,
            })
            self._persist_locked()

    def list_active(self) -> list[dict]:
        with self._lock:
            return [
                copy.deepcopy(item) for item in self._operations.values()
                if item["state"] not in TERMINAL_STATES
            ]

    def list_operations(self, *, kinds=None, states=None, since=None,
                        limit: int = 20) -> list[dict]:
        """按条件列出操作摘要，created_at 新的在前。

        为什么不复用 list_active()：list_active 只返回非终态，且把整条操作（含
        payload / result）都拷出来；job_id 一丢时既找不回 succeeded / failed 这类
        终态，也没法做 limit 摘要（现网库已 1.9 MB）。故单开本方法，只读元数据。

        边界：
        - limit 默认 20，内部硬上限 100（传 500 按 100 处理）；limit <= 0 返回空
          列表（不抛错，便于 HTTP 参数直通）；
        - kinds / states 为 None 表示不过滤，空集合表示“什么都不匹配”；
        - since 为 ISO 时间字符串，只保留 created_at >= since，解析失败抛
          ValueError；无时区的 since 按 UTC 解释，与 now_iso() 的 +00:00 同口径；
        - 每项只含 OPERATION_SUMMARY_FIELDS 八个字段的深拷贝，绝不含 payload /
          result，调用方改返回值不会影响内部存储。
        """
        kind_filter = None if kinds is None else {str(item) for item in kinds}
        state_filter = None if states is None else {str(item) for item in states}
        since_at = self._parse_timestamp(since) if since is not None else None
        try:
            effective_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit 必须是整数") from exc
        effective_limit = min(effective_limit, LIST_LIMIT_MAX)
        if effective_limit <= 0:
            return []
        with self._lock:
            matched: list[dict] = []
            for operation in self._operations.values():
                if kind_filter is not None and str(operation.get("kind")) not in kind_filter:
                    continue
                if state_filter is not None and str(operation.get("state")) not in state_filter:
                    continue
                if since_at is not None and self._created_at(operation) < since_at:
                    continue
                matched.append(operation)
            matched.sort(
                key=lambda item: (self._created_at(item), str(item.get("operation_id") or "")),
                reverse=True,
            )
            return [
                copy.deepcopy({field: operation.get(field) for field in OPERATION_SUMMARY_FIELDS})
                for operation in matched[:effective_limit]
            ]

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        """解析 ISO 时间；无时区按 UTC 补齐。格式非法抛 ValueError（不静默忽略）。"""
        text = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"不是合法的 ISO 时间：{text!r}") from exc
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _created_at(operation: dict) -> datetime:
        """排序/过滤用的创建时间；历史脏数据缺字段时沉到最早，避免拖垮整次列表。"""
        try:
            return OperationStore._parse_timestamp(operation.get("created_at"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    def audit_entries(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._audit)
