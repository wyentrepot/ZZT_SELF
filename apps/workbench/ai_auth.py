"""Scoped authorization grants for the AI control plane."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


class AuthorizationError(PermissionError):
    pass


# P1 only maps v2 names to existing grant scopes.  It neither changes grant
# storage nor v1's authenticate semantics.
V2_CAPABILITY_SCOPES: dict[str, frozenset[str]] = {
    "capabilities.read": frozenset({"status:read"}),
    "investigations.create": frozenset({"observation:create", "evidence:read"}),
    "verification_runs.create": frozenset({"simcon:verify"}),
    "module_actions.ensure": frozenset({"module_session:ensure"}),
    "module_actions.send": frozenset({"module_send:execute"}),
    "module_actions.stop": frozenset({"module_session:stop"}),
    "flash_jobs.create": frozenset({"module_flash:execute"}),
    "jobs.read": frozenset({"evidence:read"}),
    "jobs.cancel": frozenset({"observation:create"}),
    "jobs.evidence.read": frozenset({"evidence:read"}),
}

# A capability is usable only against aliases from these logical sources.  The
# mapping deliberately uses opaque resource IDs, never physical serial names.
V2_CAPABILITY_SOURCES: dict[str, frozenset[str]] = {
    "capabilities.read": frozenset({"module_log", "listener", "simcon"}),
    "investigations.create": frozenset({"module_log", "listener", "simcon"}),
    "verification_runs.create": frozenset({"simcon"}),
    "module_actions.ensure": frozenset({"module_log"}),
    "module_actions.send": frozenset({"module_log"}),
    "module_actions.stop": frozenset({"module_log"}),
    "flash_jobs.create": frozenset({"module_log"}),
    "jobs.read": frozenset({"module_log", "listener", "simcon"}),
    "jobs.cancel": frozenset({"module_log", "listener", "simcon"}),
    "jobs.evidence.read": frozenset({"module_log", "listener", "simcon"}),
}


def grant_allows_v2_capability(grant: dict, capability: str) -> bool:
    """Whether a valid LAN grant contains every existing scope for v2 capability."""
    required = V2_CAPABILITY_SCOPES[capability]
    return required.issubset({str(scope) for scope in grant.get("scopes") or []})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthorizationStore:
    """Short-lived grants that persist only bearer-token SHA-256 digests."""

    STORAGE_VERSION = 1

    def __init__(self, storage_path: Path | str | None = None):
        self._lock = threading.RLock()
        self._storage_path = Path(storage_path) if storage_path else None
        self._grants: dict[str, dict] = {}
        self._token_index: dict[str, str] = {}
        self._load()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalise_strings(values: Iterable[str]) -> list[str]:
        return sorted({str(value).strip() for value in values if str(value).strip()})

    def _load(self) -> None:
        if self._storage_path is None or not self._storage_path.is_file():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if payload.get("version") != self.STORAGE_VERSION:
                return
            for grant in payload.get("grants", []):
                grant_id = str(grant.get("grant_id") or "")
                digest = str(grant.get("token_sha256") or "")
                if not grant_id or len(digest) != 64:
                    continue
                self._grants[grant_id] = dict(grant)
                self._token_index[digest] = grant_id
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A damaged local cache must not turn into an authentication bypass.
            self._grants = {}
            self._token_index = {}

    def _persist_locked(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        payload = {"version": self.STORAGE_VERSION, "grants": list(self._grants.values())}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._storage_path)

    def create_grant(
        self, *, scopes: Iterable[str], resources: Iterable[str], ttl_seconds: int,
        created_by: str, reason: str = "", max_operation_seconds: int = 1800,
        firmware_roots: Iterable[str] = (),
    ) -> tuple[dict, str]:
        if not 1 <= int(ttl_seconds) <= 86400:
            raise ValueError("ttl_seconds 必须在 1 到 86400 之间")
        clean_scopes = self._normalise_strings(scopes)
        if not clean_scopes:
            raise ValueError("至少需要一个授权 scope")
        token = secrets.token_urlsafe(32)
        grant_id = "grant-" + uuid.uuid4().hex[:16]
        now = _utcnow()
        grant = {
            "grant_id": grant_id,
            "scopes": clean_scopes,
            "resources": self._normalise_strings(resources) or ["*"],
            "expires_at": (now + timedelta(seconds=int(ttl_seconds))).isoformat(),
            "created_at": now.isoformat(),
            "created_by": str(created_by or "human"),
            "reason": str(reason or ""),
            "max_operation_seconds": min(max(int(max_operation_seconds), 1), 86400),
            "firmware_roots": self._normalise_strings(firmware_roots),
            "token_sha256": self._digest(token),
            "revoked_at": None,
        }
        with self._lock:
            self._grants[grant_id] = grant
            self._token_index[grant["token_sha256"]] = grant_id
            self._persist_locked()
        return self._public_grant(grant), token

    @staticmethod
    def _public_grant(grant: dict) -> dict:
        return {key: value for key, value in grant.items() if key != "token_sha256"}

    def export_grants(self) -> list[dict]:
        with self._lock:
            return [self._public_grant(grant) for grant in self._grants.values()]

    def revoke(self, grant_id: str) -> dict:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise KeyError(grant_id)
            grant["revoked_at"] = _utcnow().isoformat()
            self._persist_locked()
            return self._public_grant(grant)

    def authenticate(self, token: str, scope: str | None = None, resource: str | None = None) -> dict:
        digest = self._digest(str(token or ""))
        with self._lock:
            grant_id = self._token_index.get(digest)
            grant = self._grants.get(grant_id or "")
            if grant is None or not secrets.compare_digest(grant["token_sha256"], digest):
                raise AuthorizationError("Bearer token 无效")
            if grant["revoked_at"] is not None:
                raise AuthorizationError("授权已撤销")
            if datetime.fromisoformat(grant["expires_at"]) <= _utcnow():
                raise AuthorizationError("授权已过期")
            if scope and scope not in grant["scopes"]:
                raise AuthorizationError(f"缺少授权范围：{scope}")
            if resource and "*" not in grant["resources"] and resource not in grant["resources"]:
                raise AuthorizationError(f"资源不在授权范围内：{resource}")
            return self._public_grant(grant)
