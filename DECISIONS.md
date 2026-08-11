# 决策记录（DECISIONS.md）

本文件采用 ADR（Architecture Decision Record）精简版模式：**决策只追加、不覆盖**。已有记录永不修改、不删除；被取代的只把活动决策表里状态改为「❌ 已取代」，正文不动。

## 活动决策表

| # | 标题 | 状态 |
|---|------|------|
| 1 | 项目拆分为 listener / module_log 双应用 + shared 共享库 | ✅ 生效 |
| 2 | module_log 打包为本地桌面 exe（pywebview 内嵌窗口），保留网页模式 | ✅ 生效 |
| 3 | 侦听台打包为本地桌面 exe（pywebview 内嵌窗口）+ 统一菜单式打包脚本 | ✅ 生效 |

---

## ADR-1 项目拆分为 listener / module_log 双应用 + shared 共享库

- **日期**：2026-08-11
- **状态**：✅ 生效
- **决定**：将原 `hplc_web/` 单包拆分为三个平级顶层目录——`listener/`（侦听台，端口 8765）、`module_log/`（模块日志/烧录，端口 8766）、`shared/`（共享基础设施与解析链路）。`parser_lib/` 保留在仓库根作为独立共享解析库。
- **理由**：
  - 原 `hplc_web/` 单包同时承载侦听台与模块串口两套路由，二者只是进程/端口独立，代码仍耦合（`listener_app` 与 `module_serial_app` 互相通过 `from hplc_web import app` 共享）。
  - 用户希望两个应用完全解耦，仅靠一键启动脚本（`启动工具.bat`）统一拉起。
  - `parser_lib` 未来会被其他项目调用，故保留为仓库根独立共享库；`dll` 与解析链路归入 `shared/`。
- **影响**：
  - `listener/` 与 `module_log/` 各自拥有独立 `app.py`（create_app 工厂）与 `run.py`（uvicorn 入口），互不 import。
  - `shared/infra.py` 抽取通用工具（文件选择/盘符/目录列举），两个应用复用。
  - 启动脚本：根目录 `启动工具.bat`（菜单 1/2/3）+ 各项目独立启动 bat。
  - 测试保持在各项目内（`listener/test_*.py`、`module_log/test_*.py`、`shared/test_*.py`、`parser_lib/.../tests/`），全量 `pytest listener module_log shared parser_lib` 通过（290 passed / 66 skipped）。
- **被取代**：无（首次记录）。

---

## ADR-2 module_log 打包为本地桌面 exe（pywebview 内嵌窗口），保留网页模式

- **日期**：2026-08-11
- **状态**：✅ 生效
- **决定**：将 `module_log`（模块日志/烧录，端口 8766）打包为本地桌面软件 exe（`dist/模块日志/模块日志.exe`），用 **pywebview 内嵌窗口**加载 `/module-serial` 页面；同时**保留网页模式**（`python -m module_log.run` + 浏览器）两套启动方式并存。
- **理由**：
  - 用户希望网页功能在本地 app 内运行，但前端（`module-serial.html/js/css`）零重写。
  - 串口由后端 Python（`pyserial`）独占读写，前端仅 `fetch` HTTP 轮询，UI 框架不影响串口性能，故无需 Qt 重写前端。
  - `module_log` 仅依赖 `shared.infra` 的通用函数（无 pythonnet/C# DLL，比 listener 简单），打包难度低。
  - 本机已装 WebView2 runtime，pywebview 内嵌窗口可开箱即用。
- **影响**：
  - `module_log/app.py` 新增 frozen 路径处理（`_is_frozen`/`_base_dir`/`_runtime_dir`/`_log_dir`/`STATIC_DIR`/`RUNTIME_DIR`/`LAST_PATH_FILE`），frozen 下静态资源指向 `_MEIPASS/static`，LOG 与 runtime 落在 exe 同目录。
  - 新增 `module_log/desktop.py`（pywebview 内嵌窗口入口）：后台线程起 Uvicorn(8766) → 主线程 pywebview 开窗；未装 pywebview 时回退浏览器模式。
  - 新增 `packaging/module_log.spec`（PyInstaller onedir，console=False）+ `packaging/build_exe.bat module` 构建命令。
  - `启动工具.bat` 菜单新增选项 4（模块日志本地软件），保留 1/2/3。
  - `.gitignore` 新增忽略 `dist/*/LOG/`、`dist/*/*.WebView2/`（运行时产物），打包产物 `dist/模块日志/` 入库与 `dist/侦听台/` 一致。
  - 打包依赖：`pywebview`（含 bottle/proxy_tools）、`pyinstaller`。
- **被取代**：无（补充 ADR-1 的启动菜单，不取代）。

---

## ADR-3 侦听台打包为本地桌面 exe（pywebview 内嵌窗口）+ 统一菜单式打包脚本

- **日期**：2026-08-11
- **状态**：✅ 生效
- **决定**：将 `listener`（侦听台，端口 8765）打包为本地桌面软件 exe（`dist/侦听台桌面/侦听台桌面.exe`），用 **pywebview 内嵌窗口**加载 `/` 页面；保留网页模式（`python -m listener.run` + 浏览器）两套启动方式并存。同时将 `packaging/build_exe.bat` 改为**菜单式统一打包脚本**（选择应用与形态）。
- **理由**：
  - 与 ADR-2 的 module_log 桌面化一致，前端（`index.html/app.js/styles.css`）零重写，串口/DLL 由后端 Python 独占。
  - 侦听台已具备 frozen 路径处理（`_is_frozen`/`_base_dir`/`_runtime_dir`/`_log_dir`/`DEFAULT_DLL`），比 module_log 当时更省事。
  - 唯一差异是依赖 pythonnet + C# DLL（`GwHPLCAnalysis.dll`），但现有 `hplc_parser.spec` 已处理，桌面版 spec 复用即可。
  - 用户希望一键打包脚本能同时产出网页版与桌面版，依赖一次性装齐。
- **影响**：
  - 新增 `listener/desktop.py`（pywebview 内嵌窗口入口，照搬 module_log/desktop.py 模式，端口 8765 加载 `/`，未装 pywebview 回退浏览器）。
  - 新增 `packaging/hplc_parser_desktop.spec`（基于 hplc_parser.spec：入口改 desktop.py、console=False、额外 collect_all("webview")、保留 pythonnet+DLL，输出 `dist/侦听台桌面/`）。
  - `packaging/build_exe.bat` 改为菜单式：1=侦听台网页版 / 2=侦听台桌面版 / 3=模块日志桌面版，统一安装 pyinstaller+pywebview 依赖一次到位。
  - `启动工具.bat` 菜单新增选项 5（侦听台本地软件），保留 1/2/3/4。
  - `listener/test_launcher.py` 读取 bat 编码由 utf-8 改为 gbk（匹配项目「bat 用 GBK」约定，修复既有解码失败）。
  - 新增 `listener/test_desktop.py` 单元测试（5 用例，mock 验证服务地址/窗口 URL/回退分支/listener.app:app 解析含 DLL 初始化）。
  - 打包产物 `dist/侦听台桌面/` 入库与既有 `dist/*/` 约定一致（`.gitignore` 已忽略 `dist/*/LOG/`、`dist/*/*.WebView2/`）。
- **被取代**：无（补充 ADR-2 的桌面化范围与 ADR-1 的启动菜单）。


