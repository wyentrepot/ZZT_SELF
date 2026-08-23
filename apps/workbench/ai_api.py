"""HTTP adapter for the bounded and scoped AI control plane."""
from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from .ai_auth import AuthorizationError, AuthorizationStore
from .ai_operations import AIControlService, InvalidObservation, SessionBusy, SourceUnavailable
from .ai_store import IdempotencyConflict


def create_ai_router(
    control: AIControlService, auth_store: AuthorizationStore, *,
    admin_key: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/ai/v1", tags=["ai-control"])

    def grant_from_header(authorization: str | None, scope: str, resource: str = "") -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="缺少 Bearer token")
        try:
            return auth_store.authenticate(authorization[7:].strip(), scope=scope, resource=resource or None)
        except AuthorizationError as exc:
            message = str(exc)
            status = 401 if "无效" in message or "过期" in message or "撤销" in message else 403
            raise HTTPException(status_code=status, detail=message) from exc

    def actor(grant: dict) -> str:
        return "ai:" + grant["grant_id"]

    def require_human_admin(request: Request, supplied_key: str | None) -> None:
        host = getattr(request.client, "host", "") if request.client else ""
        if host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(status_code=403, detail="授权管理仅允许本机访问")
        if not admin_key:
            raise HTTPException(status_code=503, detail="未配置人工授权管理密钥")
        if not supplied_key or not secrets.compare_digest(str(supplied_key), str(admin_key)):
            raise HTTPException(status_code=403, detail="人工授权管理密钥无效")

    def authorised_flash_request(request: dict[str, Any], grant: dict) -> dict[str, Any]:
        raw_path = str(request.get("bin_path") or "").strip()
        if not raw_path:
            raise HTTPException(status_code=422, detail="烧录必须提供 bin_path")
        roots = [str(item) for item in grant.get("firmware_roots") or [] if str(item)]
        if not roots:
            raise HTTPException(status_code=403, detail="当前授权未配置允许烧录目录")
        candidate = Path(raw_path).expanduser().resolve()
        for root_text in roots:
            try:
                candidate.relative_to(Path(root_text).expanduser().resolve())
                result = dict(request)
                result["bin_path"] = str(candidate)
                return result
            except ValueError:
                continue
        raise HTTPException(status_code=403, detail="固件路径不在当前授权的允许目录内")

    @router.get("/admin/grants")
    def admin_list_grants(request: Request, x_workbench_admin_key: str | None = Header(None)):
        require_human_admin(request, x_workbench_admin_key)
        return {"grants": auth_store.export_grants()}

    @router.post("/admin/grants", status_code=201)
    def admin_create_grant(
        body: dict[str, Any], request: Request,
        x_workbench_admin_key: str | None = Header(None),
    ):
        require_human_admin(request, x_workbench_admin_key)
        try:
            grant, token = auth_store.create_grant(
                scopes=body.get("scopes") or [],
                resources=body.get("resources") or [],
                ttl_seconds=int(body.get("ttl_seconds") or 0),
                created_by="human-local-admin",
                reason=str(body.get("reason") or ""),
                max_operation_seconds=int(body.get("max_operation_seconds") or 1800),
                firmware_roots=body.get("firmware_roots") or [],
            )
            return {"grant": grant, "token": token}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/admin/grants/{grant_id}/revoke")
    def admin_revoke_grant(
        grant_id: str, request: Request, x_workbench_admin_key: str | None = Header(None),
    ):
        require_human_admin(request, x_workbench_admin_key)
        try:
            return auth_store.revoke(grant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="授权不存在") from exc

    @router.get("/status")
    def status(authorization: str | None = Header(None)):
        grant = grant_from_header(authorization, "status:read")
        return control.status(include_paths="evidence:read" in grant["scopes"])

    @router.get("/audit")
    def audit(authorization: str | None = Header(None)):
        grant = grant_from_header(authorization, "status:read")
        return {"entries": control.audit_entries(grant["resources"])}

    @router.post("/module-sessions/ensure")
    def ensure_module_session(request: dict[str, Any], authorization: str | None = Header(None)):
        resource = str(request.get("mapping_id") or "")
        if request.get("session_id"):
            try:
                resource = control.session_resource(str(request["session_id"]))
            except KeyError:
                resource = str(request["session_id"])
        grant = grant_from_header(authorization, "module_session:ensure", resource)
        try:
            return control.ensure_module_session(request, actor=actor(grant))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/module-sessions/{session_id}/stop")
    def stop_module_session(session_id: str, request: dict[str, Any] | None = None,
                            authorization: str | None = Header(None)):
        try:
            resource = control.session_resource(session_id)
            grant = grant_from_header(authorization, "module_session:stop", resource)
            return control.stop_module_session(session_id, actor=actor(grant), force=bool((request or {}).get("force")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/module-sessions/{session_id}/send")
    def send_module(session_id: str, request: dict[str, Any], authorization: str | None = Header(None)):
        try:
            resource = control.session_resource(session_id)
            grant = grant_from_header(authorization, "module_send:execute", resource)
            existing = control.idempotent_operation(str(request.get("client_request_id") or ""))
            if existing is not None:
                grant_from_header(
                    authorization, "module_send:execute", control.operation_resource(existing["operation_id"]),
                )
            return control.send_module(
                session_id, request, actor=actor(grant),
                client_request_id=str(request.get("client_request_id") or ""),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/flash-operations", status_code=202)
    def flash_operation(request: dict[str, Any], authorization: str | None = Header(None)):
        session_id = str(request.get("session_id") or "")
        try:
            resource = control.session_resource(session_id)
            grant = grant_from_header(authorization, "module_flash:execute", resource)
            existing = control.idempotent_operation(str(request.get("client_request_id") or ""))
            if existing is not None:
                grant_from_header(
                    authorization, "module_flash:execute", control.operation_resource(existing["operation_id"]),
                )
            return control.flash_module(authorised_flash_request(request, grant), actor=actor(grant))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/listener/ensure")
    def ensure_listener(request: dict[str, Any], authorization: str | None = Header(None)):
        resource = str(request.get("mapping_id") or request.get("port") or "listener-main")
        grant = grant_from_header(authorization, "listener:ensure", resource)
        try:
            return control.ensure_listener(request, actor=actor(grant))
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/listener/stop")
    def stop_listener(
        request: dict[str, Any] | None = None,
        authorization: str | None = Header(None),
    ):
        try:
            resource = control.listener_resource()
            grant = grant_from_header(authorization, "listener:stop", resource)
            return control.stop_listener(
                actor=actor(grant), force=bool((request or {}).get("force")),
            )
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/observations", status_code=202)
    def create_observation(
        request: dict[str, Any], authorization: str | None = Header(None),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        target = request.get("target") or {}
        resource = str(target.get("mapping_id") or "listener-main")
        if target.get("session_id"):
            try:
                resource = control.session_resource(str(target["session_id"]))
            except KeyError:
                resource = str(target["session_id"])
        grant = grant_from_header(authorization, "observation:create", resource)
        client_request_id = str(request.get("client_request_id") or idempotency_key or "")
        existing = control.idempotent_operation(client_request_id)
        if existing is not None:
            grant_from_header(
                authorization, "observation:create", control.operation_resource(existing["operation_id"]),
            )
        try:
            operation = control.create_observation(
                request, actor=actor(grant),
                client_request_id=client_request_id,
            )
            return {
                "operation_id": operation["operation_id"], "state": operation["state"],
                "version": operation["version"], "result": operation["result"],
            }
        except InvalidObservation as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc

    @router.get("/operations/{operation_id}")
    def get_operation(operation_id: str, authorization: str | None = Header(None)):
        try:
            grant_from_header(authorization, "evidence:read", control.operation_resource(operation_id))
            return control.get_operation(operation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"操作不存在：{exc.args[0]}") from exc

    @router.get("/operations/{operation_id}/wait")
    def wait_operation(operation_id: str, timeout_seconds: int = Query(30, ge=0, le=30),
                       authorization: str | None = Header(None)):
        try:
            grant_from_header(authorization, "evidence:read", control.operation_resource(operation_id))
            return control.wait_operation(operation_id, timeout_seconds=timeout_seconds)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"操作不存在：{exc.args[0]}") from exc

    @router.post("/operations/{operation_id}/cancel")
    def cancel_operation(operation_id: str, authorization: str | None = Header(None)):
        try:
            grant = grant_from_header(
                authorization, "observation:create", control.operation_resource(operation_id),
            )
            return control.cancel_operation(operation_id, actor=actor(grant))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"操作不存在：{exc.args[0]}") from exc
        except SessionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/artifacts/{artifact_id}")
    def artifact_manifest(artifact_id: str, authorization: str | None = Header(None)):
        try:
            grant_from_header(authorization, "evidence:read", control.artifact_resource(artifact_id))
            return control.artifact_manifest(artifact_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Artifact 不存在") from exc

    @router.get("/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str, authorization: str | None = Header(None)):
        try:
            grant_from_header(authorization, "evidence:read", control.artifact_resource(artifact_id))
            return control.read_artifact(artifact_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Artifact 不存在") from exc

    @router.get("/listener/schema")
    def listener_schema(authorization: str | None = Header(None)):
        grant_from_header(authorization, "evidence:read", control.listener_resource())
        return control.listener_schema()

    @router.get("/listener/indexes")
    def listener_indexes(authorization: str | None = Header(None)):
        grant_from_header(authorization, "evidence:read", control.listener_resource())
        try:
            return control.listener_indexes()
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/listener/indexes/{index_id}/frames")
    def listener_index_frames(
        index_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        query: str = Query("", max_length=100),
        nid: str = Query("", max_length=16),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        after_id: int | None = Query(None, ge=0),
        authorization: str | None = Header(None),
    ):
        grant_from_header(authorization, "evidence:read", control.listener_resource())
        try:
            return control.listener_frame_page(
                index_id, offset=offset, limit=limit, query=query, nid=nid,
                start_time=start_time, end_time=end_time, after_id=after_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该索引") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/listener/indexes/{index_id}/frames/{frame_id}")
    def listener_index_frame_detail(
        index_id: str, frame_id: int, authorization: str | None = Header(None),
    ):
        grant_from_header(authorization, "evidence:read", control.listener_resource())
        try:
            return control.listener_frame_detail(index_id, frame_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该索引或帧") from exc
        except SourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
