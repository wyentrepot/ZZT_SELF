"""侦听台独立应用（需求 0002 拆分）。

与模块日志（module_serial_app，8766）独立端口/独立进程。复用已验证的
app.create_app 工厂（完整侦听台功能 + 解析），保证功能完整可用。
启动：python -m hplc_web.listener_run（见 listener_run.py）。
"""
from __future__ import annotations

from pathlib import Path

from hplc_web import app as _shared_app
from hplc_web.app import create_app
from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.log_service import LogFileService
from hplc_web.parser_service import ParserService
from hplc_web.serial_service import SerialCaptureService

DEFAULT_DLL = _shared_app.DEFAULT_DLL


def _log_dir() -> Path:
    """侦听台日志根目录：项目根 LOG/侦听台。"""
    root = Path(__file__).resolve().parent.parent
    d = root / "LOG" / "侦听台"
    d.mkdir(parents=True, exist_ok=True)
    return d


parser_service = ParserService(DotNetHplcParser(DEFAULT_DLL))
log_file_service = LogFileService(parser_service, _shared_app._runtime_dir() / "log_index.sqlite3")
serial_capture_service = SerialCaptureService(log_file_service, port="COM19", log_dir=_log_dir())
# 复用完整 create_app（不含模块串口服务 → 模块路由返回 503），保证侦听台全部功能可用
app = create_app(parser_service, log_file_service, serial_capture_service)
