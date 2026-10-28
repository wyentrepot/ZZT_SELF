"""P1 HTTP adapter for the typed, task-oriented AI v2 facade."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
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
from .ai_store import LIST_LIMIT_MAX, IdempotencyConflict
from sim_concentrator.scenario_codec import ScenarioParamError, validate_steps


def _bearer_grant(authorization: str | None, auth_store: AuthorizationStore,
                  *, scope: str = "status:read") -> dict:
    """按 Bearer 取授权；scope 缺省 status:read（保持既有能力清单端点行为）。

    只读端点各自声明所需 scope（如 simcon:read），不另写一套 401/403 映射。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        # REQS-0034 BR-1：缺 token 与缺环境变量是两回事——直接提示本机 loopback 免 token 姿势，
        # 不再裸 401「缺少 Bearer token」；有 token 但无效/过期/撤销时走下方原文案。
        raise HTTPException(
            status_code=401,
            detail="缺少 Bearer token；本机 loopback 可在启动时设 WORKBENCH_LOCAL_FULL_ACCESS=1 免 token",
        )
    try:
        return auth_store.authenticate(authorization[7:].strip(), scope=scope)
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


# ---------------------------------------------------------------------------
# 烧录白名单与授权来源审计：v1（ai_api）与 v2 的唯一实现
# ---------------------------------------------------------------------------

_FLASH_ROOTS_ENV = "WORKBENCH_AI_FIRMWARE_ROOTS"


@dataclass(frozen=True)
class FlashAuthorization:
    """一次烧录请求被放行的依据；只用于审计，不改变烧录输入。"""

    bin_path: str
    firmware_root: str
    authorization_source: str  # grant | env | local_full_env


def resolve_flash_authorization(
    body: dict, grant: dict | None, *, local_full: bool = False,
) -> FlashAuthorization:
    """校验 bin_path 是否落在允许烧录目录内，并给出授权来源。

    v1/v2 共用同一口径：grant.firmware_roots 优先 → WORKBENCH_AI_FIRMWARE_ROOTS
    兜底 → 两者都为空 403 → 路径不在任一 root 内 403。此前 v1 缺 env 兜底，
    同一台机器两条路径结论不一致，故收敛到本函数。
    """
    raw_path = str(body.get("bin_path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=422, detail="烧录必须提供 bin_path")
    roots = [str(item) for item in (grant or {}).get("firmware_roots") or [] if str(item)]
    source = "grant"
    if not roots:
        roots = [item for item in os.environ.get(_FLASH_ROOTS_ENV, "").split(os.pathsep) if item]
        source = "local_full_env" if local_full else "env"
    if not roots:
        raise HTTPException(status_code=403, detail="当前授权未配置允许烧录目录")
    candidate = Path(raw_path).expanduser().resolve()
    for root_text in roots:
        root = Path(root_text).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        return FlashAuthorization(
            bin_path=str(candidate), firmware_root=str(root), authorization_source=source,
        )
    raise HTTPException(status_code=403, detail="固件路径不在当前授权的允许目录内")


def record_flash_authorization(
    control: AIControlService, *, flash_authorization: FlashAuthorization,
    actor: str, resource: str, operation_id: str,
) -> None:
    """把「这次烧录凭什么被允许」写进 audit；不新增任何拒绝分支。"""
    control.store.audit(
        actor=actor,
        action="flash.authorization",
        resource=resource,
        result=json.dumps({
            "authorization_source": flash_authorization.authorization_source,
            "bin_path": flash_authorization.bin_path,
            "firmware_root": flash_authorization.firmware_root,
        }, ensure_ascii=False, sort_keys=True),
        operation_id=operation_id,
    )


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
    if control.simeter_service is not None:
        aliases.append(ResourceAlias(alias="simeter", source="simeter"))

    unique = {(item.alias, item.source): item for item in aliases}
    result = sorted(unique.values(), key=lambda item: (item.alias, item.source))
    if grant is None or "*" in grant.get("resources", []):
        return result
    granted_resources = {str(item) for item in grant.get("resources") or []}
    return [item for item in result if item.alias in granted_resources]


# REQS-0033 BR-7：每项 v2 能力的最小 HTTP 调用链示例（"一键小抄"）。
# 口径与 ai-control-plane SKILL.md「任务 → 最小路径速查」表逐条一致；
# 缺省能力不在此表 → call_examples 为空数组。
_CALL_EXAMPLES: dict[str, list[str]] = {
    "capabilities.read": ["GET /api/ai/v2/capabilities"],
    "investigations.create": [
        "POST /api/ai/v2/investigations",
        "GET /api/ai/v2/jobs/{id}",
        "GET /api/ai/v2/jobs/{id}/evidence?level=L1",
    ],
    "module_actions.ensure": [
        "POST /api/ai/v2/module-actions",
        "GET /api/ai/v2/jobs/{id}",
    ],
    "module_actions.send": [
        "POST /api/ai/v2/module-actions  # action=ensure → session_id",
        "POST /api/ai/v2/module-actions  # action=send, session_id + text 或 data_hex",
        "GET /api/ai/v2/jobs/{id}",
    ],
    "module_actions.stop": [
        "POST /api/ai/v2/module-actions  # action=ensure → session_id",
        "POST /api/ai/v2/module-actions  # action=stop",
        "GET /api/ai/v2/jobs/{id}",
    ],
    "verification_runs.create": [
        "POST /api/ai/v2/verification-runs",
        "GET /api/ai/v2/jobs/{id}",
        "GET /api/ai/v2/jobs/{id}/evidence?level=L1",
    ],
    "flash_jobs.create": [
        "POST /api/ai/v2/module-actions  # action=ensure, mapping_id={alias} → session_id",
        "POST /api/ai/v2/flash-jobs",
        "GET /api/ai/v2/jobs/{id}",
    ],
    "jobs.read": [
        "GET /api/ai/v2/jobs?limit=20  # job_id 丢了先列最近任务",
        "GET /api/ai/v2/jobs/{id}",
    ],
    "jobs.evidence.read": [
        "GET /api/ai/v2/jobs/{id}/evidence?level=L1",
        "GET /api/ai/v2/jobs/{id}/evidence?level=L2",
        "GET /api/ai/v2/jobs/{id}/evidence?level=L3&ref=listener:{index_id}:{frame_id}",
    ],
    "jobs.cancel": [
        "POST /api/ai/v2/jobs/{id}/cancel",
        "GET /api/ai/v2/jobs/{id}",
    ],
    # 模拟电表（REQS-0035）：AI 平面只读观察；写操作经工作台直连 HTTP 并
    # 以 GET /api/simeter/_meta 自描述小抄为准（省 token、防调用迷路）。
    "simeter_frames.read": [
        "GET /api/simeter/_meta",
        "GET /api/simeter/status",
        "GET /api/simeter/frames?limit=50",
        "POST /api/ai/v2/investigations  # body.observations=[{\"source\":\"simeter\"}]",
    ],
}


# 能力清单的「人话口径」：summary 一句话说清「做什么 + 什么时候用」，requires 列
# 真实前置条件（依赖的 session_id、烧录白名单、串口/闸门等）。只描述现有行为，
# 不引入新语义；缺省能力不在此表 → summary 为空、requires 为空数组。
_CAPABILITY_SUMMARIES: dict[str, str] = {
    "capabilities.read": "读本机当前可用的 v2 能力、资源别名与源健康度；开工前先看它确认能做什么、走哪条路，省得翻文档。",
    "flash_jobs.create": "把 iap 固件烧进 CCO/STA，异步受理后返回 job_id，终态要轮询 jobs.read。",
    "investigations.create": "对 module_log/listener/simcon/simeter 发起 1-3 路观察任务并 202 返回 job_id；要看实时帧、等事件或取证时用。",
    "jobs.read": "按 job_id 读任务信封与终态结论，wait_seconds 可阻塞等推进；拿到 202 后靠它轮询，别空转。",
    "jobs.cancel": "取消未终态的普通观察/模块任务（烧录与模拟集中器验证不可取消，会 409）；已终态则原样返回。",
    "jobs.evidence.read": "按 job_id 取 L1/L2/L3 证据（帧、日志、判定理由）；要回答「凭什么这么判」或给人看证据时用。",
    "module_actions.ensure": "按 mapping_id 开一个模块串口会话（幂等），后续 send/flash 都要用它返回的 session_id。",
    "module_actions.send": "向已开的模块会话发一条文本或 hex 指令并等回执；要在 CCO/STA 串口上发既定指令时用。",
    "module_actions.stop": "关闭 module_actions.ensure 开的模块会话并释放串口；任务收尾或换 mapping 前用，force 可强停。",
    "simeter_frames.read": "只读查看模拟电表的状态与近期收发帧；开表口、喂 645 请求、改表数据请走直连 /api/simeter/*。",
    "verification_runs.create": "向模拟集中器提交多步验证任务（task.steps 逐跳 afn/fn 收发）并 202 返回 job_id；验协议交互时用，靠 jobs.read 轮询。",
}

_CAPABILITY_REQUIRES: dict[str, list[str]] = {
    "capabilities.read": [],
    "flash_jobs.create": [
        "module_actions.ensure → session_id",
        "烧录白名单 firmware_roots",
        "同会话无并发烧录",
    ],
    "investigations.create": ["观测源在线（source_health.available）"],
    "jobs.read": ["create 类能力返回的 job_id"],
    "jobs.cancel": ["job_id（未终态才有实际取消效果）", "烧录/模拟集中器验证任务不可取消"],
    "jobs.evidence.read": ["job_id", "L3 需 ref=listener:{index_id}:{frame_id} 且属于该 job"],
    "module_actions.ensure": ["mapping_id（resource_aliases 里的逻辑资源别名）"],
    "module_actions.send": ["module_actions.ensure → session_id", "text 与 data_hex 二选一"],
    "module_actions.stop": ["module_actions.ensure → session_id"],
    "simeter_frames.read": ["simeter 在线（source_health.simeter.available）"],
    "verification_runs.create": [
        "simcon 空闲（GET /api/ai/v2/simcon/lease）",
        "task.steps 的 afn/fn 写法合法（非法 422）",
    ],
}


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
            capabilities.append(Capability(
                name=name, allowed=True, resources=resources,
                call_examples=_CALL_EXAMPLES.get(name, []),
                summary=_CAPABILITY_SUMMARIES.get(name, ""),
                requires=list(_CAPABILITY_REQUIRES.get(name, [])),
            ))

    source_health = [
        (SourceKind.MODULE_LOG, SourceHealth(available=control.module_service is not None,
                     reason=None if control.module_service is not None else "module_service_unavailable")),
        (SourceKind.LISTENER, SourceHealth(available=control.listener_service is not None,
                     reason=None if control.listener_service is not None else "listener_service_unavailable")),
        (SourceKind.SIMCON, SourceHealth(available=control.simcon_service is not None,
                     reason=None if control.simcon_service is not None else "simcon_service_unavailable")),
        (SourceKind.SIMETER, SourceHealth(available=control.simeter_service is not None,
                     reason=None if control.simeter_service is not None else "simeter_service_unavailable")),
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
        # 必须在 start_verification_run 之前校验：该服务先建父 job 再调 simcon，
        # 202 之后才在后台构帧失败只会留下一个 failed job（result=fail、
        # sent_hex=""），把参数写错伪装成设备不响应；这里非法直接 422、不建 job。
        payload = body.model_dump(mode="json", exclude_none=True)
        task = payload.get("task")
        try:
            validate_steps(task.get("steps") if isinstance(task, dict) else None)
        except ScenarioParamError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        flash_authorization = resolve_flash_authorization(
            payload, grant, local_full=context.zone == "local_full",
        )
        payload["bin_path"] = flash_authorization.bin_path
        try:
            envelope = capability_service.start_flash_job(payload, context=context)
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
        record_flash_authorization(
            control, flash_authorization=flash_authorization, actor=context.actor,
            resource=resource, operation_id=capability_service._operation_id(envelope.job_id),
        )
        return envelope

    @router.get("/jobs")
    def list_jobs(request: Request,
                  kind: list[str] = Query(default=[]),
                  state: str | None = Query(None),
                  since: str | None = Query(None),
                  limit: int = Query(20, ge=1),
                  authorization: str | None = Header(None)) -> dict[str, object]:
        """列出最近的 job 摘要（created_at 倒序），job_id 丢了也能找回最近任务。

        - kind：可重复（?kind=flash_job&kind=verification_run）或逗号分隔
          （?kind=flash_job,verification_run），两种写法可混用；不传不过滤。
        - state：按操作状态精确过滤（created/running/waiting/succeeded/error/...）。
        - since：ISO 时间，只返回 created_at >= since；非法值 422，不落 500。
        - limit：默认 20，超过 100 按 100 处理；响应里的 limit 是实际生效值。
        每条摘要只含元数据（不含 payload/result），另带派生 job_id 便于直接
        调用 GET /api/ai/v2/jobs/{job_id}。
        """
        _context(request, authorization, "jobs.read")
        kinds = [
            part.strip()
            for value in kind for part in str(value).split(",")
            if part.strip()
        ]
        states = [state.strip()] if state and state.strip() else None
        try:
            operations = control.store.list_operations(
                kinds=kinds or None, states=states, since=since, limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        jobs = [
            {**item, "job_id": capability_service._job_id(str(item.get("operation_id") or ""))}
            for item in operations
        ]
        return {"jobs": jobs, "count": len(jobs), "limit": min(limit, LIST_LIMIT_MAX)}

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

    @router.get("/simcon/lease")
    def read_simcon_lease(request: Request,
                          authorization: str | None = Header(None)) -> dict[str, object]:
        """模拟集中器统一闸门的只读快照：谁在用、用的是什么动作、已经用了多久。

        - 空闲 {"held": false, "holder": null}；占用时 holder 带
          actor/kind/operation_id/started_at/elapsed_seconds/thread。
        - 不取闸门：查一下不会阻塞请求线程，也不改变在跑任务的准入状态。
        - policy 固定 no_queue_reject：撞门就直接 409，本版不排队。
        鉴权与其它 v2 只读端点一致：本机 loopback 免 token，局域网需 simcon:read。
        """
        context = resolve_access_context(request, local_full_enabled=local_full_enabled)
        if context.zone.value != "local_full":
            _bearer_grant(authorization, auth_store, scope="simcon:read")
        return control.simcon_lease()

    return router
