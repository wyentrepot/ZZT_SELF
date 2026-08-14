"""共享基础工具（两个项目共用的无状态辅助函数）。

存放原 hplc_web/app.py 中的纯工具函数：Windows 盘符枚举、目录列举、
上次路径读写、Windows 原生文件选择对话框等。这些函数不依赖任何 FastAPI
路由，也不依赖任何项目私有路径，故抽到 shared/ 供 listener/ 与 module_log/
复用。

路径常量（STATIC_DIR、DEFAULT_DLL、runtime/log 目录等）因各项目位置不同，
由各项目 app.py 基于 __file__ 自行计算，不放在本文件。
"""
import base64
import binascii
import os
import queue
import string
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import HTTPException, Query

# 文件选择器只展示常见日志扩展名，避免目录被无关文件淹没
LOG_EXTENSIONS = {".txt", ".log", ".dat", ".csv", ".bin", ".raw"}


def ensure_paths() -> None:
    """把仓库根、apps/、libs/ 同时加入 sys.path（幂等）。

    目录分层后（apps/listener、libs/shared 等），各进程入口（run/desktop/
    conftest）在 import 顶层包之前调用本函数，保持 `from shared ...`、
    `from sim_concentrator ...` 等导入名不变。frozen（PyInstaller）环境下
    依赖 spec 的 pathex 注入，本函数仅在源码/开发模式生效。
    """
    caller = Path(__file__).resolve()
    repo_root = caller.parent.parent  # shared/infra.py → libs → 仓库根
    for sub in ("", "apps", "libs"):
        p = str(repo_root / sub) if sub else str(repo_root)
        if p not in sys.path:
            sys.path.insert(0, p)


def list_directory(path_text: str) -> dict:
    """列出目录内容：返回上级目录、子目录与日志文件（按名称排序）。"""
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


def windows_drives() -> list[dict]:
    """返回 Windows 可用盘符列表（如 C:\\、D:\\），非 Windows 返回空列表。"""
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append({"name": drive, "path": drive})
    return drives


def read_last_path(last_path_file: Path) -> str:
    try:
        return last_path_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def write_last_path(last_path_file: Path, path_text: str) -> None:
    try:
        last_path_file.parent.mkdir(parents=True, exist_ok=True)
        last_path_file.write_text(path_text[:1024], encoding="utf-8")
    except (OSError, UnicodeError):
        pass


def pick_file_via_tkinter_dialog(initial_dir: str = "", timeout_s: int = 60) -> str:
    """调用 Windows 原生文件选择对话框，返回用户选中的文件路径。

    浏览器网页受安全沙箱限制无法读取本地路径，但后端运行在用户本机，
    可通过 tkinter（Windows 通用对话框）弹出系统原生选择器直接拿真实路径。
    取消、失败或超时返回空串。
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
                    title="选择固件 .bin 文件",
                    initialdir=initial_dir or None,
                    filetypes=[
                        ("固件/日志文件", "*.bin *.txt *.log *.dat *.csv *.raw"),
                        ("所有文件", "*.*"),
                    ],
                )
                result.put(path or "")
            finally:
                root.destroy()
        except Exception:
            result.put("")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
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


def pick_file_via_native_dialog(initial_dir: str = "") -> str:
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
