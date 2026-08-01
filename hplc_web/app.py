from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.log_service import LogFileService
from hplc_web.parser_service import FrameValidationError, ParserService


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DLL = (
    BASE_DIR.parent / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
).resolve()
DEFAULT_INDEX = BASE_DIR / "runtime" / "log_index.sqlite3"


class ParseRequest(BaseModel):
    hex: str


class OpenLogRequest(BaseModel):
    path: str


def create_app(service: ParserService, log_service=None) -> FastAPI:
    app = FastAPI(title="国网 HPLC 日志解析工具")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/version")
    def version():
        return service.version()

    @app.post("/api/parse")
    def parse(request: ParseRequest):
        try:
            return service.parse(request.hex)
        except FrameValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"DLL 解析失败：{exc}") from exc

    @app.post("/api/logs/open", status_code=202)
    def open_log(request: OpenLogRequest):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.start_index(request.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/logs/status")
    def log_status():
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.status()

    @app.get("/api/logs/frames")
    def log_frames(
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        query: str = Query("", max_length=100),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.list_frames(offset=offset, limit=limit, query=query)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/logs/frames/{frame_id}")
    def log_frame_detail(frame_id: int):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.get_frame(frame_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该帧") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"帧详情解析失败：{exc}") from exc

    return app


parser_service = ParserService(DotNetHplcParser(DEFAULT_DLL))
log_file_service = LogFileService(parser_service, DEFAULT_INDEX)
app = create_app(parser_service, log_file_service)
