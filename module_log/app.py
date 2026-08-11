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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared import infra
from module_log.module_serial_service import CHANNELS, ModuleSerialService

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _log_dir() -> Path:
    """模块日志根目录：项目根 LOG/模块。"""
    root = Path(__file__).resolve().parent.parent
    d = root / "LOG" / "模块"
    d.mkdir(parents=True, exist_ok=True)
    return d

def create_app(module_serial_service=None) -> FastAPI:
    app = FastAPI(title="模块日志 / 烧录串口")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/module-serial")
    def module_serial_page():
        return FileResponse(STATIC_DIR / "module-serial.html")

    @app.get("/api/version")
    def version():
        return {
            "app": "module-serial",
            "module_serial_api_revision": 2,
            "channels": list(CHANNELS),
        }

    # ---- 模块串口 ----
    @app.get("/api/module-serial/ports")
    def module_serial_ports():
        return {"ports": ModuleSerialService.list_available_ports()}

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
        channel: str = Field("cco", pattern=r"^(cco|sta)$")   # 目标通道

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
    def fs_roots():
        drives = infra.windows_drives()
        if drives:
            return {"roots": drives}
        home = str(Path(__file__).resolve().parent.parent)
        return {"roots": [{"name": home, "path": home}]}

    @app.get("/api/fs/list")
    def fs_list(path: str = Query("", max_length=1024)):
        return infra.list_directory(path)

    @app.get("/api/fs/pick")
    def fs_pick():
        if os.name != "nt":
            raise HTTPException(status_code=501, detail="仅 Windows 支持原生文件选择")
        last_path = infra.read_last_path(Path(__file__).resolve().parent / "runtime" / "last_path.txt")
        return {"path": infra.pick_file_via_tkinter_dialog(last_path)}

    return app


module_serial_svc = ModuleSerialService(log_dir=_log_dir())
app = create_app(module_serial_service=module_serial_svc)