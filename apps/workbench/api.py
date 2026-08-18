"""workbench.api —— 编排路由（/api/run、/api/scenarios、/api/compare、/api/feedback）。

无 UI 依赖；挂载到统一后端 workbench.app.create_workbench_app()。
CLI / REST / AI agent 三端复用（FR-6.2 编排层）。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from .orchestration.models import Report, RunInput
from .orchestration.runner import RunExecutor
from .orchestration.scenarios import load_scenario, load_scenarios, validate_scenario
from .orchestration.store import RunStore

router = APIRouter(prefix="/api")


def _executor() -> RunExecutor:
    # 延迟创建共享 executor + store（单例化，避免多请求并发开多个 sqlite 连接、
    # 以及 cancel/submit 落在不同实例导致取消事件丢失）
    if not hasattr(_executor, "_store"):
        _executor._store = RunStore()
    if not hasattr(_executor, "_exec"):
        _executor._exec = RunExecutor(_executor._store)
    return _executor._exec


def _store() -> RunStore:
    return _executor().store


@router.get("/scenarios")
async def list_scenarios():
    """列出场景模板。"""
    return load_scenarios()


@router.get("/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str):
    s = load_scenario(scenario_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"场景不存在：{scenario_id}")
    errors = validate_scenario(s)
    if errors:
        raise HTTPException(status_code=422, detail=f"场景模板非法：{'；'.join(errors)}")
    return s


@router.post("/run")
async def create_run(run_input: RunInput):
    """创建并异步执行一个验证批次（全链路：烧录→监控→激励→比对→反馈→报告）。

    异步执行：立刻返回（状态 running），调用方轮询 GET /api/run/{run_id}
    获取进度，可经 POST /api/run/{run_id}/cancel 取消（任务4 取消 Run）。
    """
    try:
        run = _executor().submit(run_input)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Run 启动失败：{exc}") from exc
    return run.model_dump()


@router.post("/run/{run_id}/cancel")
async def cancel_run(run_id: str):
    """取消一个正在执行的 Run（协作式取消，落 CANCELLED 终态）。"""
    run = _store().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run 不存在：{run_id}")
    ok = _executor().cancel(run_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"Run 不可取消（不存在或已结束）：{run_id}")
    return {"run_id": run_id, "status": "cancelling"}


@router.get("/run/{run_id}")
async def get_run(run_id: str):
    run = _store().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run 不存在：{run_id}")
    return run


@router.get("/run/{run_id}/report")
async def get_report(run_id: str):
    report = _store().get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"报告不存在：{run_id}")
    return report


@router.get("/run/{run_id}/artifacts")
async def list_artifacts(run_id: str):
    """列出 Run 的 Artifact manifest（D-03 审计链：结构化清单）。"""
    report = _store().get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"报告不存在：{run_id}")
    return report.get("artifacts") or []


@router.get("/run/{run_id}/artifacts/{artifact_id}")
async def download_artifact(run_id: str, artifact_id: str):
    """按逻辑 Artifact ID 下载产物（D-03：路径越界防护，对外只暴露逻辑 ID）。

    - 未登记的逻辑 ID → 404
    - 真实路径不存在/为目录 → 404（ArtifactPathUnsafe）
    """
    from fastapi.responses import FileResponse

    from .orchestration.artifacts import (
        ArtifactPathUnsafe,
        find_artifact,
        resolve_artifact_path,
    )

    report = _store().get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"报告不存在：{run_id}")
    item = find_artifact(report, artifact_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Artifact 不存在：{artifact_id}")
    try:
        path = resolve_artifact_path(item)
    except ArtifactPathUnsafe as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(str(path), filename=item.get("name") or path.name)


@router.get("/runs")
async def list_runs(limit: int = Query(50, ge=1, le=200)):
    return _store().list_runs(limit)


@router.post("/compare")
async def compare(body: dict):
    """直接比对：期望流程 vs 实际事件流（不落 Run）。"""
    from .orchestration.compare import compare_flow

    expected = body.get("expected_flow", [])
    events = body.get("events", [])
    return compare_flow(expected, events).model_dump()


@router.post("/feedback")
async def feedback(body: dict):
    """直接归因：根据比对结论 + 激励结论生成反馈（不落 Run）。"""
    from .orchestration.feedback import build_feedback
    from .orchestration.models import FlowCompare

    fc = FlowCompare(**body.get("flow_compare", {}))
    return build_feedback(fc, body.get("simcon_summary"), body.get("loghooks_drift", False))


@router.get("/health")
async def health():
    return {"status": "ok", "app": "workbench"}
