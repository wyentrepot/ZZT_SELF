"""侦听台独立应用（需求 0002 拆分）。

与模块日志（module_serial_app，8766）完全独立：独立 FastAPI 应用、
独立端口（默认 8765）、独立页面、独立串口采集（SerialCaptureService）。

复用共享基础设施：hplc_web/ 下服务、静态资源、DLL 解析库。
启动：python -m hplc_web.listener_run（见 listener_run.py）。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hplc_web import app as _shared_app  # 复用辅助函数、常量、DLL
from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.log_service import LogFileService
from hplc_web.parser_service import FrameValidationError, ParserService
from hplc_web.serial_service import SerialCaptureService

STATIC_DIR = _shared_app.STATIC_DIR
BASE_DIR = _shared_app.BASE_DIR
DEFAULT_DLL = _shared_app.DEFAULT_DLL
DEFAULT_INDEX = _shared_app._runtime_dir() / "log_index.sqlite3"


def _log_dir() -> Path:
    """侦听台日志根目录：项目根 LOG/侦听台。"""
    root = Path(__file__).resolve().parent.parent
    d = root / "LOG" / "侦听台"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_listener_app(service: ParserService, log_service=None,
                        serial_service=None) -> FastAPI:
    app = FastAPI(title="国网 HPLC 侦听台")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/version")
    def version():
        return {
            **service.version(),
            "picker_api_revision": 2,
            "minute_analysis_api_revision": 3,
            "frame_filter_api_revision": 2,
            "serial_api_revision": 1,
        }

    class ParseRequest(BaseModel):
        hex: str

    @app.post("/api/parse")
    def parse(request: ParseRequest):
        try:
            return service.parse(request.hex)
        except FrameValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"DLL 解析失败：{exc}") from exc

    class OpenLogRequest(BaseModel):
        path: str = Field(..., max_length=1024)

    @app.post("/api/logs/open", status_code=202)
    def open_log(request: OpenLogRequest):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        if serial_service is not None:
            serial_state = serial_service.status().get("state")
            if serial_state in ("running", "starting"):
                raise HTTPException(status_code=409, detail="串口监听正在运行，请先停止串口采集")
        try:
            result = log_service.start_index(request.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result

    @app.get("/api/logs/status")
    def log_status():
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.status()

    @app.get("/api/logs/frames")
    def log_frames(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                   query: str = Query("", max_length=100),
                   nid: str = Query("", max_length=16),
                   start_time: str = Query("", max_length=12),
                   end_time: str = Query("", max_length=12),
                   after_id: int | None = Query(None, ge=0)):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.query_frames(offset=offset, limit=limit, query=query,
                                        nid=nid, start_time=start_time,
                                        end_time=end_time, after_id=after_id)

    @app.get("/api/logs/frames/{frame_id}")
    def log_frame_detail(frame_id: int):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        frame = log_service.get_frame(frame_id)
        if frame is None:
            raise HTTPException(status_code=404, detail=f"帧 {frame_id} 不存在")
        return frame

    @app.get("/api/logs/minute-analysis")
    def minute_analysis(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                        query: str = Query("", max_length=100),
                        nid: str = Query("", max_length=16),
                        start_time: str = Query("", max_length=12),
                        end_time: str = Query("", max_length=12)):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.minute_analysis(offset=offset, limit=limit, query=query,
                                           nid=nid, start_time=start_time, end_time=end_time)

    @app.get("/api/fs/roots")
    def fs_roots():
        return _shared_app._windows_drives()

    @app.get("/api/fs/list")
    def fs_list(path: str = Query("", max_length=1024)):
        return _shared_app._list_directory(path)

    @app.get("/api/fs/pick")
    def fs_pick():
        return {"path": _shared_app._pick_file_via_tkinter_dialog(_shared_app._read_last_path())}

    # ---- 串口采集（侦听台）----
    @app.get("/api/serial/ports")
    def serial_ports():
        return {"ports": SerialCaptureService.list_available_ports()}

    @app.get("/api/serial/status")
    def serial_status():
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        return serial_service.status()

    class SerialStartRequest(BaseModel):
        port: str = Field("COM19", min_length=1, max_length=64)
        baudrate: int = Field(115200, ge=300, le=921600)

    @app.post("/api/serial/start", status_code=202)
    def serial_start(request: SerialStartRequest):
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        try:
            return serial_service.start(port=request.port, baudrate=request.baudrate)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/serial/stop")
    def serial_stop():
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        return serial_service.stop()

    return app


parser_service = ParserService(DotNetHplcParser(DEFAULT_DLL))
log_file_service = LogFileService(parser_service, DEFAULT_INDEX)
serial_capture_service = SerialCaptureService(log_file_service, port="COM19", log_dir=_log_dir())
app = create_listener_app(parser_service, log_file_service, serial_capture_service)
