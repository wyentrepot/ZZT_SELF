"""P1 HTTP adapter for the typed, task-oriented AI v2 facade."""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request

from .ai_access import grant_access_context, resolve_access_context
from .ai_auth import (
    AuthorizationError,
    AuthorizationStore,
    V2_CAPABILITY_SCOPES,
    V2_CAPABILITY_SOURCES,
    grant_allows_v2_capability,
)
from .ai_contracts import Capability, CapabilitySnapshot, ResourceAlias, SourceHealth, SourceKind
from .ai_contracts import (
    EvidenceLevel, EvidenceView, FlashJobRequest, InvestigationRequest, JobEnvelope,
    ModuleActionRequest, VerificationRunRequest,
)
from .ai_capability_service import AICapabilityService
from .ai_capability_service import EvidenceRefForbidden
from .ai_operations import AIControlService
from .ai_operations import InvalidObservation, SessionBusy, SourceUnavailable
from .ai_store import IdempotencyConflict


def _bearer_grant(authorization: str | None, auth_store: AuthorizationStore) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer token")
    try:
        return auth_store.authenticate(authorization[7:].strip(), scope="status:read")
    except AuthorizationError as exc:
        message = str(exc)
        status = 401 if any(word in message for word in ("无效", "过期", "撤销")) else 403
        raise HTTPException(status_code=status, detail=message) from exc


def _v2_grant(authorization: str | None, auth_store: AuthorizationStore,
              capability: str, resources: list[str] | None = None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer token")
    try:
        grant = auth_store.authenticate(authorization[7:].strip())
    except AuthorizationError as exc:
        message = str(exc)
        raise HTTPException(status_code=401 if any(word in message for word in ("无效", "过期", "撤销")) else 403,
                            detail=message) from exc
    if not grant_allows_v2_capability(grant, capability):
        raise HTTPException(status_code=403, detail=f"缺少 v2 能力范围：{capability}")
    allowed = {str(item) for item in grant.get("resources") or []}
    if "*" not in allowed:
        for resource in resources or []:
            if resource and resource not in allowed:
                raise HTTPException(status_code=403, detail=f"资源不在授权范围内：{resource}")
    return grant


_SAFE_RESOURCE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PHYSICAL_SERIAL_ALIAS = re.compile(r"^(?:COM|LPT)[0-9]+$", re.IGNORECASE)


def _safe_resource_alias(value: object) -> str:
    alias = str(value or "").strip()
    if not _SAFE_RESOURCE_ALIAS.fullmatch(alias):
        return ""
    if _PHYSICAL_SERIAL_ALIAS.fullmatch(alias) or alias.lower().startswith(("tty", "cu.")):
        return ""
    return alias


def _resource_aliases(control: AIControlService, grant: dict | None) -> list[ResourceAlias]:
    aliases: list[ResourceAlias] = []
    sessions = []
    if control.module_service is not None:
        try:
            sessions = control.module_service.list_sessions()
        except Exception:
            sessions = []
    for session in sessions:
        alias = _safe_resource_alias((session.get("port_identity") or {}).get("mapping_id"))
        if alias:
            aliases.append(ResourceAlias(alias=alias, source="module_log"))
    if control.listener_service is not None:
        aliases.append(ResourceAlias(alias="listener-main", source="listener"))
    if control.simcon_service is not None:
        aliases.append(ResourceAlias(alias="simcon", source="simcon"))

    unique = {(item.alias, item.source): item for item in aliases}
    result = sorted(unique.values(), key=lambda item: (item.alias, item.source))
    if grant is None or "*" in grant.get("resources", []):
        return result
    granted_resources = {str(item) for item in grant.get("resources") or []}
    return [item for item in result if item.alias in granted_resources]


def capability_snapshot(*, context, control: AIControlService, grant: dict | None) -> CapabilitySnapshot:
    aliases = _resource_aliases(control, grant)
    capabilities: list[Capability] = []
    for name in sorted(V2_CAPABILITY_SCOPES):
        resources = [
            item.alias for item in aliases
            if item.source.value in V2_CAPABILITY_SOURCES[name]
        ]
        allowed = grant is None or (grant_allows_v2_capability(grant, name) and bool(resources))
        if allowed:
            capabilities.append(Capability(name=name, allowed=True, resources=resources))

    source_health = [
        (SourceKind.MODULE_LOG, SourceHealth(available=control.module_service is not None,
                     reason=None if control.module_service is not None else "module_service_unavailable")),
        (SourceKind.LISTENER, SourceHealth(available=control.listener_service is not None,
                     reason=None if control.listener_service is not None else "listener_service_unavailable")),
        (SourceKind.SIMCON, SourceHealth(available=control.simcon_service is not None,
                     reason=None if control.simcon_service is not None else "simcon_service_unavailable")),
    ]
    return CapabilitySnapshot(
        capability_revision="ai-v2-p1", access=context, capabilities=capabilities,
        resource_aliases=aliases, source_health=dict(source_health),
    )


def create_ai_v2_router(
    control: AIControlService, auth_store: AuthorizationStore, *, local_full_enabled: bool,
) -> APIRouter:
    router = APIRouter(prefix="/api/ai/v2", tags=["ai-task-facade"])
    capability_service = AICapabilityService(control)

    @router.get("/capabilities", response_model=CapabilitySnapshot)
    def get_capabilities(request: Request, authorization: str | None = Header(None)) -> CapabilitySnapshot:
        context = resolve_access_context(request, local_full_enabled=local_full_enabled)
        grant = None
        if context.zone != "local_full":
            grant = _bearer_grant(authorization, auth_store)
            context = grant_access_context(grant)
        control.store.audit(
            actor=context.actor,
            action="ai_v2.capabilities.read",
            resource="workbench",
            result=context.zone.value,
        )
        return capability_snapshot(context=context, control=control, grant=grant)

    def _context(request: Request, authorization: str | None, capability: str,
                 resources: list[str] | None = None):
        context = resolve_access_context(request, local_full_enabled=local_full_enabled)
        if context.zone == "local_full":
            return context
        grant = _v2_grant(authorization, auth_store, capability, resources)
        return grant_access_context(grant)

    def _module_resource(body: dict) -> str:
        session_id = str(body.get("session_id") or "")
        if session_id:
            try:
                return control.session_resource(session_id)
            except KeyError:
                return session_id
        return str(body.get("mapping_id") or "")

    def _authorised_flash_request(body: dict, grant: dict | None) -> dict:
        raw_path = str(body.get("bin_path") or "").strip()
        if not raw_path:
            raise HTTPException(status_code=422, detail="烧录必须提供 bin_path")
        roots = [str(item) for item in (grant or {}).get("firmware_roots") or [] if str(item)]
        if not roots:
            roots = [item for item in os.environ.get("WORKBENCH_AI_FIRMWARE_ROOTS", "").split(os.pathsep) if item]
        if not roots:
            raise HTTPException(status_code=403, detail="当前授权未配置允许烧录目录")
        candidate = Path(raw_path).expanduser().resolve()
        for root_text in roots:
            try:
                candidate.relative_to(Path(root_text).expanduser().resolve())
                result = dict(body)
                result["bin_path"] = str(candidate)
                return result
            except ValueError:
                continue
        raise HTTPException(status_code=403, detail="固件路径不在当前授权的允许目录内")

    @router.post("/investigations", status_code=202, response_model=JobEnvelope)
    def create_investigation(request: Request, body: InvestigationRequest,
                             authorization: str | None = Header(None)) -> JobEnvelope:
        resources = [capability_service._resource_for(
            control, item.model_dump(mode="json", exclude_none=True),
        ) for item in body.observations]
        context = _context(request, authorization, "investigations.create", resources)
        try:
            return capability_service.start_investigation(body, context=context)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (InvalidObservation, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/module-actions", status_code=202, response_model=JobEnvelope)
    def create_module_action(request: Request, body: ModuleActionRequest,
                             authorization: str | None = Header(None)) -> JobEnvelope:
        payload = body.model_dump(mode="json", exclude_none=True)
        resource = _module_resource(payload)
        context = _context(request, authorization, f"module_actions.{body.action}", [resource])
        if context.zone.value != "local_full" and not resource:
            raise HTTPException(status_code=403, detail="局域网写操作必须提供已授权的逻辑资源")
        try:
            return capability_service.start_module_action(body, context=context)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模块会话不存在") from exc
        except (InvalidObservation, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/verification-runs", status_code=202, response_model=JobEnvelope)
    def create_verification_run(request: Request, body: VerificationRunRequest,
                                authorization: str | None = Header(None)) -> JobEnvelope:
        context = _context(request, authorization, "verification_runs.create", ["simcon"])
        try:
            return capability_service.start_verification_run(body, context=context)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (InvalidObservation, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/flash-jobs", status_code=202, response_model=JobEnvelope)
    def create_flash_job(request: Request, body: FlashJobRequest,
                         authorization: str | None = Header(None)) -> JobEnvelope:
        payload = body.model_dump(mode="json", exclude_none=True)
        resource = _module_resource(payload)
        context = _context(request, authorization, "flash_jobs.create", [resource])
        if context.zone.value != "local_full" and not resource:
            raise HTTPException(status_code=403, detail="局域网烧录必须提供已授权的逻辑资源")
        grant = None if context.zone == "local_full" else _v2_grant(
            authorization, auth_store, "flash_jobs.create", [resource],
        )
        try:
            return capability_service.start_flash_job(
                _authorised_flash_request(payload, grant), context=context,
            )
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模块会话不存在") from exc
        except (InvalidObservation, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/jobs/{job_id}", response_model=JobEnvelope)
    def read_job(request: Request, job_id: str,
                 wait_seconds: int = Query(0, ge=0, le=30),
                 authorization: str | None = Header(None)) -> JobEnvelope:
        try:
            operation = control.store.get(capability_service._operation_id(job_id))
            resources = operation.get("payload", {}).get("resources") or []
            _context(request, authorization, "jobs.read", resources)
            return capability_service.read_job(job_id, wait_seconds=wait_seconds)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job 不存在") from exc

    @router.get("/jobs/{job_id}/evidence", response_model=EvidenceView)
    def read_job_evidence(request: Request, job_id: str,
                          level: EvidenceLevel = Query(EvidenceLevel.L1),
                          ref: list[str] = Query(default=[]),
                          authorization: str | None = Header(None)) -> EvidenceView:
        try:
            operation = control.store.get(capability_service._operation_id(job_id))
            resources = operation.get("payload", {}).get("resources") or []
            _context(request, authorization, "jobs.evidence.read", resources)
            return capability_service.read_job_evidence(job_id, level=level, refs=ref)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job 不存在") from exc
        except EvidenceRefForbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (InvalidObservation, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/jobs/{job_id}/cancel", response_model=JobEnvelope)
    def cancel_job(request: Request, job_id: str,
                   authorization: str | None = Header(None)) -> JobEnvelope:
        try:
            operation = control.store.get(capability_service._operation_id(job_id))
            resources = operation.get("payload", {}).get("resources") or []
            context = _context(request, authorization, "jobs.cancel", resources)
            return capability_service.cancel_job(job_id, actor=context.actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job 不存在") from exc
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
