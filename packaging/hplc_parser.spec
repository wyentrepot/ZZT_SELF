# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller onedir 打包配置：产出 dist/侦听台/侦听台.exe。

在仓库根目录运行：
    .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm packaging\hplc_parser.spec

datas 布局与 listener/app.py 的 frozen 路径解析一致：
- static/、dll/bin/Debug/ 均落入 _MEIPASS（onedir 下即 _internal/）
- 运行时数据 runtime/ 不入包，由 exe 在自身目录生成
"""
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # PyInstaller 6.x 下 spec 在 exec 时无 __file__，改用 SPECPATH（spec 所在目录）
ROOT = SPEC_DIR.parent  # 仓库根（packaging/ 的上级）

from PyInstaller.utils.hooks import collect_all

# pythonnet：收集 Python.Runtime.dll 等 .NET 程序集与 clr 模块
net_datas, net_binaries, net_hiddenimports = collect_all("pythonnet")

datas = [
    (str(ROOT / "listener" / "static"), "static"),
    (str(ROOT / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"), "dll/bin/Debug"),
    (str(ROOT / "shared" / "dll" / "bin" / "Debug" / "Newtonsoft.Json.dll"), "dll/bin/Debug"),
] + list(net_datas)

binaries = list(net_binaries)

hiddenimports = [
    # uvicorn.run("listener.app:app") 字符串导入，PyInstaller 静态分析发现不了
    "listener.app",
    "shared.parser_service",
    "shared.application_service",
    "listener.log_service",
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
] + list(net_hiddenimports)

a = Analysis(
    [str(ROOT / "listener" / "run.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name="侦听台",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="侦听台",
)
