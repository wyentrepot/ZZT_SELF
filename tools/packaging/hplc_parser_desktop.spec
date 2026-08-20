# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller onedir 打包配置：产出 dist/侦听台桌面/侦听台桌面.exe（pywebview 内嵌窗口）。

在仓库根目录运行：
    .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm tools\packaging\hplc_parser_desktop.spec

基于 hplc_parser.spec（网页/控制台版），差异：
- 入口为 listener/desktop.py（pywebview 内嵌窗口）
- console=False（windowed，不弹控制台）
- 额外收集 pywebview 前端资源

datas 布局与 listener/app.py 的 frozen 路径解析一致：
- static/、dll/bin/Debug/ 均落入 _MEIPASS（onedir 下即 _internal/）
- 运行时数据 runtime/、LOG/ 不入包，由 exe 在自身目录生成（frozen 下日志仍落 exe 同目录 LOG/）
"""
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # PyInstaller 6.x 下 spec 在 exec 时无 __file__，改用 SPECPATH（spec 所在目录）
ROOT = SPEC_DIR.parent.parent  # 仓库根（packaging→tools→根）

from PyInstaller.utils.hooks import collect_all

# pythonnet：收集 Python.Runtime.dll 等 .NET 程序集与 clr 模块
net_datas, net_binaries, net_hiddenimports = collect_all("pythonnet")
# pywebview：收集其前端资源（bottle 等）
web_datas, web_binaries, web_hiddenimports = collect_all("webview")

datas = [
    (str(ROOT / "apps" / "listener" / "static"), "static"),
    # 可维护映射模板；runtime hook 首次启动复制到 exe 同级 config/。
    (str(ROOT / "config" / "serial_ports.json"), "config"),
    (str(ROOT / "libs" / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"), "dll/bin/Debug"),
    (str(ROOT / "libs" / "shared" / "dll" / "bin" / "Debug" / "Newtonsoft.Json.dll"), "dll/bin/Debug"),
] + list(net_datas) + list(web_datas)

binaries = list(net_binaries) + list(web_binaries)

hiddenimports = [
    # uvicorn.run("listener.app:app") 字符串导入，PyInstaller 静态分析发现不了
    "listener.app",
    "shared.parser_service",
    "shared.application_service",
    "listener.log_service",
    "listener.serial_service",
    "listener.index_registry",
    "shared.serial_mapping",
    # 串口服务在条件导入中使用，显式收集保证冻结环境可枚举端口。
    "serial",
    "serial.tools.list_ports",
    "shared.dotnet_parser",
    # uvicorn 动态加载的 loop/protocol/lifespan 实现
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.logging",
    # shared.infra 里动态 import 的 tkinter 文件对话框
    "tkinter",
    "tkinter.filedialog",
] + list(net_hiddenimports) + list(web_hiddenimports)

a = Analysis(
    [str(ROOT / "apps" / "listener" / "desktop.py")],
    pathex=[str(ROOT), str(ROOT / "apps"), str(ROOT / "libs")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPEC_DIR / "runtime_hooks" / "ensure_serial_ports_config.py")],
    excludes=["pytest", "tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="侦听台桌面",
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
    name="侦听台桌面",
)
