import os
import string
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.log_service import LogFileService
from hplc_web.parser_service import FrameValidationError, ParserService


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DLL = (
    BASE_DIR.parent / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
).resolve()
DEFAULT_INDEX = BASE_DIR / "runtime" / "log_index.sqlite3"
LAST_PATH_FILE = BASE_DIR / "runtime" / "last_path.txt"

# 文件选择器只展示常见日志扩展名，避免目录被无关文件淹没
LOG_EXTENSIONS = {".txt", ".log", ".dat", ".csv", ".bin", ".raw"}


def _list_directory(path_text: str) -> dict:
    """列出目录内容：返回上级目录、子目录与日志文件（按名称排序）。

    若传入的是文件路径，自动定位到其父目录（便于文件选择器默认定位
    到上次打开的文件所在目录）。
    """
    try:
        target = Path(path_text).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="路径无效") from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"目录不存在：{target}")
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录：{target}")

    dirs: list[dict] = []
    files: list[dict] = []
    try:
        with os.scandir(target) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": entry.name, "path": entry.path})
                    elif entry.is_file(follow_symlinks=False):
                        suffix = Path(entry.name).suffix.lower()
                        if suffix in LOG_EXTENSIONS:
                            files.append(
                                {
                                    "name": entry.name,
                                    "path": entry.path,
                                    "size": entry.stat().st_size,
                                }
                            )
                except OSError:
                    continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"无权限访问：{target}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="读取目录失败") from exc

    key = lambda item: item["name"].lower()
    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "dirs": sorted(dirs, key=key),
        "files": sorted(files, key=key),
    }


def _windows_drives() -> list[dict]:
    """返回 Windows 可用盘符列表（如 C:\、D:\），非 Windows 返回空列表。"""
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append({"name": drive, "path": drive})
    return drives


def _read_last_path() -> str:
    try:
        return LAST_PATH_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


class ParseRequest(BaseModel):
    hex: str


class OpenLogRequest(BaseModel):
    path: str = Field(..., max_length=1024)


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
            result = log_service.start_index(request.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # 记录上次成功打开的路径，供文件选择器默认定位（限制长度防滥用）
        try:
            LAST_PATH_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAST_PATH_FILE.write_text(request.path[:1024], encoding="utf-8")
        except (OSError, UnicodeError):
            pass
        return result

    @app.get("/api/logs/status")
    def log_status():
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.status()

    @app.get("/api/fs/roots")
    def fs_roots():
        """返回可浏览的根路径：Windows 盘符；非 Windows 返回用户主目录。"""
        drives = _windows_drives()
        if drives:
            return {"roots": drives}
        home = str(Path.home().resolve())
        return {"roots": [{"name": home, "path": home}]}

    @app.get("/api/fs/list")
    def fs_list(path: str = Query("", max_length=1024)):
        """列出目录内容：子目录与常见日志文件。"""
        if not path.strip():
            raise HTTPException(status_code=400, detail="请提供目录路径")
        return _list_directory(path)

    @app.get("/api/fs/last")
    def fs_last():
        """返回上次成功打开的日志路径（可能为空字符串）。"""
        return {"path": _read_last_path()}

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

    @app.get("/api/logs/minute-analysis")
    def minute_analysis(
        period_minutes: int = Query(15, ge=1, le=1440),
        cco_tei: str = Query("001", min_length=3, max_length=3,
                             pattern=r"^[0-9A-Fa-f]{3}$"),
        deduplicate: bool = Query(True),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            periods = log_service.list_minute_periods(
                period_minutes, cco_tei, deduplicate
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "periods": periods,
            "summary": {
                "total_periods": len(periods),
                "raw_report_count": sum(
                    p["raw_report_count"] for p in periods
                ),
                "unique_station_count": sum(
                    p["unique_station_count"] for p in periods
                ),
                "duplicate_count": sum(
                    p["duplicate_count"] for p in periods
                ),
                "success_count": sum(p["success_count"] for p in periods),
                "failure_count": sum(p["failure_count"] for p in periods),
                "parse_error_count": sum(
                    p["parse_error_count"] for p in periods
                ),
            },
            "filters": {
                "period_minutes": period_minutes,
                "cco_tei": cco_tei.upper(),
                "deduplicate": deduplicate,
            },
        }

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
