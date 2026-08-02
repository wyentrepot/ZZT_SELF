import base64
import binascii
import os
import queue
import string
import threading
import subprocess
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


def _pick_file_via_tkinter_dialog(initial_dir: str = "") -> str:
    """调用 Windows 原生文件选择对话框，返回用户选中的文件路径。

    浏览器网页受安全沙箱限制无法读取本地路径，但后端运行在用户本机，
    可通过 tkinter（Windows 通用对话框）弹出系统原生选择器直接拿真实路径。
    文件内容不经浏览器，由后端后续按路径读取。取消、失败或超时返回空串。
    """
    result: "queue.Queue[str]" = queue.Queue()

    def _run() -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                path = filedialog.askopenfilename(
                    parent=root,
                    title="选择日志文件",
                    initialdir=initial_dir or None,
                    filetypes=[
                        ("日志文件", "*.txt *.log *.dat *.csv *.bin *.raw"),
                        ("所有文件", "*.*"),
                    ],
                )
                result.put(path or "")
            finally:
                root.destroy()
        except Exception:
            # 任何异常（tkinter 缺失、无桌面会话 TclError 等）都保证队列有值，
            # 避免主线程 result.get() 永久挂起
            result.put("")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=300)
    if thread.is_alive():
        # 超时：对话框仍打开（用户长时间未操作），返回空串不阻塞请求；
        # daemon 线程随后自然结束
        return ""
    return result.get()


_POWERSHELL_PICK_FILE_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '选择日志文件'
$dialog.Filter = '日志文件|*.txt;*.log;*.dat;*.csv;*.bin;*.raw|所有文件|*.*'
$initial = $env:HPLC_PICKER_INITIAL_DIR
if ($initial -and (Test-Path -LiteralPath $initial -PathType Leaf)) {
    $initial = Split-Path -LiteralPath $initial -Parent
}
if ($initial -and (Test-Path -LiteralPath $initial -PathType Container)) {
    $dialog.InitialDirectory = $initial
}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($dialog.FileName)
    [Console]::Out.Write([Convert]::ToBase64String($bytes))
}
"""


def _pick_file_via_native_dialog(initial_dir: str = "") -> str:
    """在本机桌面会话中打开 Windows 文件管理器选文件，并返回完整路径。"""
    environment = os.environ.copy()
    environment["HPLC_PICKER_INITIAL_DIR"] = initial_dir
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command",
             _POWERSHELL_PICK_FILE_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"无法启动 Windows 文件选择器：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "PowerShell 未返回错误详情"
        raise RuntimeError(f"Windows 文件选择器启动失败：{detail}")
    encoded_path = completed.stdout.strip()
    if not encoded_path:
        return ""
    try:
        return base64.b64decode(encoded_path, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeError("Windows 文件选择器返回了无效路径") from exc


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
        return {**service.version(), "picker_api_revision": 2}

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

    @app.get("/api/fs/pick")
    def fs_pick():
        """弹出 Windows 原生文件选择对话框，返回用户选中的真实路径。

        浏览器无法读取本地路径，由后端（同机进程）调用系统原生对话框；
        取消选择时返回空路径。非 Windows 环境返回 501。
        """
        if os.name != "nt":
            raise HTTPException(status_code=501, detail="仅 Windows 支持原生文件选择")
        try:
            path = _pick_file_via_native_dialog(_read_last_path())
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"path": path}

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
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            periods = log_service.list_minute_periods(period_minutes, cco_tei)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "periods": periods,
            "summary": {
                "total_periods": len(periods),
                "report_count": sum(p["report_count"] for p in periods),
            },
            "filters": {
                "period_minutes": period_minutes,
                "cco_tei": cco_tei.upper(),
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
