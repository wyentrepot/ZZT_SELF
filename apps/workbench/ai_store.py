"""Thread-safe persistent operation, artifact, and audit storage."""
from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


TERMINAL_STATES = frozenset({"matched", "succeeded", "timed_out", "cancelled", "source_stopped", "error", "interrupted"})


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

    def create(self, kind: str, actor: str, payload: dict, *, client_request_id: str = "") -> dict:
        with self._changed:
            if client_request_id and client_request_id in self._request_ids:
                return copy.deepcopy(self._operations[self._request_ids[client_request_id]])
            operation_id = "op-" + uuid.uuid4().hex[:16]
            operation = {
                "operation_id": operation_id, "kind": kind, "actor": actor,
                "state": "created", "created_at": now_iso(), "updated_at": now_iso(),
                "version": 1, "payload": copy.deepcopy(payload), "result": None, "error": None,
                "client_request_id": client_request_id or None,
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

    def audit_entries(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._audit)
