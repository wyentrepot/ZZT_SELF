# 决策记录（DECISIONS.md）

本文件采用 ADR（Architecture Decision Record）精简版模式：**决策只追加、不覆盖**。已有记录永不修改、不删除；被取代的只把活动决策表里状态改为「❌ 已取代」，正文不动。

## 活动决策表

| # | 标题 | 状态 |
|---|------|------|
| 1 | 项目拆分为 listener / module_log 双应用 + shared 共享库 | ✅ 生效 |
| 2 | module_log 打包为本地桌面 exe（pywebview 内嵌窗口），保留网页模式 | ✅ 生效 |
| 3 | 侦听台打包为本地桌面 exe（pywebview 内嵌窗口）+ 统一菜单式打包脚本 | ✅ 生效 |
| 4 | loghooks 日志运行状态钩子：配置驱动 + 内容匹配为主 + 行号弱约束 + 定期更新闭环 | ✅ 生效 |
| 5 | loghooks 规则按 cco/sta 模块隔离，不能通用 | ✅ 生效 |
| 6 | module_log 新增「对照解析」页签：事件解析与原始日志双向绑定联动 | ✅ 生效 |
| 7 | 对照解析页来源卡片化（串口/导入文件互斥二选一）+ 深色终端精致化美化 | ✅ 生效 |
| 8 | module_log exe 打包修正：spec 补 loghooks 模块 + rules 数据文件 | ✅ 生效 |
| 9 | 一键启动脚本统一为 GBK + CRLF 编码，修复中文 cmd 下乱码一闪而过 | ✅ 生效 |
| 10 | 新增 sim_concentrator 模拟集中器模块：统一用 adapter_10376，独立可读写串口，REST/CLI 验证任务闭环 | ✅ 生效 |
| 11 | module_log 启动脚本编码修正回 GBK（ADR-9 落地补漏）+ test_launcher 断言同步 | ✅ 生效 |
| 12 | OpenViking 记忆后端 embedding/VLM 切换到火山方舟（doubao-embedding-vision + doubao-seed-code） | ✅ 生效 |
| 13 | 模拟集中器前端可视化：module_log 新增第三页签「模拟集中器」，挂载 simcon 子应用 | ✅ 生效 |
| 14 | 仓库目录结构两阶段迁移（apps/libs 分层 + docs/tools/data 归组 + graphify-out 清理） | ✅ 生效 |

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

---

## ADR-4 loghooks 日志运行状态钩子：配置驱动 + 内容匹配为主 + 行号弱约束 + 定期更新闭环

- **日期**：2026-08-12
- **状态**：✅ 生效
- **决定**：
  - 新增独立 `loghooks/` 包（`engine/sequence/matchers/rules/sources/correlate/output/cli`），以**声明式 JSON 规则**从 `module_log`（文本行）与 `listener`（simple dict）双来源抓取关键运行状态事件（入网/采集/发送），产出摘要 JSON 供 AI 烧录验证核查；规则表结构不变，第三来源 `concentrator_10376`（13762 帧）预留注册位。
  - **匹配主锚是消息内容**（text 正则 / field 字段值），**不依赖行号**；`match` 可选携带 `file`/`line`/`line_tolerance`（默认 ±10）作**弱约束**：行号超出容差时**仍命中事件**，但标记 `line_drift: true` 并在摘要汇总 `rule_drifts` 漂移清单。
  - 规则**全部 json 同时加载 + 自动识别省份**（`--province` 仅作可选过滤），`detected_provinces` 给出命中判定。
  - 规则更新走**半自动闭环**：工程 AI 重跑扫描（`docs/loghooks-source-scan-prompt.md`）→ `python -m loghooks rules diff --old <旧> --new <新>` 检出新增/删除/行号漂移/msg 变更 → 按清单更新规则文件 → 真实日志回归命中率。对比基准为 `(file, msg)` 稳定标识。
  - module_log 运行时接入为**可选调用点**（异步队列 + `LOG_HOOKS_ENABLED` 开关 + 失败静默降级），不侵入现有解析链路。
- **理由**：
  - 模块日志被海量轮询/状态机噪音淹没，AI 核查烧录结果难以聚焦关键事件；侦听台帧可深度解析出结构化字段，两者形态完全不同，需要统一声明式框架。
  - 工程侧源码会持续变更，行号必然漂移，但**关键打印语句内容相对稳定**——故匹配以内容为主锚、行号仅作弱约束且漂移不阻断事件，保证源码变更不中断烧录验证。
  - 省份/分支差异用"一份规则文件"承载，新省份 = 新增 json，无需改 Python 代码。
- **影响**：
  - 新增 `loghooks/` 包及 `rules/common.json`、`rules/provinces/anhui.json` 规则文件；`docs/loghooks-design.md` 为设计定稿（状态：已确认）。
  - `module_log/module_serial_service.py` 是**唯一现有代码改动**（可选 run_loghooks 调用点）。
  - **不改动**：`listener` 现有解析链路、`shared`、`parser_lib`、DLL。
  - `loghooks/rules_source/` 已由工程侧 AI 产出扫描结果（`cco_print_scan.json` 2806 条），作为规则编写素材。
- **被取代**：无（首次记录 loghooks）。

---

## ADR-5 loghooks 规则按 cco/sta 模块隔离，不能通用

- **日期**：2026-08-12
- **状态**：✅ 生效
- **决定**：
  - 规则 schema 新增 `module` 字段（`cco`/`sta`/`common`），用于标识该打印规则适用的模块。
  - 规则文件按模块拆分：`loghooks/rules/cco.json`（CCO 专属）、`loghooks/rules/sta.json`（STA 专属）、`loghooks/rules/common.json`（真·跨模块通用，如 `bcn crc check err`）、`loghooks/rules/provinces/`（省份专属，跨模块适用）。
  - `RuleLoader` 按文件自动推断 module：`cco.json`→cco、`sta.json`→sta、`common.json`→common；`filter_by_module(module)` 返回「该模块专属 + common 通用」的规则集。
  - CLI `scan` 新增 `--module cco|sta|common` 参数（不传 = 仅通用 common 规则）。
  - module_log 运行时接入 `run_loghooks(module, direction, text)` 传入通道名（cco/sta），按 module 过滤规则。
  - 修复 sequence 超时判定 bug：超时用**日志绝对时间戳**而非行计数（此前按行数×1000ms 当时间，日志行多时误报超时）。
- **理由**：
  - CCO 与 STA 是不同固件工程，打印语句完全不同（CCO 有 `onnet cnt`/`assocreq send ok`，STA 有 `recv bcn`/`nwk disc done`/`nwk assoc ok`），不能互相套用规则。
  - 此前只有 CCO 扫描结果（`cco_print_scan.json`），STA 未扫描；STA 规则暂从真实测试日志逆向提取。
  - 用同一规则集匹配两类日志会导致 STA 日志去匹配 CCO 规则（逻辑错误、误报）。
- **影响**：
  - `loghooks/rules/` 从单一 `common.json` 拆分为 `cco.json`（6 条）+ `sta.json`（7 条）+ `common.json`（1 条）+ `provinces/anhui.json`（2 条）。
  - CLI 用法：`python -m loghooks scan <cco日志> --module cco` / `python -m loghooks scan <sta日志> --module sta`。
  - 全量测试 110 passed（新增 module 隔离 2 用例）。
- **被取代**：无（补充 ADR-4 的规则组织方式）。

---

## ADR-6 module_log 新增「对照解析」页签：事件解析与原始日志双向绑定联动

- **日期**：2026-08-12
- **状态**：✅ 生效
- **决定**：
  - 在 `module_log` 前端（`module-serial.html/js`）新增顶部**页签栏**：「实时日志」与「对照解析」，同页面切换、不另开路由。
  - 「对照解析」页左右分栏：**左栏事件流**（等级色条 + 分类图标 + 含义 + 时间，卡片式）、**右栏原始日志**（等宽可滚动）。
  - **双向联动**：点击左栏事件 → 右栏滚动定位并高亮对应日志行；点击右栏日志行 → 左栏高亮对应事件。
  - **来源**：打开日志文件/目录（`/api/loghooks/scan`）与 实时串口端口（`/api/loghooks/realtime`）两套都支持，前端可切换；实时模式 2s 定时刷新。
  - 后端新增 `module_log/loghooks_api.py`：`scan_log_file` 复用 loghooks 引擎按模块（cco/sta）解析，返回**事件 + 原始日志行绑定**（含行号/时间/文本）；`scan_realtime` 扫内存日志缓冲。路由挂到 `/api/loghooks/*`。
  - `loghooks/engine.py` 的 `Event` 新增 `source_line_idx`（原始日志行序号），供前端定位绑定。
- **理由**：
  - 用户需要直观看到"解析出的事件对应日志里的哪一行"，便于核查烧录/入网结果时快速定位原文。
  - 双向联动避免人肉在事件与日志间来回找行。
  - 实时端口与历史文件两种来源覆盖"采集中实时看"与"事后复盘"两个场景。
- **影响**：
  - 前端新增页签 + 对照解析页（HTML/CSS/JS），实时日志页签内原功能不变。
  - 后端新增 3 个 API：`/api/loghooks/scan`、`/api/loghooks/realtime`、`/api/loghooks/sources`。
  - 新增测试 `module_log/test_loghooks_api.py`（4 用例）；全量测试 117 passed。
  - **不改动**：listener、shared、parser_lib、DLL。
- **被取代**：无（loghooks 能力的应用内可视化补充）。
---

## ADR-7 对照解析页来源卡片化（串口/导入文件互斥二选一）+ 深色终端精致化美化

- **日期**：2026-08-12
- **状态**：✅ 生效
- **决定**：
  - 对照解析页**来源二选一（互斥）**，用**卡片式选择**呈现：`🔌 实时串口` 与 `📂 导入日志文件` 两张卡片，选中高亮带 ✓ 标识。
  - **串口来源**：复用模块日志已接入串口的实时内存日志（`/api/loghooks/realtime`），页内展示串口状态（模块/COM/波特率/运行中）；与导入文件互斥，不能同时。
  - **导入文件来源**：支持"打开文件"与"打开目录"（复用 `/api/fs/pick` + `/api/loghooks/scan`），可扫描历史日志。
  - 模块切换（CCO/STA）用**分段控件 pill**（非下拉）。
  - 新增**统计条**：事件数 / 日志行数 / 来源文件数 / 行号漂移数。
  - **美化风格**：延续深色终端风，精致化为来源卡片（渐变+微光效+圆角+浮起 hover）、事件卡片（等级色条+图标底色）、日志行联动高亮、图例、分段控件。
- **理由**：
  - 实时串口与导入文件是**互斥来源**（同一时刻只能解析一个），卡片化让用户直观理解二选一语义。
  - 用户要求"来源可以是串口，也可以是导入文件"，且要求美化前端；深色终端精致化保持与现有界面主题统一。
- **影响**：
  - `module-serial.html`：来源卡片区、配置区、统计条、图例；`module-serial.js`：`cmpSetSource/cmpPickFile/cmpPickDir/cmpRefreshSerialStatus/cmpUpdateStats` 及来源卡片/分段控件绑定；`<style>`：新增 `.cmp-srcbar/.cmp-srccard/.cmp-modseg/.cmp-stats` 等。
  - 后端不改动（复用既有 `/api/loghooks/scan`、`/api/loghooks/realtime`、`/api/module-serial/status`、`/api/fs/pick`）。
  - 全量测试 119 passed。
- **被取代**：无（ADR-6 的对照解析页交互与视觉升级）。
---

## ADR-8 module_log exe 打包修正：spec 补 loghooks 模块 + rules 数据文件

- **日期**：2026-08-12
- **状态**：✅ 生效
- **决定**：
  - 修正 `packaging/module_log.spec`，确保桌面 exe（`dist/模块日志/模块日志.exe`）包含 loghooks 对照解析全部能力：
    - `hiddenimports` 补充 `module_log.loghooks_api` 及 `loghooks` 全部子模块（`engine/rules/sources/matchers/sequence/correlate/output/runtime/cli`）——app.py 在函数内动态 import，PyInstaller 静态分析检测不到。
    - `datas` 补充 `collect_data_files("loghooks")`——loghooks 的**规则 .json 数据文件**（`rules/*.json`）PyInstaller 不会自动打包，缺失会导致 RuleLoader 加载不到规则、事件解析为 0。
  - 重新打包 exe，并实机验证。
- **理由**：
  - 桌面 exe 是 PyInstaller 打包快照，不跟随源码自动更新；此前 exe 是 08-11 旧产物，不含对照解析页。
  - 首次打包后发现 exe 内 loghooks `.py` 进了 PYZ 但**规则 json 缺失**，`/api/loghooks/scan` 事件数为 0；补 `collect_data_files` 后修复。
- **影响**：
  - `packaging/module_log.spec`：hiddenimports +8、datas +collect_data_files("loghooks")。
  - 重新打包 `dist/模块日志/`（exe、static、base_library 更新 + 新增 `_internal/loghooks/rules/*.json`）。
  - 实机验证：exe `/api/loghooks/scan` 返回 `events=1305`、`files=5`、`lines=113844`，与网页版一致。
  - 启动脚本 `启动工具.bat` 选项 4 指向的 exe 现为最新版，含对照解析。
- **被取代**：无（补充 ADR-4/6/7 的打包落地）。

---

## ADR-9 一键启动脚本统一为 GBK + CRLF 编码，修复中文 cmd 下乱码一闪而过

- **日期**：2026-08-13
- **状态**：✅ 生效
- **决定**：将全部启动/打包 .bat 文件统一为 **GBK 编码 + CRLF 行尾**（`启动工具.bat`、`listener/启动侦听台.bat`、`module_log/启动模块日志.bat`、`packaging/build_exe.bat`），与 ADR-3 既有「bat 用 GBK」约定一致。
- **理由**：
  - 中文 Windows cmd 默认按 GBK（代码页 936）解析 .bat；若文件是 **UTF-8 无 BOM**，中文汉字字节被按 GBK 拆解，导致 `set /p "HPLC_CHOICE=请输入..."` 的变量名被截断成 `LC_CHOICE`、菜单 `echo` 中的 `exe` 被误判为独立命令，脚本立即报错崩溃——即「一闪而过，没有启动程序」。
  - git HEAD 中 4 个 bat 本就为 GBK，但工作区里 `module_log/启动模块日志.bat` 与 `packaging/build_exe.bat` 被改成了 UTF-8（git 报 M），是本次故障直接原因。
  - 统一 CRLF 行尾以匹配 `.gitattributes`（`*.bat eol=crlf`）并兼容 cmd。
- **影响**：
  - 4 个 .bat 全部为 GBK + CRLF（LF-only=0）。
  - 实测：`启动工具.bat` 菜单正常显示中文、`set /p` 变量名完整、无 `LC_CHOICE`/`ktop` 崩溃错误，`uvicorn` 在 8765 端口正常启动、前端 API 返回 200。
  - 后续新增/修改 .bat 一律保存为 **GBK + CRLF**，勿用 UTF-8。
- **被取代**：无（落地 ADR-3 的「bat 用 GBK」约定）。






---

## ADR-10 新增 sim_concentrator 模拟集中器模块：统一用 adapter_10376，独立可读写串口，REST/CLI 验证任务闭环

- **日期**：2026-08-13
- **状态**：✅ 生效
- **决定**：
  - 在侦听台仓库内新增**独立模块 `sim_concentrator/`**（模拟集中器上位机），不侵入 listener 现有采集流程，也不依赖 GW-CASS 工程。
  - **帧格式统一走 `parser_lib.adapters.adapter_10376`**（Q/GDW 10376.2 信封：AFN/SEQ/RTUA/MSAA/PW + 用户数据 + 嵌套 645/698）：`frame_codec.build_13762_frame` 构帧、`QGDW103762Adapter.decode` 解析，保证构帧与解析口径一致（不混用 GW-CASS BasicFeature 的 DL/T 1376.2 结构）。
  - **串口通道可读写**（`serial_io.SerialIO`：读线程按 1376.2 帧结构切帧入队 + 写锁发送），区别于 listener 的只读监听。
  - **应答引擎**（`responder.Responder`）：内置常见 AFN 应答规则表 + 验证任务可传入覆盖规则；模块上行帧 → 自动回下行帧。
  - **验证闭环**（`runner.execute_task`）：下发 → 接收 → 匹配（`matcher.match_frame`，按 AFN/信封字段/嵌套字段断言）→ 解析 → 逐步判定 Pass/Fail → 汇总结论 JSON。
  - **对外入口**：FastAPI 子应用（`api.create_simcon_app`，独立端口 8781，可挂载到侦听台 create_app）+ CLI（`python -m sim_concentrator verify <task.json>`），共用同一执行核心。
  - **填上 loghooks 预留的第三来源**：`loghooks/sources.py` 的 `parse_concentrator_10376` 由占位（返回 None）改为真实现，接入 `sim_concentrator.frame_codec.decode_frame`。
- **理由**：
  - 需求（用户 2026-08-13）：① 模块上行数据，模拟集中器可主动应答；② 模拟集中器下发数据，能匹配接收并解析；③ 支持插入方法自动化验证流程——AI 烧录后把待验证方法交给工具，工具返回结论。
  - 用户确认：帧格式统一用 adapter_10376；部署在侦听台内独立模块；AI 验证接口用 HTTP REST API；应答逻辑内置模板 + 用例覆盖结合；结论粒度逐步判定 + 汇总。
  - GW-CASS 的 `docs/13762构帧测试工具设计.md` 只规划了 CLI 构帧工具（4 个脚本均未实现），且其 `web_gateway` 自动化运行中 tx_hex 被禁、无按用例下发+匹配+判定闭环，不适合作为 AI 验证入口；侦听台侧 adapter_10376 + loghooks 预留位更贴近烧录验证闭环。
- **影响**：
  - 新增 `sim_concentrator/`：frame_codec / serial_io / responder / matcher / runner / api / cli + 测试（35 用例）。
  - `loghooks/sources.py`：`parse_concentrator_10376` 占位 → 真实现（+ 6 测试）。
  - 仓库 pytest 全绿无回归（loghooks + sim_concentrator = 62 用例）。
  - 依赖：复用 parser_lib（adapter_10376）、pyserial、fastapi、uvicorn；不新增第三方。
  - 后续扩展：内置应答规则表按需补充；验证任务 JSON 格式见 `docs/模拟集中器验证工具使用手册.md`。
- **被取代**：无（新增模块，不取代既有 ADR）。

---

## ADR-11 module_log 启动脚本编码修正回 GBK（ADR-9 落地补漏）+ test_launcher 断言同步

- **日期**：2026-08-13
- **状态**：✅ 生效
- **决定**：
  - 将 `module_log/启动模块日志.bat` 从 **UTF-8 + chcp 65001** 改回 **GBK + CRLF**（符合 ADR-9：bat 一律 GBK），并移除 `chcp 65001` 行——GBK 编码下 cmd 默认代码页 936 即可正确解析中文，`chcp 65001` 反而会让 GBK 字节在 UTF-8 代码页下乱码。
  - 同步更新 `listener/test_launcher.py` 的 `test_module_launcher_bootstraps`：原断言（`python -m venv` / `requirements.txt` / `module_log.run`）对应源码直跑旧逻辑，已被提交 9e84f2e 改为「启动 dist 内 exe」取代；新断言匹配实际脚本（检查 `dist\模块日志\模块日志.exe`、`build_exe.bat`、`start ""` 启动 exe）。
  - 本补充记录**不取代 ADR-9**，仅落地 ADR-9 在 module_log 启动脚本上的遗漏。
- **理由**：
  - 全量 pytest 中 `listener/test_launcher.py` 2 个用例失败：① bat 是 UTF-8 导致 `read_text(encoding="gbk")` 抛 UnicodeDecodeError（编码偏差）；② 编码修复暴露后，`test_module_launcher_bootstraps` 断言的是 9e84f2e 之前的源码直跑逻辑（测试过期）。
  - 修复编码后 cmd 双击/菜单调用时中文路径匹配正常（与 `启动工具.bat`、`listener/启动侦听台.bat` 一致）。
- **影响**：
  - `module_log/启动模块日志.bat`：UTF-8 → GBK（CRLF、无 BOM、无 chcp 65001）。
  - `listener/test_launcher.py`：module 用例断言更新为「启动 exe」语义。
  - 全量 pytest：`398 passed, 66 skipped`（原 396 passed + 2 failed 全部修复）。
- **被取代**：无（补充 ADR-9）。

---

## ADR-12 OpenViking 记忆后端 embedding/VLM 切换到火山方舟（doubao-embedding-vision + doubao-seed-code）

- **日期**：2026-08-13
- **状态**：✅ 生效
- **决定**：
  - 工作区 `D:\2-侦听台改造\ov.conf` 的 OpenViking 记忆后端配置从 **Ollama（nomic-embed-text）** 切换为**火山方舟**：
    - `embedding.dense`：`provider=volcengine`，`model=doubao-embedding-vision`，`api_base=https://ark.cn-beijing.volces.com/api/coding/v3`，`dimension=2048`，`input=text`。
    - `vlm`：`provider=volcengine`，`model=doubao-seed-code-250915`，同一 `api_base` 与 API Key。
    - API Key 为 Coding Plan 的 `ARK_API_KEY_PLACEHOLDER`（Coding Plan 不含通用模型/通用 embedding，`doubao-embedding-vision` 与 `doubao-seed-code` 系列在 coding/v3 下可用，实测验证）。
  - server 启动方式：`openviking-server.exe --config "D:\2-侦听台改造\ov.conf" --host 127.0.0.1 --port 1933`（用 `--config` 指向工作区配置，因沙盒不能写 `~/.openviking/`）。
  - Reasonix 接入：`reasonix.toml` 的 `[[plugins]] openviking` HTTP MCP 指向 `http://127.0.0.1:1933/mcp`。
- **理由**：
  - 原 Ollama embedding 不可用：本机 Ollama 未运行且未安装（11434 connection refused），OpenViking 记忆写入/提取实际失败。
  - 用户明确要求接入火山大模型的向量模型，并提供 Coding Plan 凭据；实测 `doubao-embedding-vision` 返回 2048 维向量（中文经 UTF-8 body 正常），`doubao-seed-code-250915` 对话可用。
  - 仅配 embedding 不够：OpenViking 的 remember 记忆提取流程依赖 vlm（LLM）生成摘要/抽取，vlm 不可用则消息不落库、检索不到，故 vlm 一并切换。
- **影响**：
  - `ov.conf`：embedding 与 vlm 均改为 volcengine provider + coding/v3 base_url + Coding Plan Key；dimension 与既有向量集合对齐（2048）。
  - 端到端验证通过：remember 写入中文事实 → vlm 提取出 `memories/preferences/小明/向量模型偏好.md` → embedding 向量化 → `find` 语义检索 66% 命中；验证产生的测试记忆已用 forget 清理。
  - 服务器需常驻运行（当前由后台 job bash-3 保持，PID 7364）；机器重启后需重新启动（`启动工具.bat` 或手动命令）。
- **被取代**：无（新增决策）。

---

## ADR-13 模拟集中器前端可视化：module_log 新增第三页签「模拟集中器」，挂载 simcon 子应用

- **日期**：2026-08-13
- **状态**：✅ 生效
- **决定**：
  - 在 module_log（模块日志/烧录，8766）前端新增**第三页签「模拟集中器」**（页签栏：实时日志 / 对照解析 / 模拟集中器），把 sim_concentrator 的 AI 验证能力可视化：AI 可继续通过 REST/CLI 接管串口，同时人工能看到并操作同一串口。
  - 后端：`module_log/app.py` 的 `create_app` 内 `app.mount("/api/simcon", create_simcon_app(prefix=""), name="simcon")`，挂载 sim_concentrator 子应用；子应用路由用**相对路径（prefix=""）**避免 mount 前缀 + 路由前缀双前缀。
  - `create_simcon_app` 增加 `prefix` 参数：默认 `/api/simcon`（独立运行 8781 不变），挂载时传 `""`。
  - sim_concentrator API 增强：`open` 改为 POST body（`OpenSpec`）、open/verify 串口异常统一转 409 HTTP 错误（前端 alert 弹窗展示可读 detail）。
  - 新增 `sim_concentrator/__main__.py`，补上 CLI 入口，`python -m sim_concentrator verify/responders/ports` 可用。
  - 前端第三页：串口控制（端口/波特率/打开/关闭/状态）、应答规则列表（/api/simcon/responders）、验证任务 JSON 编辑 + 执行 + 逐步结论渲染（/api/simcon/verify）。
  - 打包：`packaging/module_log.spec` hiddenimports 补 sim_concentrator 全部子模块 + parser_lib.adapters.adapter_10376，**excludes 移除 parser_lib**（simcon 依赖其构帧/解析）。
- **理由**：
  - 用户需求：AI 接管串口的同时，人工需要可视化界面；用户要求集成到模块日志/烧录的第三页签。
  - 挂载子应用（而非并入路由）保持 sim_concentrator 独立可测（延续 ADR-10 独立模块定位）。
  - 串口冲突不做后端互斥锁：调用失败后端返回 409、前端 alert 弹窗提示即可（用户确认）。
  - spec 排除 parser_lib 是 module_log 未依赖它时的旧设定，simcon 引入 adapter_10376 依赖后必须移除（否则桌面 exe 第三页会崩）。
- **影响**：
  - `module_log/app.py`（挂载）、`module_log/static/module-serial.{html,js,css}`（第三页签，JS v4）、`sim_concentrator/api.py`（prefix + OpenSpec + 409）、`sim_concentrator/__main__.py`（新增）、`packaging/module_log.spec`（hiddenimports/excludes）、`module_log/test_module_serial_frontend.py`（+4 前端测试）。
  - 测试：全量 pytest 402 passed / 66 skipped（+sim_concentrator 35 +前端 4）；node --check 前端 JS 通过。
  - 桌面 exe 需重新打包才含第三页签；网页模式立即生效。
- **被取代**：无（补充 ADR-10 的可视化界面）。

---

## ADR-14 仓库目录结构两阶段迁移（apps/libs 分层 + 归组）

- **日期**：2026-08-14
- **状态**：✅ 生效
- **决定**：
  - 依据 `docs/01-第一待开发需求/AI闭环平台项目设计需求文档.md` §6.2.3 目标布局，两阶段一并执行（`git mv` 保留历史，未移动的包不改 import）。
  - **阶段一（归组非代码项）**：
    - `侦听台文档/` → `docs/协议/`（南网/国网子目录）、`oad_todo.md` → `docs/oad_todo.md`，交叉引用全量同步。
    - `scripts/` + `packaging/` → `tools/scripts/` + `tools/packaging/`（spec 的 `ROOT` 解析、bat 相对路径、冒烟脚本路径同步）。
    - 运行日志 `LOG/` → `data/logs/`（非 frozen 形态；frozen 仍为 exe 同目录 `LOG/`），listener/module_log/loghooks 的 `_log_dir()` 与默认落盘路径同步，本地历史日志随目录移动保留现场。
    - `data/graphify-out/` 76 个跟踪文件 `git rm` 清理，`.gitignore` 追加 `data/graphify-out/`。
  - **阶段二（代码包分层）**：`listener/`、`module_log/` → `apps/`；`shared/`、`parser_lib/`、`loghooks/`、`sim_concentrator/` → `libs/`。
    - 依赖改造采用 **sys.path 注入、导入名不变**（~131 处 `from shared/...`、`from sim_concentrator/...` 零改动）。
    - 注入点：`conftest.py`、`apps/*/run.py`、`apps/*/desktop.py`、`apps/module_log/flash_module.py`、`libs/*/__main__.py`，收敛到 `shared.infra.ensure_paths()`（仓库根 + apps/ + libs/）。
    - PyInstaller spec：`pathex` 同时含 apps/ 与 libs/；`datas`（static、DLL）、入口脚本路径按新布局；`ROOT` 解析为 `SPEC_DIR.parent.parent`。
    - 启动脚本（`启动工具.bat`、`apps/*/启动*.bat`）注入 `PYTHONPATH=apps;libs`（`python -m` 需在模块解析前拿到包位置）；`DLL.sln` 的 C# 工程路径改 `libs\shared\dll\...`；CWD 相对路径的测试（`test_ui_layout` 等）改为 `__file__` 相对。
- **理由**：
  - 文档 §6.2 目标布局要求应用/库分层与目录归组；用户确认阶段二与阶段一**一并执行**（原文档要求 platform 落地后执行）。
  - 采用 sys.path 注入而非改写 131 处导入，迁移风险集中在入口/配置层，import 名保持不变，回归面最小。
- **影响**：
  - README「根目录速览」表、目录树、启动/构建/测试章节同步；设计文档 §6.2.3/6.2.4 更新为已执行状态。
  - 测试基线：本 WSL 无 DLL 环境 326 passed / 66 skipped / 9 DLL 失败（环境基线）；Windows + C# DLL 终验应为 402 passed / 66 skipped。
  - `python -m listener.run` / `module_log.run` / `loghooks scan` / `sim_concentrator verify` 冷启动验证通过。
- **被取代**：无（新增决策）。
