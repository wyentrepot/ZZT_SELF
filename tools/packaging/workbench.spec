# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller onedir 打包配置：产出 dist/工作台/工作台.exe（AI 闭环研发验证工作台，FR-6）。

在仓库根目录运行：
    .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm tools\packaging\workbench.spec

datas 布局与 workbench/app.py 的 frozen 路径解析一致：
- static/（页签式 SPA）落入 _MEIPASS（onedir 下即 _internal/）
- scenarios/ 规则与场景模板（.json 数据文件，PyInstaller 不自动打包，显式收集）
- loghooks rules/（.json 数据文件，显式收集）
- C# DLL：libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll 以 binaries 打进，
  listener/app.py frozen 下从 _MEIPASS/dll/bin/Debug/ 解析（_default_dll）
- 运行时数据 data/、LOG/、runtime/ 不入包，由 exe 在自身目录生成
"""
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # PyInstaller 6.x：spec exec 时用 SPECPATH（spec 所在目录）
ROOT = SPEC_DIR.parent.parent  # 仓库根（packaging→tools→根）

from PyInstaller.utils.hooks import collect_all, collect_data_files

# pywebview：收集其前端资源
web_datas, web_binaries, web_hiddenimports = collect_all("webview")

datas = [
    (str(ROOT / "apps" / "workbench" / "static"), "static"),
    (str(ROOT / "apps" / "workbench" / "scenarios"), "workbench/scenarios"),
    # 统一后端挂载的子应用静态前端：各自映射到包目录，
    # frozen 下子应用 _base_dir()=包目录 → STATIC_DIR=包目录/static 保持一致
    (str(ROOT / "apps" / "listener" / "static"), "listener/static"),
    (str(ROOT / "apps" / "module_log" / "static"), "module_log/static"),
    # loghooks 规则文件（.json）——显式绝对路径收集（collect_data_files
    # 在 spec 分析期可能因 pathex 未含 libs/ 而漏包，改用显式 datas）
    (str(ROOT / "libs" / "loghooks" / "rules"), "loghooks/rules"),
] + list(web_datas)

binaries = [
    # C# 协议解析库（listener 挂载必需）；frozen 下 listener.app._default_dll()
    # 解析 _MEIPASS/dll/bin/Debug/GwHPLCAnalysis.dll
    (
        str(ROOT / "libs" / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"),
        "dll/bin/Debug",
    ),
] + list(web_binaries)

hiddenimports = [
    # uvicorn.run("workbench.app:app") 字符串导入，PyInstaller 静态分析发现不了
    "workbench",
    "workbench.app",
    "workbench.run",
    "workbench.desktop",
    "workbench.api",
    "workbench.orchestration",
    "workbench.orchestration.models",
    "workbench.orchestration.store",
    "workbench.orchestration.scenarios",
    "workbench.orchestration.compare",
    "workbench.orchestration.feedback",
    "workbench.orchestration.runner",
    # 统一后端挂载的子应用（惰性/动态 import）
    "listener",
    "listener.app",
    "listener.log_service",
    "listener.serial_service",
    "module_log",
    "module_log.app",
    "module_log.module_serial_service",
    "module_log.xmodem_flash",
    "module_log.loghooks_api",
    # 编排层复用 loghooks 引擎
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
    # 编排层复用 sim_concentrator runner
    "sim_concentrator",
    "sim_concentrator.api",
    "sim_concentrator.runner",
    "sim_concentrator.responder",
    "sim_concentrator.serial_io",
    "sim_concentrator.frame_codec",
    "sim_concentrator.matcher",
    "sim_concentrator.cli",
    # 共享层：路径工具 + C# DLL 解析链 + 帧解析服务
    "shared.infra",
    "shared.dotnet_parser",
    "shared.parser_service",
    "shared.application_service",
    # listener 依赖 pythonnet（clr.AddReference 动态加载）
    "pythonnet",
    "clr",
    # parser_lib 协议适配（simcon 构帧/解析）
    "parser_lib",
    "parser_lib.adapters.adapter_10376",
    # shared.infra 动态 import 的文件对话框
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
] + list(web_hiddenimports)

a = Analysis(
    [str(ROOT / "apps" / "workbench" / "desktop.py")],
    pathex=[str(ROOT), str(ROOT / "apps"), str(ROOT / "libs")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests", "test_"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="工作台",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 调试版：console 模式便于捕获启动错误（发布时改回 False）
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="工作台",
)
