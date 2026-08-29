"""workbench.app —— 统一 FastAPI 工厂（FR-6 落地，方案①：前端合并 + 后端代理）。

合并策略（ADR-18，替代原"响应重写中间件"方案）：
- listener / module_log 后端包**保持独立**（可各自独立运行，ADR-1/10/13 解耦
  哲学不推翻）。
- workbench 统一后端通过 **ASGI 前缀代理**把子应用挂到
  `/api/listener/*` 与 `/api/module-serial/*`：请求
  `/api/listener/logs/status` → 子应用内部路由 `/api/logs/status`。
  纯后端路径转换，稳定可靠，不再拦截/改写 HTML 响应。
- 前端页面**物理复制**到 workbench/static/pages/{listener,module-serial}/，
  JS 内 `/api/` 统一改为 `/api/listener/`、`/api/module-serial/`；
  静态资源 `/static/` 改为 `/static/pages/{pkg}/`（见 static/pages/）。
- 编排路由（/api/run 等）保持本模块 api.router。

listener 依赖 C# DLL/pythonnet，惰性导入 + 挂载失败降级（页签显示"不可用"，
不拖垮整体，见详细设计 §9 风险表）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router as orchestration_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 统一入口端口（详细设计 §3.2：8790）
PORT = 8790


def _workbench_static_dir() -> Path:
    """workbench 自身 static 数据目录。

    frozen：PyInstaller 将 apps/workbench/static 打进 _internal/static；
    源码：apps/workbench/static（__file__ 真实路径）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "static"  # type: ignore[attr-defined]
    return STATIC_DIR


def _subapp_static_dir(pkg: str) -> Path:
    """子应用 static 数据目录（打包时打进 _internal/{pkg}/static）。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / pkg / "static"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / pkg / "static"


def _prepare_subapp_static(subapp_module) -> None:
    """frozen 下修正子应用模块的 STATIC_DIR，指向其独立 static 数据目录。

    子应用独立 exe 时 static 平铺 _MEIPASS/static；被 workbench 挂载后
    _MEIPASS/static 已被 workbench 占用（同名冲突），故指向 {pkg}/static。
    源码模式 STATIC_DIR 本就指向包内 static，无需修改。
    """
    if not getattr(sys, "frozen", False):
        return
    pkg = subapp_module.__name__.split(".")[0]
    subapp_module.STATIC_DIR = _subapp_static_dir(pkg)


class SimconAIService:
    """AI 控制面 ↔ 模拟集中器执行核心的进程内桥（层间不走 HTTP 回调）。

    包装 module_log 子应用提升上来的 simcon 访问器；串口占用等 RuntimeError
    统一译成 SessionBusy（AI 路由映射 409），其余异常原样上抛。
    """

    def __init__(self, *, run_verify, run_step, frames, session, open_session, close_io):
        self._run_verify = run_verify
        self._run_step = run_step
        self._frames = frames
        self._session = session
        self._open_session = open_session
        self._close_io = close_io

    def verify(self, task: dict) -> dict:
        return self._run_verify(task)

    def step(self, payload: dict) -> dict:
        from .ai_operations import SessionBusy
        try:
            return self._run_step(payload)
        except RuntimeError as exc:
            raise SessionBusy(str(exc)) from exc

    def open(self, spec: dict | None = None) -> dict:
        from .ai_operations import SessionBusy
        try:
            return self._open_session(spec)
        except RuntimeError as exc:
            raise SessionBusy(str(exc)) from exc

    def close(self) -> dict:
        self._close_io()
        return {"open": False}

    def frames(self, **filters) -> dict:
        return self._frames(**filters)

    def session(self) -> dict:
        return self._session()


class _PrefixProxy:
    """ASGI 前缀代理：把 {api_prefix}{sub_path} 转发给子应用。

    Starlette 6.x mount 设置 scope["root_path"]=挂载前缀，但 path 不剥离，
    子应用 FastAPI 用 path - root_path 匹配路由。本代理主动剥掉
    api_prefix 并把 root_path 清空，使子应用收到与独立运行一致的
    path（/api/...），其内部路由原样命中。
    例：请求 /api/module-serial/fs/list → 代理剥前缀 → 子应用收到
    /api/fs/list（200）。
    """

    def __init__(self, sub_app, api_prefix: str, sub_root: str):
        self.sub_app = sub_app
        self.api_prefix = api_prefix
        self.sub_root = sub_root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.sub_app(scope, receive, send)
            return
        path = scope.get("path", "")
        prefix = self.api_prefix
        if path == prefix or path.startswith(prefix + "/"):
            new_path = self.sub_root + path[len(prefix):]  # /api + 剩余
            scope = dict(scope)
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode("utf-8")
            scope["root_path"] = ""
        await self.sub_app(scope, receive, send)


def _mount_proxied(app: FastAPI, name: str, sub_app, api_prefix: str, sub_root: str = "/api") -> None:
    """把子应用以 ASGI 前缀代理挂到 app（{api_prefix}/* → 子应用 {sub_root}/*）。

    sub_root 默认 "/api"：适用于子应用路由无业务前缀（如 listener 的 /api/*）。
    对自带完整前缀的子应用（如 module_log 的 /api/module-serial/*），
    传 sub_root=api_prefix 以透传，避免代理剥前缀导致 404。
    """
    proxy = _PrefixProxy(sub_app, api_prefix, sub_root)
    app.mount(api_prefix, proxy, name=f"proxy-{name}")


def create_workbench_app(
    listener_factory=None,
    module_log_factory=None,
    mount_listener: bool = True,
    ai_control_service=None,
    ai_auth_store=None,
    ai_admin_key: str | None = None,
    ai_storage_dir: Path | str | None = None,
) -> FastAPI:
    """创建统一工作台应用。

    参数可注入替代工厂（测试用）；listener 挂载失败自动降级（mount_listener=False）。
    """
    app = FastAPI(
        title="AI 闭环研发验证工作台",
        description="统一集成程序：侦听台 / 模块日志 / 模拟集中器 / 验证工作台",
        version="0.1.0",
    )
    # 统一工作台内的所有后端串口服务使用同一资源登记表。UI/AI 不持有
    # 物理句柄，只读取这些服务暴露的状态、日志和索引。
    from shared.serial_resources import SerialResourceRegistry

    app.state.serial_resource_registry = SerialResourceRegistry()

    # D-04 统一错误响应（code/message/details/request_id + 兼容 detail）
    from .errors import register_error_handlers

    register_error_handlers(app)

    _ml_sub = None
    _listener_sub = None
    _sub = None  # listener 挂载失败时 AI 控制面注入仍需该名字可安全访问

    # ---- 1. 挂载 module_log（含内部 simcon 子应用）----
    try:
        if module_log_factory is None:
            import module_log.app as _ml_mod

            _prepare_subapp_static(_ml_mod)
            module_log_factory = _ml_mod.create_app
            _ml_sub = module_log_factory(
                resource_registry=app.state.serial_resource_registry,
            )
        else:
            _ml_sub = module_log_factory()
        _mount_proxied(app, "module-serial", _ml_sub, "/api/module-serial", sub_root="/api/module-serial")
        # module_log 子应用还自带 /api/fs、/api/loghooks 路由，并在内部把
        # simcon 挂在 /api/simcon；统一工作台下按透传模式补齐这三组前缀，
        # 使 module-serial 页面的 fs/loghooks/simcon 请求在 iframe 内可达。
        _mount_proxied(app, "module-serial-fs", _ml_sub, "/api/fs", sub_root="/api/fs")
        _mount_proxied(app, "module-serial-loghooks", _ml_sub, "/api/loghooks", sub_root="/api/loghooks")
        _mount_proxied(app, "module-serial-simcon", _ml_sub, "/api/simcon", sub_root="/api/simcon")
        app.state.module_serial_service = getattr(
            getattr(_ml_sub, "state", None), "module_serial_service", None,
        )
        module_service = app.state.module_serial_service
        set_registry = getattr(module_service, "set_resource_registry", None)
        if callable(set_registry):
            set_registry(app.state.serial_resource_registry)
        if getattr(_ml_sub, "state", None) is not None:
            _ml_sub.state.serial_resource_registry = app.state.serial_resource_registry
        app.state.module_log_mounted = True
    except Exception as exc:  # pragma: no cover - 依赖缺失降级
        app.state.module_log_mounted = False
        app.state.module_log_error = str(exc)

    # ---- 2. 挂载 listener（依赖 C# DLL，失败降级）----
    if mount_listener:
        try:
            if listener_factory is None:
                import listener.app as _ls_app

                _prepare_subapp_static(_ls_app)
                _sub = _ls_app.create_app(
                    _ls_app.parser_service,
                    _ls_app.log_file_service,
                    _ls_app.serial_capture_service,
                )
            else:
                _sub = listener_factory()
            _listener_sub = _sub
            _mount_proxied(app, "listener", _sub, "/api/listener")
            app.state.listener_service = getattr(
                getattr(_sub, "state", None), "serial_service", None,
            )
            listener_service = app.state.listener_service
            set_registry = getattr(listener_service, "set_resource_registry", None)
            if callable(set_registry):
                set_registry(app.state.serial_resource_registry)
            if getattr(_sub, "state", None) is not None:
                _sub.state.serial_resource_registry = app.state.serial_resource_registry
            app.state.listener_log_service = getattr(
                getattr(_sub, "state", None), "log_service", None,
            )
            app.state.listener_mounted = True
        except Exception as exc:  # pragma: no cover
            app.state.listener_mounted = False
            app.state.listener_error = str(exc)
    else:
        app.state.listener_mounted = False

    # ---- 3. AI 控制面：仅复用已挂载的后端服务，不经 HTTP 回调 ----
    from .ai_api import create_ai_router
    from .ai_auth import AuthorizationStore
    from .ai_operations import AIControlService
    from .ai_store import OperationStore

    configured_storage_dir = ai_storage_dir or os.environ.get("WORKBENCH_AI_STORAGE_DIR")
    storage_dir = Path(configured_storage_dir) if configured_storage_dir else (
        (Path(sys.executable).resolve().parent / "runtime" / "ai-control")
        if getattr(sys, "frozen", False)
        else (Path(__file__).resolve().parent / "runtime" / "ai-control")
    )
    app.state.ai_auth_store = ai_auth_store or AuthorizationStore(
        storage_path=storage_dir / "grants.json",
    )
    # 模拟集中器 AI 桥：进程内包装 simcon 执行核心（缺失则 AI simcon 接口 503）
    _simcon_accessors = {
        name: getattr(_ml_sub.state, name, None)
        for name in ("simcon_run_verify", "simcon_run_step", "simcon_frames",
                     "simcon_session", "simcon_open", "simcon_close_io")
    }
    simcon_service = SimconAIService(
        run_verify=_simcon_accessors["simcon_run_verify"],
        run_step=_simcon_accessors["simcon_run_step"],
        frames=_simcon_accessors["simcon_frames"],
        session=_simcon_accessors["simcon_session"],
        open_session=_simcon_accessors["simcon_open"],
        close_io=_simcon_accessors["simcon_close_io"],
    ) if all(_simcon_accessors.values()) else None
    app.state.ai_control_service = ai_control_service or AIControlService(
        module_service=getattr(app.state, "module_serial_service", None),
        listener_service=getattr(app.state, "listener_service", None),
        log_service=getattr(app.state, "listener_log_service", None),
        resource_registry=app.state.serial_resource_registry,
        simcon_service=simcon_service,
        # 侦听台通信流追踪执行核心（需求 0009）：0008 层间进程内注入模式
        trace_service=getattr(getattr(_sub, "state", None), "trace_service", None),
        store=OperationStore(storage_path=storage_dir / "operations.json"),
    )
    app.state.ai_admin_key_configured = bool(ai_admin_key or os.environ.get("WORKBENCH_AI_ADMIN_KEY"))
    app.include_router(create_ai_router(
        app.state.ai_control_service, app.state.ai_auth_store,
        admin_key=ai_admin_key or os.environ.get("WORKBENCH_AI_ADMIN_KEY"),
    ))

    # ---- 3.5 串口 Profile（P4）：GET/PUT 只保存 + POST apply 一键应用 ----
    from .serial_profile_api import _profile_runtime_dir as _prof_dir
    from shared.serial_profile import SerialProfileStore as _ProfileStore
    from .serial_profile_api import create_serial_profile_router
    from .serial_profile_applier import SerialProfileApplier, SimconProfileAdapter

    _profile_store = _ProfileStore(runtime_dir=_prof_dir())
    _simcon_open = getattr(getattr(_ml_sub, "state", None), "simcon_open_io", None)
    _simcon_close = getattr(getattr(_ml_sub, "state", None), "simcon_close_io", None)
    _simcon_adapter = None
    if _simcon_open is not None and _simcon_close is not None:
        try:
            from sim_concentrator.serial_io import resolve_serial_config
            _simcon_adapter = SimconProfileAdapter(
                open_io=_simcon_open, close_io=_simcon_close, resolve=resolve_serial_config,
            )
        except Exception:  # noqa: BLE001 - 依赖缺失则 simcon 槽跳过
            _simcon_adapter = None
    app.state.serial_profile_store = _profile_store
    app.state.serial_profile_applier = SerialProfileApplier(
        module_service=getattr(app.state, "module_serial_service", None),
        listener_service=getattr(app.state, "listener_service", None),
        simcon_service=_simcon_adapter,
        profile_store=_profile_store,
    )
    app.include_router(create_serial_profile_router(
        profile_store=_profile_store,
        applier=app.state.serial_profile_applier,
    ))

    # ---- 4. 编排路由 ----
    app.include_router(orchestration_router)

    # ---- 4. 静态外壳（页签式 SPA）----
    _wb_static = _workbench_static_dir()
    if _wb_static.exists():
        app.mount("/static", StaticFiles(directory=_wb_static), name="static")

    @app.get("/")
    async def index():
        from fastapi.responses import FileResponse

        idx = _wb_static / "index.html"
        if idx.exists():
            return FileResponse(idx)
        return {"app": "workbench", "static": "missing"}

    @app.get("/api/platform-version")
    async def platform_version():
        return {
            "app": "workbench",
            "version": "0.1.0",
            "module_log_mounted": getattr(app.state, "module_log_mounted", False),
            "listener_mounted": getattr(app.state, "listener_mounted", False),
        }

    return app


# ---------- 模块级装配（供 uvicorn "workbench.app:app" / PyInstaller 引用）----------
app = create_workbench_app()
