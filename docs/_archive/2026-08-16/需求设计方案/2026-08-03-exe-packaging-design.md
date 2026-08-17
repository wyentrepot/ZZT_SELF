# 侦听台 exe 打包设计文档

> 更新：2026-08-03
> 目标：将 FastAPI + pythonnet（C# GwHPLCAnalysis.dll）本地工具打包为可分发形态，使他人 `git clone` 后无需任何安装操作即可打开使用。

## 1. 背景与目标

当前项目通过 `启动解析工具.bat` 启动：要求本机有 Python、首次运行创建 `.venv` 并 pip 安装依赖、还要有编译好的 DLL。对使用者不友好。

**目标**：打包为 Windows 免安装分发产物，提交到 git 仓库，他人 `git clone` 后直接双击 exe 即可使用（打开浏览器访问 `http://127.0.0.1:8765/`）。

## 2. 用户已确认的决策

| 决策点 | 选择 |
|---|---|
| 分发形态 | **文件夹版（onedir）**：一个文件夹内含 exe + 依赖，启动快、打包稳定、杀毒误报少 |
| 仓库策略 | **exe 产物入库**：打包脚本 + 预打包产物都提交进 git（大文件用 Git LFS），clone 即用 |
| 运行时数据 | **exe 同目录**：索引数据库、last_path 等写在 exe 所在目录的 `runtime/` 子目录 |

## 3. 技术前提（已扫荡确认）

- Python 3.13.5（venv），pythonnet 3.1.0（支持 Py3.13），fastapi 0.141.1 / uvicorn 0.52.0 / httpx 0.28.1
- **PyInstaller 未安装**，需要新增（打包专用，不进入运行时 requirements）
- .NET Framework 4.8 已装（Windows 10/11 自带，目标机无需安装 .NET）
- `GwHPLCAnalysis.dll`（171KB）+ 依赖 `Newtonsoft.Json.dll`（同目录）——两者都必须打进包
- `hplc_web/static/`（app.js/index.html/styles.css）——前端资源必须打进包
- `hplc_web/runtime/` 有 1GB 索引文件——**绝不打包**，打包后写路径必须重定向
- Git LFS 3.7.0 已安装可用；仓库已有普通提交二进制的先例（`dll_Tesll/*.exe`）

## 4. 方案设计

### 4.1 整体架构

```
侦听台改造/
├── packaging/
│   ├── hplc_parser.spec      # PyInstaller 打包配置（onedir）
│   └── build_exe.bat         # 一键打包脚本（在 packaging/ 下运行）
├── dist/                     # 打包产物（入库，LFS 跟踪大文件）
│   └── 侦听台/
│       ├── 侦听台.exe        # 双击启动（自动开浏览器）
│       ├── _internal/        # PyInstaller 依赖（Python runtime、site-packages、DLL、static）
│       └── runtime/          # 运行时生成（exe 同目录，首次启动创建）
├── hplc_web/
│   ├── app.py                # 路径常量改为 frozen 感知（最小改动）
│   └── run.py                # frozen 下直接 uvicorn.run(app)
└── docs/需求设计方案/2026-08-03-exe-packaging-design.md
```

### 4.2 PyInstaller 配置要点（spec 文件）

- **onedir 模式**：`EXE(..., console=True)`（保留控制台窗口，关闭窗口即停止服务，与现有 bat 行为一致）
- **入口**：`hplc_web/run.py`（保留字符串导入 `uvicorn.run("hplc_web.app:app")`，但加 `--hidden-import hplc_web.app` 及 uvicorn 相关 hidden imports 确保静态分析完整）
- **数据文件**（`datas`）：
  - `hplc_web/static/` → `static/`（frozen 下落在 `_MEIPASS/static`，与 `app.py` 的 `BASE_DIR / "static"` 解析一致）
  - `dll/bin/Debug/GwHPLCAnalysis.dll` → `dll/bin/Debug/`
  - `dll/bin/Debug/Newtonsoft.Json.dll` → `dll/bin/Debug/`（DLL 依赖，必须同目录）
- **pythonnet 收集**：`collect_all('pythonnet')` 收集 `Python.Runtime.dll` 等 .NET 程序集；`clr` 模块由 PyInstaller hook 处理（PyInstaller 6.x 自带 `hook-clr.py` / pythonnet hook）
- **hidden-imports**：`hplc_web.app`、`hplc_web.parser_service`、`hplc_web.application_service`、`hplc_web.log_service`、`hplc_web.dotnet_parser`（`uvicorn.run` 字符串导入的模块 PyInstaller 静态分析不到，必须显式列出）
- **excludes**：`tkinter` 相关保留（文件选择器用），但可排除 `pytest`、`tests`、开发依赖
- **clean/console**：`--clean` 保证可复现

### 4.3 frozen 路径适配（代码最小改动）

`hplc_web/app.py` 顶部路径常量改为：

```python
import sys

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # 打包数据根（static/DLL 所在）
    return Path(__file__).resolve().parent

BASE_DIR = _base_dir()
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DLL = (BASE_DIR.parent / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll").resolve()
```

- 打包数据放在 `_MEIPASS` 下（onedir 实际是 `_internal/`），`sys._MEIPASS` 指向它，`__file__` 相对路径解析保持不变 → static 和 DLL 都能找到
- `DEFAULT_INDEX` / `LAST_PATH_FILE`（runtime 写路径）：frozen 下指向 **exe 所在目录** `Path(sys.executable).parent / "runtime"`：

```python
def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    return BASE_DIR / "runtime"

RUNTIME_DIR = _runtime_dir()
DEFAULT_INDEX = RUNTIME_DIR / "log_index.sqlite3"
LAST_PATH_FILE = RUNTIME_DIR / "last_path.txt"
```

> 说明：非 frozen 时行为与现在完全一致，不影响现有测试。

### 4.4 run.py 调整

保持入口不变，但 frozen 时 `sys._MEIPASS` 已正确指向数据根，`uvicorn.run("hplc_web.app:app")` 字符串导入在打包后通过 hidden-imports 保证可用。无需逻辑改动（仅验证）。

### 4.5 Git LFS 与入库策略

- `.gitattributes` 新增：
  ```
  dist/**/*.exe filter=lfs diff=lfs merge=lfs -text
  dist/**/*.dll filter=lfs diff=lfs merge=lfs -text
  dist/**/*.pyd filter=lfs diff=lfs merge=lfs -text
  dist/**/*.dylib filter=lfs diff=lfs merge=lfs -text
  ```
- `.gitignore` 调整：
  - 新增 `build/`（PyInstaller 中间产物）与 `*.spec` 的生成物忽略
  - **不忽略** `dist/`（要入库）
  - 保留 `runtime/` 忽略规则（exe 目录下的 runtime 是运行时生成）
- 现有 `dll_Tesll/` 普通提交二进制**不动**（避免重写历史）
- clone 即用条件：对方机器需 Git for Windows（自带 git-lfs）或单独安装 git-lfs —— 在 README 说明

### 4.6 打包脚本 build_exe.bat

```bat
@echo off
cd /d "%~dp0.."
setlocal
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run 启动解析工具.bat once first.
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --clean packaging\hplc_parser.spec
echo [OK] Output: dist\侦听台\
```

### 4.7 端口与多实例

- 保持 8765 端口，双击 exe 启动前先探测端口（frozen 下用纯 Python 探测，不依赖 PowerShell），若已被本服务占用则直接开浏览器；被其他进程占用则报错提示
- 简化：第一版不做多实例检测，与服务现有行为一致（bat 已有检测逻辑，可复用思路）

## 5. 验收标准

1. `packaging/build_exe.bat` 在干净流程下成功产出 `dist/侦听台/侦听台.exe`（exit 0）
2. 双击 `dist/侦听台/侦听台.exe`：
   - 自动打开 `http://127.0.0.1:8765/`，页面正常渲染
   - `GET /api/version` 返回 `GwHPLCAnalysis` 名称与版本（DLL 加载成功）
   - 用 `hplc_web/tests/data/gw_log_sample.txt` 建索引 → 分页帧列表 → 单帧详情正常
   - 分钟分析 tab 正常
3. `dist/侦听台/runtime/` 在 exe 同目录生成（log_index.sqlite3、last_path.txt 落位 exe 同目录）
4. 源码模式下 `hplc_web` 全量测试仍通过（frozen 分支不影响开发模式）：`python -m pytest hplc_web/tests -q` → 全绿
5. `git lfs ls-files` 显示 dist 下 exe/dll 已 LFS 跟踪；`git status` 干净
6. README 补充"一键使用"与"重新打包"说明

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| pythonnet 打包后 `clr.AddReference` 找不到 DLL 或 `Python.Runtime.dll` | `collect_all('pythonnet')` + 实测；必要时在 dotnet_parser.py 中把 DLL 目录加入 `clr` 搜索路径 |
| PyInstaller 静态分析漏掉字符串导入模块 | 显式 hidden-imports 清单 + 打包后冒烟测试 |
| onedir 体积大（预计 80-150MB） | 可接受（不入 git 历史库的单体文件用 LFS）；不压缩 |
| 杀毒软件误报 | onedir + console 模式误报率低；README 说明 |
| 1GB 索引文件误打包 | spec datas 明确排除 runtime/；打包后检查产物无大文件 |

## 7. 范围外（YAGNI）

- 不做单文件 exe（用户已选 onedir）
- 不做多实例管理/托盘图标/自更新
- 不动现有 `dll_Tesll/` 已提交二进制（避免重写历史）
- 不做签名（无证书）
