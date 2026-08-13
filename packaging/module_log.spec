# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller onedir 打包配置：产出 dist/模块日志/模块日志.exe（pywebview 内嵌窗口）。

在仓库根目录运行：
    .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm packaging\module_log.spec

datas 布局与 module_log/app.py 的 frozen 路径解析一致：
- static/ 落入 _MEIPASS（onedir 下即 _internal/）
- 运行时数据 LOG/、runtime/ 不入包，由 exe 在自身目录生成
"""
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # PyInstaller 6.x 下 spec 在 exec 时无 __file__，改用 SPECPATH（spec 所在目录）
ROOT = SPEC_DIR.parent  # 仓库根（packaging/ 的上级）

from PyInstaller.utils.hooks import collect_all, collect_data_files

# pywebview：收集其前端资源（bottle 等）
web_datas, web_binaries, web_hiddenimports = collect_all("webview")

datas = [
    (str(ROOT / "module_log" / "static"), "static"),
    # loghooks 规则文件（.json 数据文件，PyInstaller 不会自动打包，需显式收集）
] + collect_data_files("loghooks") + list(web_datas)

binaries = list(web_binaries)

hiddenimports = [
    # uvicorn.run("module_log.app:app") 字符串导入，PyInstaller 静态分析发现不了
    "module_log.app",
    "module_log.module_serial_service",
    "module_log.xmodem_flash",
    "shared.infra",
    # shared.infra 里动态 import 的 tkinter 文件对话框
    "tkinter",
    "tkinter.filedialog",
    # uvicorn 动态加载的 loop/protocol/lifespan 实现
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    # 串口
    "serial",
    "serial.tools.list_ports",
    # 模拟集中器（第三页签）：module_log/app.py 在 create_app 内 import，需显式声明
    "sim_concentrator",
    "sim_concentrator.api",
    "sim_concentrator.runner",
    "sim_concentrator.responder",
    "sim_concentrator.serial_io",
    "sim_concentrator.frame_codec",
    "sim_concentrator.matcher",
    "sim_concentrator.cli",
    # sim_concentrator 依赖 parser_lib（adapter_10376 构帧/解析）
    "parser_lib",
    "parser_lib.adapters.adapter_10376",
    # 对照解析（loghooks）：app.py 函数内动态 import，需显式声明
    "module_log.loghooks_api",
    "loghooks",
    "loghooks.engine",
    "loghooks.rules",
    "loghooks.sources",
    "loghooks.matchers",
    "loghooks.sequence",
    "loghooks.correlate",
    "loghooks.output",
    "loghooks.runtime",
    "loghooks.cli",
] + list(web_hiddenimports)

a = Analysis(
    [str(ROOT / "module_log" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "listener"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="模块日志",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 桌面软件：windowed，不弹控制台
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="模块日志",
)
