"""模块日志/烧录串口独立应用（端口 8766）。

与侦听台（listener，8765）完全解耦：独立 FastAPI 应用、独立端口、
独立页面（/module-serial）、独立串口服务（ModuleSerialService）。
支持双通道（cco/sta）同时监控与独立烧录。
复用 shared.infra 的通用基础设施（文件选择/盘符等）。

启动：python -m module_log.run
"""

import base64
import binascii
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from shared import infra
from shared.web_static import NoCacheHTMLStaticFiles
from shared.serial_resources import SerialResourceRegistry
from module_log.module_serial_service import CHANNELS, ModuleSerialService

def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _base_dir() -> Path:
    """打包数据根：frozen 下为 PyInstaller _MEIPASS，否则为 module_log 包目录。"""
    if _is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _runtime_dir() -> Path:
    """运行时数据目录：frozen 下为 exe 同目录 runtime/，否则为包内 runtime/。"""
    if _is_frozen():
        return Path(sys.executable).resolve().parent / "runtime"
    return _base_dir() / "runtime"


def _log_dir() -> Path:
    """模块日志根目录：frozen 下为 exe 同目录 LOG/模块，否则为项目根 data/logs/模块。"""
    if _is_frozen():
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent.parent
    d = root / "data" / "logs" / "模块"
    d.mkdir(parents=True, exist_ok=True)
    return d


BASE_DIR = _base_dir()
STATIC_DIR = BASE_DIR / "static"
RUNTIME_DIR = _runtime_dir()
LAST_PATH_FILE = RUNTIME_DIR / "last_path.txt"

def create_app(module_serial_service=None, resource_registry: SerialResourceRegistry | None = None) -> FastAPI:
    # workbench 统一挂载时以 create_app() 无参调用：此时默认创建真实串口服务，
    # 否则 /api/module-serial/* 全部 503（模块串口服务未启用）。
    # 测试注入自定义 service 或显式传 None（禁用串口）仍受支持。
    resource_registry = resource_registry or SerialResourceRegistry()
    if module_serial_service is None:
        module_serial_service = ModuleSerialService(
            log_dir=_log_dir(), resource_registry=resource_registry,
        )
    else:
        set_registry = getattr(module_serial_service, "set_resource_registry", None)
        if callable(set_registry):
            set_registry(resource_registry)
    app = FastAPI(title="模块日志 / 烧录串口")
    app.state.module_serial_service = module_serial_service
    app.state.serial_resource_registry = resource_registry
    app.mount("/static", NoCacheHTMLStaticFiles(directory=STATIC_DIR), name="static")

    # 模拟集中器验证工具（第三页签后端）：挂载独立子应用到 /api/simcon
    # 子应用路由用相对路径（prefix=""），避免 mount 前缀 + 路由前缀双前缀
    from sim_concentrator.api import create_simcon_app
    _simcon_sub = create_simcon_app(prefix="", resource_registry=resource_registry)
    app.mount(
        "/api/simcon",
        _simcon_sub,
        name="simcon",
    )
    # P4：把 simcon 的 open/close 服务函数提升到 module_log state，供统一工作台
    # SerialProfileApplier 经适配器注入（不经 HTTP 回调）。
    app.state.simcon_open_io = getattr(_simcon_sub.state, "simcon_open_io", None)
    app.state.simcon_close_io = getattr(_simcon_sub.state, "simcon_close_io", None)
    # 帧日志/AI 单步执行核心：同模式提升，供 workbench AI 控制面进程内注入。
    for _name in ("simcon_run_verify", "simcon_run_step", "simcon_frames",
                  "simcon_session", "simcon_open",
                  "simcon_store_snapshots", "simcon_store_snapshot_items",
                  "simcon_store_events"):
        setattr(app.state, _name, getattr(_simcon_sub.state, _name, None))

    @app.get("/module-serial")
    def module_serial_page():
        # 页面内嵌默认任务 JSON 等易变内容，禁用浏览器缓存（与 /static 挂载一致）
        return FileResponse(STATIC_DIR / "module-serial.html",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/version")
    def version():
        return {
            "app": "module-serial",
            "module_serial_api_revision": 2,
            "channels": list(CHANNELS),
        }

    # workbench 挂载时经代理透传，同一版本信息也暴露在 /api/module-serial/ 命名空间下
    @app.get("/api/module-serial/version")
    def module_serial_version():
        return {
            "app": "module-serial",
            "module_serial_api_revision": 2,
            "channels": list(CHANNELS),
        }

    # ---- 模块串口 ----
    @app.get("/api/module-serial/ports")
    def module_serial_ports():
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        return {
            "ports": module_serial_service.list_available_ports(),
            "port_details": module_serial_service.list_available_port_details(),
            "mapping_error": module_serial_service.mapping_error(),
        }


    # ---- 动态会话 API（新前端与 AI 控制面使用）----
    class ModuleSessionCreateRequest(BaseModel):
        title: str = Field("", max_length=128)
        module: str = Field("cco", pattern=r"^(cco|sta)$")

    class ModuleSessionUpdateRequest(BaseModel):
        title: str | None = Field(None, max_length=128)
        module: str | None = Field(None, pattern=r"^(cco|sta)$")

    class ModuleSessionStartRequest(BaseModel):
        port: str = Field(..., min_length=1, max_length=256)
        baudrate: int = Field(115200, ge=300, le=921600)
        bytesize: int = Field(8, ge=5, le=8)
        parity: str = Field("N", min_length=1, max_length=1)
        stopbits: int = Field(1, ge=1, le=2)

    class ModuleSessionWriteRequest(BaseModel):
        data: str = Field(..., min_length=1, max_length=4096)

    class ModuleSessionWriteTextRequest(BaseModel):
        text: str = Field("", max_length=4096)
        append_newline: bool = True

    class ModuleSessionBaudrateRequest(BaseModel):
        baudrate: int = Field(..., ge=300, le=921600)

    class ModuleSessionFlashRequest(BaseModel):
        bin_path: str = Field(..., min_length=1, max_length=2048)
        slot: int = Field(0, ge=0, le=1)
        baud_plan: list[int] | None = None
        no_reboot_after: bool = False

    def _session_service_or_503():
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        return module_serial_service

    def _session_call(action):
        try:
            return action()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/module-serial/sessions")
    def module_sessions():
        service = _session_service_or_503()
        return {"sessions": service.list_sessions()}

    @app.post("/api/module-serial/sessions", status_code=201)
    def module_session_create(request: ModuleSessionCreateRequest):
        service = _session_service_or_503()
        return _session_call(lambda: service.create_session(request.title, request.module))

    @app.get("/api/module-serial/sessions/{session_id}")
    def module_session_get(session_id: str):
        service = _session_service_or_503()
        return _session_call(lambda: service.get_session(session_id))

    @app.patch("/api/module-serial/sessions/{session_id}")
    def module_session_update(session_id: str, request: ModuleSessionUpdateRequest):
        service = _session_service_or_503()
        if request.title is None and request.module is None:
            raise HTTPException(status_code=422, detail="至少需要提供 title 或 module")
        return _session_call(
            lambda: service.update_session(session_id, title=request.title, module=request.module)
        )

    @app.delete("/api/module-serial/sessions/{session_id}")
    def module_session_delete(session_id: str):
        service = _session_service_or_503()
        return _session_call(lambda: service.delete_session(session_id))

    @app.post("/api/module-serial/sessions/{session_id}/start", status_code=202)
    def module_session_start(session_id: str, request: ModuleSessionStartRequest):
        service = _session_service_or_503()
        return _session_call(
            lambda: service.start_session(
                session_id, request.port, request.baudrate, request.bytesize,
                request.parity, request.stopbits,
            )
        )

    @app.post("/api/module-serial/sessions/{session_id}/stop")
    def module_session_stop(session_id: str):
        service = _session_service_or_503()
        return _session_call(lambda: service.stop_session(session_id))

    @app.post("/api/module-serial/sessions/{session_id}/write")
    def module_session_write(session_id: str, request: ModuleSessionWriteRequest):
        service = _session_service_or_503()
        return _session_call(lambda: service.write_session(session_id, request.data))

    @app.post("/api/module-serial/sessions/{session_id}/write-text")
    def module_session_write_text(session_id: str, request: ModuleSessionWriteTextRequest):
        service = _session_service_or_503()
        return _session_call(
            lambda: service.write_text_session(
                session_id, request.text, append_newline=request.append_newline,
            )
        )

    @app.post("/api/module-serial/sessions/{session_id}/baudrate")
    def module_session_baudrate(session_id: str, request: ModuleSessionBaudrateRequest):
        service = _session_service_or_503()
        return _session_call(lambda: service.set_session_baudrate(session_id, request.baudrate))

    @app.post("/api/module-serial/sessions/{session_id}/flash")
    def module_session_flash(session_id: str, request: ModuleSessionFlashRequest):
        service = _session_service_or_503()
        try:
            return service.flash_session(
                session_id, request.bin_path, request.slot,
                request.baud_plan, request.no_reboot_after,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"会话不存在：{exc.args[0]}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/module-serial/sessions/{session_id}/logs")
    def module_session_logs(session_id: str, after: int = Query(-1, ge=-1)):
        service = _session_service_or_503()
        return _session_call(lambda: service.logs_session(session_id, after=after))

    @app.get("/api/module-serial/status")
    def module_serial_status():
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        return module_serial_service.status()
    class ModuleSerialStartRequest(BaseModel):
        port: str = Field(..., min_length=1, max_length=64)
        baudrate: int = Field(115200, ge=300, le=921600)
        bytesize: int = Field(8, ge=5, le=8)
        parity: str = Field("N", min_length=1, max_length=1)
        stopbits: int = Field(1, ge=1, le=2)
        log_type: str = Field("cco", pattern=r"^(cco|sta)$")  # 兼容旧字段
        channel: str | None = Field(None, pattern=r"^(cco|sta)$")  # 未传时兼容 log_type

    @app.post("/api/module-serial/start", status_code=202)
    def module_serial_start(request: ModuleSerialStartRequest):
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        try:
            return module_serial_service.start(
                port=request.port, baudrate=request.baudrate,
                bytesize=request.bytesize, parity=request.parity,
                stopbits=request.stopbits,
                log_type=request.log_type, channel=request.channel,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"模块串口启动失败：{exc}") from exc

    class ModuleSerialStopRequest(BaseModel):
        channel: str = Field("cco", pattern=r"^(cco|sta)$")

    @app.post("/api/module-serial/stop")
    def module_serial_stop(request: ModuleSerialStopRequest | None = None):
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        channel = request.channel if request is not None else "cco"
        return module_serial_service.stop(channel=channel)
    class ModuleSerialWriteRequest(BaseModel):
        data: str = Field(..., min_length=1, max_length=4096)
        channel: str = Field("cco", pattern=r"^(cco|sta)$")

    @app.post("/api/module-serial/write")
    def module_serial_write(request: ModuleSerialWriteRequest):
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        try:
            return module_serial_service.write(request.data, channel=request.channel)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    class ModuleSerialWriteTextRequest(BaseModel):
        text: str = Field("", max_length=4096)  # 允许空：空输入按换行也是发送一个换行
        channel: str = Field("cco", pattern=r"^(cco|sta)$")
        append_newline: bool = True  # 自动携带换行，默认开

    @app.post("/api/module-serial/write_text")
    def module_serial_write_text(request: ModuleSerialWriteTextRequest):
        """发送框下发：向指定通道发送文本（换行即发送；append_newline 默认补 \\n）。"""
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        try:
            return module_serial_service.write_text(
                request.text, channel=request.channel, append_newline=request.append_newline,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    class ModuleSerialBaudRequest(BaseModel):
        baudrate: int = Field(..., ge=300, le=921600)
        channel: str = Field("cco", pattern=r"^(cco|sta)$")

    @app.post("/api/module-serial/baudrate")
    def module_serial_baudrate(request: ModuleSerialBaudRequest):
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        try:
            return module_serial_service.set_baudrate(request.baudrate, channel=request.channel)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    class ModuleSerialFlashRequest(BaseModel):
        bin_path: str = Field(..., min_length=1, max_length=2048)
        slot: int = Field(0, ge=0, le=1)
        baud_plan: list[int] | None = None
        no_reboot_after: bool = False
        channel: str = Field("cco", pattern=r"^(cco|sta)$")

    @app.post("/api/module-serial/flash")
    def module_serial_flash(request: ModuleSerialFlashRequest):
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        try:
            return module_serial_service.flash(
                request.bin_path, slot=request.slot,
                baud_plan=request.baud_plan,
                no_reboot_after=request.no_reboot_after,
                channel=request.channel,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/module-serial/logs")
    def module_serial_logs(after: int = Query(-1, ge=-1),
                           channel: str = Query("cco", pattern=r"^(cco|sta)$")):
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        return module_serial_service.logs(after=after, channel=channel)
    class ModuleSerialUploadRequest(BaseModel):
        name: str = Field(..., min_length=1, max_length=255)
        base64: str = Field(..., min_length=1, max_length=10485760)  # 10 MB 上限

    @app.post("/api/module-serial/upload")
    def module_serial_upload(request: ModuleSerialUploadRequest):
        try:
            data = base64.b64decode(request.base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="base64 解码失败") from exc
        if not data:
            raise HTTPException(status_code=422, detail="空文件")
        uploads = _log_dir().parent / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        safe_name = os.path.basename(request.name) or "fw.bin"
        path = uploads / safe_name
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"固件保存失败：{exc}") from exc
        return {"path": str(path)}

    # ---- 固件选择（复用 shared.infra，仅 pick 选固件路径）----
    @app.get("/api/fs/roots")
    @app.get("/api/module-serial/fs/roots")
    def fs_roots():
        drives = infra.windows_drives()
        if drives:
            return {"roots": drives}
        home = str(_base_dir().parent)
        return {"roots": [{"name": home, "path": home}]}

    @app.get("/api/fs/list")
    @app.get("/api/module-serial/fs/list")
    def fs_list(path: str = Query("", max_length=1024)):
        return infra.list_directory(path)

    # ---- loghooks 对照解析 ----
    @app.get("/api/loghooks/scan")
    @app.get("/api/module-serial/loghooks/scan")
    def loghooks_scan(path: str = Query(..., max_length=2048),
                      module: str = Query("", pattern=r"^(cco|sta|)$"),
                      limit: int = Query(2000, ge=1, le=20000)):
        """扫描日志文件/目录，返回事件 + 原始日志行绑定。"""
        from module_log import loghooks_api
        from pathlib import Path
        res = loghooks_api.scan_log_file(Path(path), module or None, limit=limit)
        if "error" in res:
            raise HTTPException(status_code=404, detail=res["error"])
        return res

    @app.get("/api/loghooks/realtime")
    @app.get("/api/module-serial/loghooks/realtime")
    def loghooks_realtime(
        session_id: str = Query("", max_length=128),
        channel: str = Query("cco", pattern=r"^(cco|sta)$"),
        limit: int = Query(2000, ge=1, le=20000),
    ):
        """扫描实时串口内存日志，返回事件与具体会话/原始行的绑定。

        session_id 是动态会话的首选资源标识；保留 channel 仅为
        旧 CCO/STA 双通道客户端兼容。两种入口最终都只读取同一服务实例。
        """
        if module_serial_service is None:
            raise HTTPException(status_code=503, detail="模块串口服务未启用")
        from module_log import loghooks_api

        if session_id:
            try:
                session = module_serial_service.get_session(session_id)
                logs = module_serial_service.logs_session(session_id, after=-1)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"实时会话不存在：{session_id}") from exc
            module = session["module"]
            lines = logs.get("lines", []) if isinstance(logs, dict) else []
            result = loghooks_api.scan_realtime(lines, module=module, limit=limit)
            result["session_id"] = session_id
            result["source"] = {
                "kind": "module_serial_session",
                "session_id": session_id,
                "title": session["title"],
                "module": module,
                "port": session["port"],
                "port_identity": session["port_identity"],
                "log_file": session["log_file"],
            }
            return result

        logs = module_serial_service.logs(after=-1, channel=channel)
        lines = logs.get("lines", []) if isinstance(logs, dict) else []
        result = loghooks_api.scan_realtime(lines, module=channel, limit=limit)
        result["source"] = {
            "kind": "legacy_channel",
            "channel": channel,
        }
        return result

    @app.get("/api/loghooks/sources")
    def loghooks_sources():
        """列出模块日志根目录下可扫描的日志文件（按模块分组）。"""
        from module_log import loghooks_api
        root = _log_dir()
        groups = {"cco": [], "sta": []}
        if root.exists():
            for sub in ("cco", "sta"):
                d = root / sub
                if d.exists():
                    files = [
                        item for item in d.rglob("*")
                        if item.is_file() and item.suffix.lower() in {".log", ".txt", ".jsonl"}
                    ]
                    for f in sorted(files, key=lambda item: item.stat().st_mtime, reverse=True):
                        groups[sub].append({
                            "path": str(f),
                            "name": f.name,
                            "relative_path": str(f.relative_to(root)),
                            "size": f.stat().st_size,
                        })
        return {"root": str(root), "groups": groups}


    @app.get("/api/fs/pick")
    @app.get("/api/module-serial/fs/pick")
    def fs_pick():
        if os.name != "nt":
            raise HTTPException(status_code=501, detail="仅 Windows 支持原生文件选择")
        last_path = infra.read_last_path(LAST_PATH_FILE)
        return {"path": infra.pick_file_via_tkinter_dialog(last_path)}

    return app


module_serial_svc = ModuleSerialService(log_dir=_log_dir())
app = create_app(module_serial_service=module_serial_svc)