# 侦听台 exe 打包实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 FastAPI + pythonnet（GwHPLCAnalysis.dll）本地工具打包为 PyInstaller onedir 免安装分发产物，经 Git LFS 入库，他人 `git clone` 后双击 exe 即可使用。

**Architecture:** PyInstaller onedir 产出 `dist/侦听台/`（`侦听台.exe` + `_internal/` 依赖）。打包数据（static、DLL、Newtonsoft.Json.dll）放 `_internal/`，frozen 下经 `sys._MEIPASS` 定位；运行时数据（SQLite 索引、last_path）写 exe 同目录 `runtime/`。`hplc_web/app.py` 增加 frozen 感知的路径分叉，非 frozen 行为不变。

**Tech Stack:** Python 3.13.5、pythonnet 3.1.0、fastapi/uvicorn、PyInstaller（新增，仅打包用）、Git LFS 3.7.0。

## Global Constraints

- Python 版本：3.13（venv `.venv/`，命令统一用 `.venv\Scripts\python.exe`）
- pythonnet 3.1.0，`clr.AddReference` 按绝对路径加载 DLL —— 打包后 DLL 必须仍在可解析路径，`Newtonsoft.Json.dll` 必须与 `GwHPLCAnalysis.dll` 同目录
- .NET Framework 4.8（目标机自带，无需安装）
- 端口固定 `127.0.0.1:8765`；前端 API 全为相对路径，不得改动前端
- 运行时数据（runtime/）在 frozen 下写 exe 同目录，非 frozen 写 `hplc_web/runtime/`（现状不变）
- 产物入库用 Git LFS（`.gitattributes` 跟踪 `dist/**/*.exe`、`*.dll`、`*.pyd`）；`build/`、`*.spec` 生成物忽略；`runtime/` 忽略
- 源码模式 `hplc_web` 全量测试必须保持全绿（frozen 分叉不得影响开发模式）
- 冒烟验证需保证 8765 端口空闲（关闭正在运行的开发服务）

---

### Task 1: app.py 路径常量 frozen 感知

**Files:**
- Modify: `hplc_web/app.py:1-26`（顶部 import 与路径常量区）
- Test: `hplc_web/tests/test_frozen_paths.py`（新建）

**Interfaces:**
- Consumes: 现有 `BASE_DIR/STATIC_DIR/DEFAULT_DLL/DEFAULT_INDEX/LAST_PATH_FILE` 常量与 `create_app`（Task 2+ 依赖这些常量语义不变）
- Produces:
  - `hplc_web.app._is_frozen() -> bool`
  - `hplc_web.app._base_dir() -> Path`
  - `hplc_web.app._runtime_dir() -> Path`
  - `hplc_web.app._default_dll() -> Path`
  - 模块常量 `BASE_DIR/STATIC_DIR/DEFAULT_DLL/DEFAULT_INDEX/LAST_PATH_FILE`（值由上述函数计算）

- [ ] **Step 1: 写失败测试**

`hplc_web/tests/test_frozen_paths.py`：

```python
"""验证 frozen（PyInstaller）与非 frozen 两种运行形态下的路径解析。

frozen 语义：
- _base_dir() == sys._MEIPASS（打包数据根：static/DLL 所在）
- _runtime_dir() == exe 所在目录 / runtime（可写持久位置）
- _default_dll() == _MEIPASS / dll/bin/Debug/GwHPLCAnalysis.dll
非 frozen 语义与现状一致：
- _base_dir() == hplc_web 包目录（Path(__file__).parent）
- _runtime_dir() == _base_dir() / runtime
- _default_dll() == 仓库根 / dll/bin/Debug/GwHPLCAnalysis.dll
"""
import sys
from pathlib import Path

import pytest

from hplc_web import app as app_module

HPLC_WEB_DIR = Path(app_module.__file__).resolve().parent
REPO_ROOT = HPLC_WEB_DIR.parent


@pytest.fixture
def frozen_environment(monkeypatch, tmp_path):
    """模拟 PyInstaller onedir 冻结环境：_MEIPASS 与 exe 同上级目录。"""
    dist_dir = tmp_path / "dist" / "侦听台"
    internal_dir = dist_dir / "_internal"
    internal_dir.mkdir(parents=True)
    exe = dist_dir / "侦听台.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir))
    monkeypatch.setattr(sys, "executable", str(exe))
    return internal_dir, exe


def test_non_frozen_base_dir_is_package_dir():
    assert app_module._base_dir() == HPLC_WEB_DIR


def test_non_frozen_runtime_dir_is_package_runtime():
    assert app_module._runtime_dir() == HPLC_WEB_DIR / "runtime"


def test_non_frozen_default_dll_is_repo_dll():
    assert app_module._default_dll() == REPO_ROOT / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"


def test_frozen_base_dir_is_meipass(frozen_environment):
    internal_dir, _ = frozen_environment
    assert app_module._base_dir() == internal_dir


def test_frozen_runtime_dir_is_next_to_exe(frozen_environment):
    _, exe = frozen_environment
    assert app_module._runtime_dir() == exe.parent / "runtime"


def test_frozen_default_dll_is_under_meipass(frozen_environment):
    internal_dir, _ = frozen_environment
    assert app_module._default_dll() == internal_dir / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe -m pytest hplc_web/tests/test_frozen_paths.py -q`
Expected: 失败——`AttributeError: module 'hplc_web.app' has no attribute '_base_dir'`（函数尚不存在）

- [ ] **Step 3: 实现 frozen 路径分叉**

修改 `hplc_web/app.py`：在 `from pathlib import Path` 之后、`BASE_DIR = ...` 之前插入：

```python
import sys


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _base_dir() -> Path:
    """打包数据根：frozen 下为 PyInstaller _MEIPASS，否则为 hplc_web 包目录。"""
    if _is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _runtime_dir() -> Path:
    """运行时数据目录：frozen 下为 exe 同目录 runtime/，否则为包内 runtime/。"""
    if _is_frozen():
        return Path(sys.executable).resolve().parent / "runtime"
    return _base_dir() / "runtime"


def _default_dll() -> Path:
    """解析 DLL 默认路径：frozen 下数据打进 _MEIPASS，否则在仓库根 dll/ 下。"""
    if _is_frozen():
        return _base_dir() / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
    return _base_dir().parent / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
```

然后把原常量区改为调用函数：

```python
BASE_DIR = _base_dir()
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DLL = _default_dll()
DEFAULT_INDEX = _runtime_dir() / "log_index.sqlite3"
LAST_PATH_FILE = _runtime_dir() / "last_path.txt"
```

> 说明：`import sys` 需放在模块 import 区（与 `import os` 等并列）；函数定义置于 `Path` import 之后、常量之前。原 `DEFAULT_DLL` 表达式 `(BASE_DIR.parent / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll").resolve()` 被 `_default_dll()` 替代（frozen 下 DLL 在 `_MEIPASS` 内，父目录解析会错位）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe -m pytest hplc_web/tests/test_frozen_paths.py -q`
Expected: 6 passed

- [ ] **Step 5: 全量回归确认非 frozen 不回归**

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe -m pytest hplc_web/tests -q`
Expected: 全绿（现有 86 项 + 新增 6 项 = 92 passed）

- [ ] **Step 6: 提交**

```bash
git add hplc_web/app.py hplc_web/tests/test_frozen_paths.py
git commit -m "feat: 打包 frozen 感知的路径解析（DLL/static 读 _MEIPASS，runtime 写 exe 同目录）"
```

---

### Task 2: 冒烟验证脚本

**Files:**
- Create: `scripts/smoke_test_packaged.py`

**Interfaces:**
- Consumes: Task 4 的打包产物 `dist/侦听台/侦听台.exe`；现有 API `/api/version`、`/`、`/api/logs/open`、`/api/logs/status`、`/api/logs/frames`（字段：`status.state ∈ {idle, queued, running, done}`、`status.frame_count`、`frames.items/total`）
- Produces: 命令行工具 `smoke_test_packaged.py [exe路径]`，退出码 0=通过 / 1=失败（Task 4 验收使用）

- [ ] **Step 1: 编写冒烟脚本**

`scripts/smoke_test_packaged.py`：

```python
# -*- coding: utf-8 -*-
"""对打包产物（dist/侦听台/侦听台.exe）做端到端冒烟验证。

用法：.venv\\Scripts\\python.exe scripts\\smoke_test_packaged.py [exe路径]
默认 exe：dist/侦听台/侦听台.exe（相对仓库根，从脚本所在目录定位）。

验证项：
1. exe 启动后 /api/version 返回 GwHPLCAnalysis（DLL 加载成功）
2. 首页 / 正常返回
3. 用 hplc_web/tests/data/gw_log_sample.txt 建索引并分页取帧
4. runtime/ 生成在 exe 同目录
退出码 0 = 全部通过；非 0 = 失败。
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "hplc_web" / "tests" / "data" / "gw_log_sample.txt"


def _default_exe() -> Path:
    return ROOT / "dist" / "侦听台" / "侦听台.exe"


def _wait_ready(timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{HOST}/api/version", timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"服务未在 {timeout}s 内就绪：{last_error}")


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{HOST}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    exe = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _default_exe()
    if not exe.exists():
        print(f"[FAIL] exe 不存在：{exe}")
        return 1
    if not SAMPLE.exists():
        print(f"[FAIL] 测试样本不存在：{SAMPLE}")
        return 1

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        version = _wait_ready()
        print(f"[OK] /api/version -> {version}")
        if "GwHPLCAnalysis" not in version.get("name", ""):
            print(f"[FAIL] DLL 名称异常：{version}")
            return 1

        with urllib.request.urlopen(f"{HOST}/", timeout=3) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        if "侦听" not in html:
            print("[FAIL] 首页内容异常")
            return 1
        print("[OK] 首页可访问")

        payload = json.dumps({"path": str(SAMPLE)}).encode("utf-8")
        req = urllib.request.Request(
            f"{HOST}/api/logs/open", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            open_result = json.loads(resp.read().decode("utf-8"))
        print(f"[OK] /api/logs/open -> {open_result.get('state')}")

        deadline = time.monotonic() + 90
        status = {}
        while time.monotonic() < deadline:
            status = _get("/api/logs/status")
            if status.get("state") == "done":
                break
            time.sleep(1.0)
        if status.get("state") != "done":
            print(f"[FAIL] 索引未完成：{status}")
            return 1
        print(f"[OK] 索引完成：{status.get('frame_count')} 帧")

        frames = _get("/api/logs/frames?offset=0&limit=5")
        if not frames.get("items"):
            print("[FAIL] 分页无帧")
            return 1
        print(f"[OK] 分页取帧 {len(frames['items'])} 条（total={frames.get('total')}）")

        runtime_dir = exe.parent / "runtime"
        if not (runtime_dir / "log_index.sqlite3").exists():
            print(f"[FAIL] runtime 未生成在 exe 同目录：{runtime_dir}")
            return 1
        print(f"[OK] runtime 落位 exe 同目录：{runtime_dir}")

        print("[PASS] 冒烟验证全部通过")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 语法检查**

Run: `cd "D:\2-侦听台改造" && node --check scripts/smoke_test_packaged.py`（不可用则 `python -m py_compile scripts/smoke_test_packaged.py`）
Expected: 无语法错误

- [ ] **Step 3: 提交**

```bash
git add scripts/smoke_test_packaged.py
git commit -m "test: 打包产物端到端冒烟验证脚本"
```

---

### Task 3: 打包配置（spec + build_exe.bat）

**Files:**
- Create: `packaging/hplc_parser.spec`
- Create: `packaging/build_exe.bat`

**Interfaces:**
- Consumes: Task 1 的 frozen 路径逻辑（spec 的 datas 布局与 `_base_dir()/_default_dll()` 对应：static、DLL、Newtonsoft.Json.dll 均进 `_MEIPASS`）
- Produces: `packaging/hplc_parser.spec`（PyInstaller 配置）、`packaging/build_exe.bat`（一键构建，Task 4 执行）

- [ ] **Step 1: 编写 spec 文件**

`packaging/hplc_parser.spec`：

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 打包配置：产出 dist/侦听台/侦听台.exe。

在仓库根目录运行：
    .venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm packaging\\hplc_parser.spec

datas 布局与 hplc_web/app.py 的 frozen 路径解析一致：
- static/、dll/bin/Debug/ 均落入 _MEIPASS（onedir 下即 _internal/）
- 运行时数据 runtime/ 不入包，由 exe 在自身目录生成
"""
from pathlib import Path

SPEC_DIR = Path(__file__).resolve().parent
ROOT = SPEC_DIR.parent  # 仓库根（packaging/ 的上级）

from PyInstaller.utils.hooks import collect_all

# pythonnet：收集 Python.Runtime.dll 等 .NET 程序集与 clr 模块
net_datas, net_binaries, net_hiddenimports = collect_all("pythonnet")

datas = [
    (str(ROOT / "hplc_web" / "static"), "static"),
    (str(ROOT / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"), "dll/bin/Debug"),
    (str(ROOT / "dll" / "bin" / "Debug" / "Newtonsoft.Json.dll"), "dll/bin/Debug"),
] + list(net_datas)

binaries = list(net_binaries)

hiddenimports = [
    # uvicorn.run("hplc_web.app:app") 字符串导入，PyInstaller 静态分析发现不了
    "hplc_web.app",
    "hplc_web.parser_service",
    "hplc_web.application_service",
    "hplc_web.log_service",
    "hplc_web.dotnet_parser",
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
    [str(ROOT / "hplc_web" / "run.py")],
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
```

- [ ] **Step 2: 编写 build_exe.bat**

`packaging/build_exe.bat`：

```bat
@echo off
setlocal
cd /d "%~dp0.."
title Build HPLC Parser exe

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run 启动解析工具.bat once first to create it.
    pause
    exit /b 1
)
if not exist "dll\bin\Debug\GwHPLCAnalysis.dll" (
    echo [ERROR] dll\bin\Debug\GwHPLCAnalysis.dll not found. Build the C# project first.
    pause
    exit /b 1
)

echo [1/3] Installing PyInstaller...
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto :failed

echo [2/3] Building package (PyInstaller onedir)...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\hplc_parser.spec
if errorlevel 1 goto :failed

echo [3/3] Done.
echo Output: %CD%\dist\侦听台\
echo Smoke test: .venv\Scripts\python.exe scripts\smoke_test_packaged.py
exit /b 0

:failed
echo.
echo [ERROR] Build failed. See messages above.
pause
exit /b 1
```

- [ ] **Step 3: 语法/结构自检**

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe -c "import ast; ast.parse(open('packaging/hplc_parser.spec', encoding='utf-8').read()); print('spec syntax OK')" && node --check packaging/build_exe.bat`
Expected: `spec syntax OK`；bat 无语法错误（node --check 不适用于 bat，改用 `cmd /c "packaging\build_exe.bat"` 人工确认结构或跳过，见说明）

> 说明：`.bat` 无语法检查器，Step 3 仅验证 spec 语法；bat 结构在 Task 4 实际执行时验证。

- [ ] **Step 4: 提交**

```bash
git add packaging/hplc_parser.spec packaging/build_exe.bat
git commit -m "build: PyInstaller onedir 打包配置与一键构建脚本"
```

---

### Task 4: 执行打包并冒烟验证

**Files:**
- 无源码改动；运行 Task 3 的脚本，产出 `dist/侦听台/`（不入 git，Task 5 提交）
- 生成物：`build/`（PyInstaller 中间产物，.gitignore 忽略）

**Interfaces:**
- Consumes: Task 3 的 `packaging/build_exe.bat`、Task 2 的 `scripts/smoke_test_packaged.py`、Task 1 的 frozen 路径逻辑
- Produces: `dist/侦听台/侦听台.exe` + `_internal/` 依赖（Task 5 提交）

- [ ] **Step 1: 确认 8765 端口空闲**

Run: `powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count"`
Expected: `0`（如有服务在跑，先停掉，否则冒烟会连到旧服务）

- [ ] **Step 2: 运行打包**

Run: `cd "D:\2-侦听台改造" && packaging\build_exe.bat`
Expected: exit 0；输出 `Output: D:\2-侦听台改造\dist\侦听台\`

- [ ] **Step 3: 检查产物结构**

Run: `cd "D:\2-侦听台改造" && ls dist\侦听台\ && ls dist\侦听台\_internal\dll\bin\Debug\ && ls dist\侦听台\_internal\static\`
Expected: exe 在 `dist\侦听台\`；`_internal\dll\bin\Debug\` 含 `GwHPLCAnalysis.dll` + `Newtonsoft.Json.dll`；`_internal\static\` 含 `index.html/app.js/styles.css`；产物总大小合理（无 1GB 索引混入——`runtime/` 不应在 dist 内）

- [ ] **Step 4: 冒烟验证**

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe scripts\smoke_test_packaged.py`
Expected: 输出逐项 `[OK]`，最后 `[PASS] 冒烟验证全部通过`，退出码 0

> 若 pythonnet 收集不完整（`/api/version` 报 DLL 加载失败）：在 spec 的 `binaries` 中显式追加 `("<venv>/Lib/site-packages/pythonnet/runtime/Python.Runtime.dll", ".")` 后重跑 Task 4；若 `clr` 找不到 DLL，在 `dotnet_parser.py` 的 `clr.AddReference` 前把 DLL 目录加入搜索路径。记录实际修复，回填到本任务说明。

- [ ] **Step 5: 记录打包实际占用与修复点（供 Task 5 提交说明）**

Run: `cd "D:\2-侦听台改造" && du -sh dist\侦听台`
Expected: 记录数值（预计 80–150MB）

---

### Task 5: Git LFS 与 .gitignore 配置，提交产物

**Files:**
- Modify: `.gitattributes`（新增 LFS 跟踪）
- Modify: `.gitignore`（新增 `build/`、`dist/` 内忽略项保留 `runtime/`）

**Interfaces:**
- Consumes: Task 4 的 `dist/侦听台/` 产物
- Produces: LFS 跟踪的 dist 产物入库（clone 即用）

- [ ] **Step 1: 配置 .gitattributes**

`.gitattributes` 追加：

```
dist/**/*.exe filter=lfs diff=lfs merge=lfs -text
dist/**/*.dll filter=lfs diff=lfs merge=lfs -text
dist/**/*.pyd filter=lfs diff=lfs merge=lfs -text
dist/**/*.dylib filter=lfs diff=lfs merge=lfs -text
```

- [ ] **Step 2: 调整 .gitignore**

`.gitignore` 末尾追加（保留现有 `runtime/` 规则，新增 build 中间产物忽略；`dist/` 不忽略以便入库）：

```
# PyInstaller 中间产物
build/
dist/**/runtime/
```

> 说明：`packaging/hplc_parser.spec` 是源文件需入库，不整体忽略 `*.spec`；PyInstaller 使用已存在的 spec 文件（`-m PyInstaller packaging\hplc_parser.spec`）不会生成副本，故无需额外忽略。

- [ ] **Step 3: 验证 LFS 规则生效**

Run: `cd "D:\2-侦听台改造" && git add -f dist && git lfs ls-files | head`
Expected: `git lfs ls-files` 列出 dist 下 exe/dll/pyd 文件（LFS 指针入库，实际对象进 .git/lfs）

- [ ] **Step 4: 提交产物**

```bash
git add .gitattributes .gitignore dist
git commit -m "build: 提交 onedir 打包产物（Git LFS 跟踪），clone 后双击即用"
```

- [ ] **Step 5: 验证克隆即用（可选本机模拟）**

Run: `cd /tmp && git clone "D:\2-侦听台改造" zzt-clone-check && cd zzt-clone-check && ls dist\侦听台\侦听台.exe`
Expected: exe 文件可检出（LFS 指针被 smudge 还原）

---

### Task 6: README 更新与全量回归

**Files:**
- Modify: `README.md`（新增"一键使用"与"重新打包"两节）
- Modify: `doc/任务交接需求与进度表.md`（追加打包交付记录，可选）

**Interfaces:**
- Consumes: 全部前期任务的成果
- Produces: 文档化的使用与构建说明

- [ ] **Step 1: 更新 README**

`README.md` 在"## 六、构建与运行"前新增一节：

```markdown
## 六、一键使用（打包版）

仓库 `dist/侦听台/` 是免安装打包产物（onedir，Git LFS 跟踪）：

1. 克隆仓库（需 Git for Windows 或单独安装 git-lfs，首次 clone 自动拉取 LFS 对象）；
2. 双击 `dist/侦听台/侦听台.exe`；
3. 浏览器自动打开 `http://127.0.0.1:8765/`，即可选择日志建立索引、查看帧详情与分钟分析；
4. 关闭 exe 所在控制台窗口即停止服务；索引数据库等运行时数据写入 exe 同目录 `runtime/`，不会污染仓库。

> 目标机要求：Windows 10/11（自带 .NET Framework 4.8），无需安装 Python。
```

在"## 六、构建与运行"末尾追加重新打包说明：

```markdown
5. **重新打包 exe**：运行 `packaging\build_exe.bat`（需先按本节省 1-2 步准备 venv 与 DLL），产物输出到 `dist\侦听台\`。
```

- [ ] **Step 2: 全量回归**

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe -m pytest hplc_web/tests -q`
Expected: 全绿（92 passed）

Run: `cd "D:\2-侦听台改造" && .venv\Scripts\python.exe -m pytest parser_lib -q`
Expected: 108 passed / 66 skipped（无回归）

- [ ] **Step 3: 最终状态检查**

Run: `cd "D:\2-侦听台改造" && git status --short`
Expected: 仅 README 与进度表改动（dist 已提交）；无 runtime/ 泄漏、无 build/ 残留

- [ ] **Step 4: 提交**

```bash
git add README.md doc/任务交接需求与进度表.md
git commit -m "docs: 补充打包版一键使用与重新打包说明"
```

---

## 验收清单（对应 spec 第 5 节）

- [ ] `packaging/build_exe.bat` 成功产出 `dist/侦听台/侦听台.exe`（Task 4）
- [ ] 双击 exe 自动打开 `http://127.0.0.1:8765/`，`/api/version` 返回 `GwHPLCAnalysis`（Task 4 冒烟）
- [ ] 测试样本建索引 → 分页帧列表正常（Task 4 冒烟）
- [ ] `dist/侦听台/runtime/` 在 exe 同目录生成（Task 4 冒烟）
- [ ] 源码模式 `hplc_web` 全量测试全绿（Task 1/6）
- [ ] `git lfs ls-files` 显示 dist 下 exe/dll 已 LFS 跟踪；`git status` 干净（Task 5/6）
- [ ] README 含"一键使用"与"重新打包"说明（Task 6）
