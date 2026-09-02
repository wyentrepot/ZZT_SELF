"""Real-peer access-domain resolution for AI v2.

Only ASGI's direct peer address is trusted.  Host and forwarding headers are
intentionally ignored because this workbench does not establish a trusted
proxy boundary for the local-full domain.
"""
from __future__ import annotations

from fastapi import Request

from .ai_contracts import AccessContext, AccessZone


_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1", "testclient"})


def is_loopback_peer(request: Request) -> bool:
    peer = getattr(request.client, "host", "") if request.client else ""
    return str(peer).strip().lower() in _LOOPBACK_PEERS


def resolve_access_context(request: Request, *, local_full_enabled: bool) -> AccessContext:
    if local_full_enabled and is_loopback_peer(request):
        return AccessContext(zone=AccessZone.LOCAL_FULL, actor="local:loopback")
    return AccessContext(zone=AccessZone.LAN_SCOPED, actor="lan:pending_grant")


def grant_access_context(grant: dict) -> AccessContext:
    return AccessContext(zone=AccessZone.LAN_SCOPED, actor="ai:" + str(grant["grant_id"]))
