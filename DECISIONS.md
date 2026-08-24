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
| 15 | 需求文档整理：docs/需求管理/ 汇总「已完成/待完成」两文档，需求类文档移入归档 | ✅ 生效 |
| 16 | docs 文档按 协议/需求/需求设计方案/总设计框架/开发与运维 五类归类 | ✅ 生效 |
| 17 | FR-5/FR-6 落地：新增 workbench 统一工作台包（包名避开标准库 platform 冲突），编排层 + 统一后端 + 页签式 SPA | ✅ 生效 |
| 18 | workbench 前端合并 + 后端代理（方案①）：ASGI 前缀代理挂 /api/listener、/api/module-serial + 前端页面复制改前缀，根治页签空白；文件选择器 tkinter 故障降级 PowerShell | ✅ 生效 |
| 19 | 项目级安装 UI/UX 设计技能（ui-ux-pro-max + ui-styling + design-system）到 .agents/skills/，仅免费且面向前端/软件 UI 的三个，不装品牌营销类 | ✅ 生效 |
| 20 | Windows 串口网关采用原始 TCP + HTTP 控制（D:\019-wy-tool\uart_to_tcp），同一 COM 严格独占，XMODEM 为唯一 Windows 侧业务例外 | ✅ 已确认，待实现 |
| 21 | loghooks 引擎本体 Evidence 化：可插拔 on_event 发射器 + Event.to_evidence（延迟 import），保持引擎与 test_automation 解耦，取代有损 dict 代理路径 | ✅ 生效 |
| 22 | 任务5协议专项收口：E2/E3/E4 解析修复（问题清单7项 + B-01残留3项）+ 性能基线 + 异常恢复/发布冒烟；OAD/OI 覆盖（18→515）标后续 | ✅ 生效 |
| 23 | 任务4验证UI证据下钻：evidence_detail()（Report 完整证据明细按 source 分组）+ workbench.html「④ 证据下钻」面板（details 展开 payload/metadata）；运行恢复/取消Run/打包验收待续 | ✅ 生效 |
| 24 | 任务4取消Run：submit() 后台线程异步 + cancel() 协作式取消（threading.Event 步骤间检查）+ CANCELLED 终态 + POST /api/run/{id}/cancel + _executor() 单例修复 + 前端轮询/取消按钮 | ✅ 生效 |
| 25 | 任务4剩余：restoreLastRun() 刷新恢复 + store 时间戳毫秒化（排序稳定）+ check_assets.py 打包静态完整性门禁（B-03 自动化部分）；真机 DLL 留 Windows | ✅ 生效 |
| 26 | 任务4 B-03 真机打包验收完成（Windows）：PyInstaller 打包 dist/工作台/（含 C# DLL/scenarios/loghooks）+ smoke_test_workbench_packaged.py headless 冒烟 9/9；B-03 阻塞解除，任务4 完成 | ✅ 生效 |
| 27 | 修复 module-serial 页「启动串口无反应」：删除对不存在元素 ms-refresh-speed 的绑定 + check_module_serial_ids.js 静态校验防回归 | ✅ 生效 |
| 28 | workbench 开放 0.0.0.0 局域网监听（局域网内设备可访问工作台页与 AI 控制面）；已知风险：页面操作接口无鉴权 | ✅ 生效 |
| 29 | 新增 `.agents/skills/ai-control-plane/` skill：AI 调用 AI 控制面 `/api/ai/v1` 的操作 playbook | ✅ 生效 |
| 30 | P1 AI 观察接口与有界证据：module literal/regex/loghook/sequence/not_seen/cursor_range + listener index 固定 cursor_range 复合深链 + 422/幂等/安全输入边界 + HPLC_TEST_DATA_ROOT fixture 根 | ✅ 生效 |
| 31 | P2 项目内 `observe-workbench-logs` 观察技能：显式调用、六命令、默认 dry-run、Token 唯一 WORKBENCH_AI_TOKEN、不碰硬件/无控制操作 | ✅ 生效 |
| 32 | P3 串口 Profile Store：四槽（cco/sta/listener/simcon）默认禁用、原子保存、从 serial_ports.json 回填默认参数 | ✅ 生效 |
| 33 | P4 串口 Profile 一键应用：SerialProfileApplier 固定顺序（侦听台→CCO→STA→模拟集中器）+ 逐槽状态/部分成功 + GET/PUT/apply 三接口 | ✅ 生效 |
| 34 | P5 左侧分组导航（验证/设备/维护）+ hash 路由 + 抽屉/折叠 + iframe 保活保留 | ✅ 生效 |
| 35 | P6 串口配置页：四槽保存/一键应用/刷新状态、内联校验、错误摘要 | ✅ 生效 |
| 36 | 按中央信标周期 + 网络隔离(NID+CCO MAC)的网络承载能力评估：纯 Python 信标识别 + 三级健康评级 + listener/workbench 双挂 + AI 查询 API | ✅ 生效 |

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

---

## ADR-15 需求文档整理：docs/需求管理/ 汇总 + 归档

- **日期**：2026-08-14
- **状态**：✅ 生效
- **决定**：
  - 新增 `docs/需求管理/已完成需求.md` 与 `docs/需求管理/待完成需求.md` 两份汇总文档，作为需求现状的唯一入口（按状态二分类）。
  - 将散落的需求/待办/问题文档移入 `docs/需求管理/归档/`：`任务交接需求与进度表.md`、`代办事务.md`、`oad_todo.md`、`分钟采集帧结构_待确认知识点.md`、`分钟采集问题清单_待AI确认.md`。
  - 交叉引用同步：`AI闭环平台项目设计需求文档.md`、`一键打包发布方案.md`、`tools/scripts/analyze_oad_coverage.py` 的路径更新到归档位置。
  - 归档正文与 `docs/superpowers/plans/` 历史计划正文不修改（保留原始记录）。
- **理由**：需求/待办信息分散在多个交接式文档中，需要按「已完成 / 待完成」两个状态集中呈现，便于后续跟踪；原文档保留为归档底稿。
- **影响**：需求现状以 `docs/需求管理/` 两份汇总文档为准；归档目录保留全部历史细节，供追溯。
- **被取代**：无（新增决策）。

---

## ADR-16 docs 文档按 协议/需求/需求设计方案/总设计框架/开发与运维 五类归类

- **日期**：2026-08-14
- **状态**：✅ 生效
- **决定**：
  - `docs/` 目录按五类整理：`总设计框架/`、`需求设计方案/`、`协议/`、`需求管理/`、`开发与运维/`。
  - **总设计框架/**：`AI闭环平台项目设计需求文档.md`（PRD + 总体设计，顶层文档）由 `docs/01-第一待开发需求/` 移入。
  - **需求设计方案/**：`loghooks-design.md`、`一键打包发布方案.md`、`安徽分钟采集帧结构_代码差异分析.md`、`platform-一体化工作台详细设计.md`，及原 `docs/superpowers/specs/` 两份设计（`2026-08-03-exe-packaging-design.md`、`2026-08-13-wsl-dev-split-design.md`）。
  - **协议/**：原有 国网/南网协议、报文格式、DLL 接口说明 等；新增移入 `安徽分钟采集帧结构编程参考手册.md`（位级帧结构定义）。
  - **需求管理/**：保持既有「已完成/待完成 + 归档」结构不变。
  - **开发与运维/**（新类）：`WSL 开发环境使用手册.md`、`开发指南.md`、`使用说明.md`、`module-serial-usage.md`、`模拟集中器验证工具使用手册.md`、`performance-analysis.md`、`loghooks-source-scan-prompt.md`，及原 `docs/superpowers/plans/` 全部实施计划（`2026-07-25-*`、`2026-08-02-*`、`2026-08-03-exe-packaging.md`）。
  - 原 `docs/superpowers/`、`docs/01-第一待开发需求/` 目录已清空删除；`docs/pic/` 保留为图片资源（供 `使用说明.md` 引用）。
  - 全部被移动文档的交叉引用（docs 内 + README）已同步到新路径；`使用说明.md` 图片相对路径 `.\pic\` 改为 `../pic/`。
- **理由**：docs 根目录散落 30+ 文档，需按文档性质归组，便于检索与维护；用户指定「协议、需求、需求设计方案、总设计框架」四类，四类之外的手册/指南/实施计划另立「开发与运维」类。
- **影响**：所有 `docs/...` 相对路径引用需以新五类目录为准；`DECISIONS.md`、`.superpowers/sdd/` 等历史记录中的旧路径按「归档正文不修改」原则保留原样。
- **被取代**：无（新增决策）。

---

## ADR-17 FR-5/FR-6 落地：workbench 统一工作台包（包名避开标准库 platform 冲突）

- **日期**：2026-08-15
- **状态**：✅ 生效
- **决定**：
  1. **新增 `apps/workbench/` 包**（而非详细设计文档原定的 `apps/platform/`）：Python 标准库自带 `platform` 模块，而 `apps/` 始终位于 `sys.path` 最前（`conftest.py` / `shared.infra.ensure_paths()`），带 `__init__.py` 的常规包 `platform` 会**遮蔽标准库**，导致 uvicorn/fastapi 内部 `import platform` 崩溃。已实测验证：命名空间包（无 `__init__.py`）不遮蔽、常规包遮蔽。故包名改用 `workbench`（语义贴合"验证工作台"），详细设计文档中的 `platform` 统一理解为产品名而非包名。
  2. **FR-5 落地于 `workbench/orchestration/`**（无 UI 依赖，CLI/REST/AI 三端复用）：
     - `models.py`：Run 抽象（FR-5.1）+ 统一报告 Report Schema（FR-5.2，三源归一 module_log/listener/sim_concentrator）。
     - `store.py`：RunStore —— `data/runs.sqlite` 元数据 + `data/reports/{run_id}.json` 报告归档（frozen 落 exe 同目录）。
     - `scenarios.py` + `scenarios/*.json`：场景模板库（期望流程 + 激励任务 + 监控规则集绑定），4 个场景（分钟采集/入网/拉合闸/搜表）。
     - `compare.py`：期望流程比对器（FR-5.3）——hit/missing/timeout/out_of_order/negate 五类差异。
     - `feedback.py`：归因规则引擎（FR-5.4）——JSON 可配置规则表，失败→归因→修复→再验证。
     - `runner.py`：RunExecutor 全链路（flash→monitor→stimulus→compare→feedback→report），每步可 `skip_*` 跳过，全部复用 loghooks 引擎与 sim_concentrator runner（FR-6.4 零重实现）。
  3. **FR-6 落地于 `workbench/app.py` 统一 FastAPI**：`create_workbench_app()` 挂载 `/module-serial`（module_log 含内部 simcon）与 `/listener`（listener 依赖 C# DLL，挂载失败自动降级 `listener_mounted=false`，不拖垮整体）+ 编排路由（`/api/run`、`/api/scenarios`、`/api/compare`、`/api/feedback`、`/api/runs`）+ 页签式 SPA 静态外壳；`run.py` 启动 8790、`desktop.py` pywebview 单窗口（1440×900）。
  4. **前端**：`static/index.html` + `app.js` + `styles.css` 页签注册表（验证工作台/模块日志/侦听台），验证工作台页签为 `static/workbench.html`（场景选择 → 一键 Run → 报告链接 → 历史 Runs）。
  5. **场景规则过滤语义**：`monitor.rules` 引用（如 `["common","provinces/anhui"]`）按 `scope`/`province` 匹配规则（非 `id` 前缀匹配），与 loghooks 规则文件的 `scope: common|province`、`province: anhui` 字段对齐。
- **理由**：FR-5/FR-6 是需求文档 v1.1 的最终形态（一个程序全链路闭环），用户确认全面铺开；包名冲突为硬性技术约束，必须规避；编排层无 UI 依赖是 CLI/REST/AI 三端复用的前提。
- **影响**：`python -m workbench.run` 启动 8790 一体化工作台（双模式并存，8765/8766/8781 独立服务保留）；Run 报告按 run_id 归档可回溯；全量 pytest 回归不破（291 passed / 66 skipped，排除依赖 C# DLL 编译产物的既有 listener/shared 测试）。
- **被取代**：无（新增决策）。

---

## ADR-18 workbench 前端合并 + 后端代理（方案①，根治子应用页签空白）

- **日期**：2026-08-15
- **状态**：✅ 生效
- **决定**：
  1. **放弃"响应重写中间件"方案**（原 `_with_static_rewrite` 拦截 HTML 改写 `/static/`）：只处理了 HTML 的 src/href，**JS 内的 `/api/` 绝对路径未重写**，前端 fetch 打到 workbench 根 → 404 → 侦听台/模块日志页签空白黑色。
  2. **后端改为 ASGI 前缀代理**（`_PrefixProxy`）：listener / module_log 经 mount 挂到 `/api/listener/*`、`/api/module-serial/*`，代理主动剥掉挂载前缀、补回 `/api`、清空 root_path，子应用路由与独立运行时完全一致地命中。前端统一规则：JS 里 `/api/` → `/api/listener/`（listener）、`/api/module-serial/`（module_log）。
  3. **前端页面物理复制**进 `workbench/static/pages/{listener,module-serial}/`（listener 的 index.html/app.js/styles.css、module_log 的 module-serial.html/js/styles.css），复制时 `/api/` 全局替换为对应前缀、`/static/` 改为 `/static/pages/{pkg}/`。页签 iframe 指向复制后页面。原 listener/module_log 独立应用页面**保持不动**（可独立运行，ADR-1/10/13 解耦哲学不推翻）。
  4. **文件选择器降级**：`pick_file_via_tkinter_dialog` 原实现 except 静默吞掉 tkinter 故障返回空串（前端"点了没反应"）；现 tkinter 初始化/弹窗失败时自动降级到 `pick_file_via_native_dialog`（PowerShell 原生对话框）。PyInstaller hook 已自动收集 Tcl/Tk 数据（`_tcl_data`/`_tk_data`），frozen 下 tkinter 8.6 初始化正常。
  5. **挂载测试更新**：`test_app.py` 的 `test_module_log_mounted`/`test_listener_mounted` 断言路径由 `/module-serial/api/version`、`/listener/api/version` 更新为 `/api/module-serial/version`、`/api/listener/version`。
- **理由**：用户反馈"侦听台页面空白黑色、无法加载，且要求合并成一个工程而非仅打包"。方案①（前端合并 + 后端代理）保持底层包独立（不推翻 ADR-1/10/13）、根治前端 API 前缀错配、替代脆弱的响应改写，是符合微前端聚合标准形态的做法（页面聚合 + 反向代理）。
- **影响**：workbench 三页签（验证工作台/模块日志/侦听台）经复制页面 + 前缀代理完整可用；listener/module_log 独立应用不受影响；文件选择器在 frozen 下 tkinter 故障自动降级 PowerShell；全量 pytest 回归 414 passed / 66 skipped / 0 failed（排除依赖 C# DLL 编译产物的既有 listener/shared 测试）。
- **被取代**：无（新增决策；原 ADR-17 的"挂载 + 静态重写"表述被本决策细化修正）。


## ADR-19 项目级安装 UI/UX 设计技能（.agents/skills/）

- **日期**：2026-08-15
- **状态**：✅ 生效
- **决定**：在项目根新建 `.agents/skills/`，仅安装三个免费且面向前端/软件 UI 设计的技能：
  - `ui-ux-pro-max`（核心，v2.13.0，MIT）：79 风格/192 配色/74 字体配对/119 UX 指南/25 图表/22 技术栈，含 `--design-system` 设计系统生成器；
  - `ui-styling`（MIT，claudekit）：shadcn/ui + Tailwind + canvas 实现层技能；
  - `design-system`（MIT，claudekit）：三层 design token（primitive→semantic→component）+ Tailwind theme 配置。
  不安装 `banner-design`/`brand`/`design`/`slides`（官方划为高级版付费内容，且属品牌营销向，与前端/软件 UI 无关）。
  来源：`nextlevelbuilder/ui-ux-pro-max-skill`（main 分支，对应 v2.13.0），从 `.claude/skills/` 对应目录原样复制。
- **理由**：项目 workbench 前端（页签式 SPA + 数据可视化）需要专业 UI/UX 设计指导；用户要求免费、专注前端/软件 UI，科研/数据类风格可经 `--design-system` 组合查询获得。
- **影响**：`D:/2-侦听台改造/.agents/skills/` 下新增 3 个技能目录（195 文件）；通用 agent 标准目录（Claude Code 2.x/Codex 等）可加载；搜索脚本依赖 Python 3.x 标准库，无外部依赖；`.gitignore` 未忽略 `.agents/`，技能文件随仓库入库。
- **被取代**：无（新增决策）。

---

## ADR-20 Windows 串口网关采用原始 TCP + HTTP 控制

- **日期**：2026-08-17
- **状态**：✅ 已确认，待实现
- **决定**：
  - 在 `D:\019-wy-tool\uart_to_tcp` 新建带简洁窗口和轮转日志的 Windows 网关。
  - 普通字节走原始 TCP；枚举、租约、状态、配置和烧录走带令牌 HTTP。
  - WSL 增加 local/windows_tcp，覆盖全部串口入口；旧请求默认 local。
  - 同一 COM 严格独占；断线失败并释放，不自动重连或续跑。
  - XMODEM 是唯一 Windows 侧业务例外，共享路径只读固件，校验 size/SHA-256 后执行。
  - 仅限同机；不使用 RFC2217、虚拟 COM/PTY、COM 共享或局域网访问。
- **理由**：真实串口在 Windows；普通链路应透明，时序敏感的 XMODEM 放在 Windows 更稳定。

---

## ADR-21 loghooks 引擎本体 Evidence 化：可插拔 on_event 发射器 + Event.to_evidence（保持解耦）

- **日期**：2026-08-17
- **状态**：✅ 生效
- **决定**：
  - `libs/loghooks/engine.py` 新增 `Event.to_evidence(run_id="")`：Event → `test_automation.Evidence`（kind=event, source=loghooks），payload 含全字段（captures/漂移/level/source 等），metadata 携带 `origin=loghooks.engine` + `source_line` + `source_line_idx`。**延迟 import `test_automation.models.Evidence`**，引擎本体不硬依赖 test_automation。
  - `Engine(rules, source, on_event=None)` 新增可插拔发射器：每产出一条 Event 即回调 `on_event(event)`；为 None 时行为与旧版完全一致（内部统一走 `_emit()` 登记 + 回调）。
  - `apps/workbench/orchestration/runner.py:_scan_logs` 改用引擎发射器 + `Event.to_evidence()` 直接收集完整 Evidence（`scan["evidence"]`），取代此前「Event 降维成 7 字段 dict → `_dict_to_loghooks_event` 代理包装」的**有损路径**；`scan["events"]`（dict）保留供 `compare_flow` 比对（契约不变）。
  - `apps/workbench/orchestration/evidence.py:collect_three_source_evidence` 的 events 分支支持双形态：已 Evidence 对象（`type(...).__name__ == "Evidence"`）直接 `sink` 写入；dict/Event 仍走 `LoghooksEventAdapter` 适配路径。
- **理由**：任务 3 剩余项「loghooks 引擎本体 Evidence 化」要求引擎产出的 Event 直接可转 Evidence；但直接让引擎 import test_automation 会引入反向依赖，违背 ADR-1/10/13 解耦哲学。可插拔发射器 + 延迟 import 让引擎保持零 test_automation 依赖，由调用方决定是否/如何转 Evidence，同时消除有损 dict 代理路径（证据字段无损、可下钻）。
- **影响**：`pytest apps/workbench/orchestration/test_evidence.py` = 21 passed（新增 4）；`pytest libs/loghooks libs/test_automation libs/sim_concentrator apps/workbench` = 187 passed；`pytest libs apps` = 563 passed / 66 skipped（无回归）。任务 3 至此全部收口（docs/04-任务安排.md 任务 3 = ✅ 已完成）。
- **被取代**：无（新增决策；docs/12 §8 原「引擎本体改造延后」表述被本决策收口）。

---

## ADR-22 任务5协议专项收口：E2/E3/E4 解析修复 + 性能基线 + 异常恢复

- **日期**：2026-08-17
- **状态**：✅ 生效
- **决定**：
  1. **分钟采集问题清单 7 项全部处理**（`libs/parser_lib/adapters/adapter_dualmode`、`adapter_10376`）：
     - 确定性 bug：E3 字节6 位宽统一为 3+2+3（协议类型3bit `&0x07`、电表类型2bit `(>>3)&0x03`，与 C 侧一致，残留③随此解决）；E3 冻结时刻按小端 BCD 反转解码（与 E4 主动上报一致）；E2 下行删除多余"方向"字段（字节1 bit4~7 为保留）；10376 `_DUALMODE_MESSAGE_NAMES` 补 00E2/E3/E4 注册。
     - 新实现：E2 上行应答解析（报文头长度 15 → 按手册 §2.2 展开：电表MAC/任务号/启动删除/结果位/周期）；E4 并发抄读格式展开（启动位=0，报文头长度 23 → 按 §4.1 展开：协议/电表/响应结果/源MAC/任务号/冻结时刻/报文条数/转发数据长度 + 报文内容递归解内嵌帧）。
  2. **B-01 残留 3 项关闭**：① E4 主动上报 result≠0 断言口径——统计层"数据区非空视为成功，不校验结果码"，result 仅作诊断（真机验证行为不变）；② E2 删除应答判定——删除下发只计数，删除后仍上报照常计入周期；③ E3 位宽标注统一随①修复。均固化测试。
  3. **性能基线可复现**：`apps/listener/test_perf_baseline.py` 5 条查询路径基线（浅翻页/keyset深翻页/时间范围COUNT/query筛选/nid筛选），5万行合成数据实测远优于 2026-08-09 旧基线（23万行 167/221/122ms → 现 6.7/3.8/3/14/27.5ms）。
  4. **异常恢复+发布冒烟**：RunExecutor 中途异常→failed 终态+report 含 fail assertion 可下钻；无串口环境 skip 降级跑完整 Run 冒烟。
  5. **OAD/OI 覆盖（18→515）标记为任务 5 后续项**（用户决定单独立项，2026-08-17）。
- **理由**：B-01 已解除（协议口径经 C 侧 aps_stack.c/aps_stack.h + 真实日志帧交叉验证），任务 5 可离线推进；问题清单 7 项与残留 3 项是协议正确性缺口，性能基线是验收出口"可复现"的硬要求；OAD/OI 覆盖是 500+ 条数据工程，不宜与协议修复混做。
- **影响**：`pytest libs/parser_lib libs/minute_assert` = 137 passed；`pytest apps/listener/test_perf_baseline.py` = 5 passed；`pytest apps/workbench/orchestration/test_evidence.py` = 23 passed；全量 `pytest libs apps` = 577 passed / 66 skipped（无回归）。任务 5 状态：进行中（协议/性能/异常恢复完成，OAD/OI 与真机验证项后续）。
- **被取代**：无（新增决策）。

---

## ADR-23 任务4验证UI：证据下钻面板（Report 完整证据明细 + 前端下钻）

- **日期**：2026-08-17
- **状态**：✅ 生效
- **决定**：
  - `apps/workbench/orchestration/evidence.py` 新增 `evidence_detail(store)`：EvidenceStore → 按 source 分组的完整证据明细（每条含 kind/source/sequence/raw_ref/correlation_key/observed_at/payload/metadata），与 `evidence_index`（只暴露 raw_ref 锚点）互补。
  - `runner.py` 把 `evidence_detail` 写入 `Report.evidence_detail`（Report 模型加字段），经既有 `GET /api/run/{run_id}/report` 暴露（无需新端点）。
  - `workbench/static/workbench.html` 的 `renderRun` 在 Run 执行后异步 fetch report，渲染「④ 证据下钻」面板：按 source 分组（loghooks 事件/模拟集中器步骤/侦听台帧），每条 `<details>` 可展开 payload/metadata（递归键值渲染 `renderKeyValue`）。
- **理由**：FR-6 要求"展示运行状态、步骤、断言、证据和报告"。此前 Report 只有 `evidence_index`（raw_ref 锚点），前端无法下钻到证据原始内容；`evidence_detail` 提供完整可下钻字段，`evidence_index` 保持轻量索引不破坏既有契约。
- **影响**：`test_evidence.py` = 26 passed（新增 TestEvidenceDetail 3 用例：完整字段/空/Run 端到端含 detail）；全量 `pytest libs apps` = 580 passed / 66 skipped（无回归）。前端 JS 经 node --check 语法验证通过。任务 4 验证 UI 部分证据下钻完成；运行恢复（刷新后恢复 Run）、取消 Run、打包验收（B-03）待续。
- **被取代**：无（新增决策）。

---

## ADR-24 任务4取消Run：异步执行 + 协作式取消（submit/cancel + CANCELLED 终态）

- **日期**：2026-08-17
- **状态**：✅ 生效
- **决定**：
  1. **Run 执行改异步**：`RunExecutor.submit(run_input)` 在后台线程执行 `_run_steps`，`POST /api/run` 立即返回（状态 running），前端轮询 `GET /api/run/{id}` 获取进度。同步 `execute()` 保留（CLI/测试复用）。
  2. **协作式取消**：`cancel(run_id)` 置 `threading.Event` 取消标志 + 状态转 CANCELLING；`_run_steps` 在 flash/monitor/stimulus/compare/feedback 各步骤前检查标志，已取消抛 `RunCancelled`，外层捕获后落 CANCELLED 终态；Report 标注 `run.cancelled` 断言。
  3. **API**：新增 `POST /api/run/{run_id}/cancel`（200=cancelling，409=不可取消/不存在，404=不存在）。`_executor()` 单例化修复——原实现每次 new `RunExecutor`，submit 与 cancel 落在不同实例导致取消事件丢失（这是本功能踩到的关键坑）。
  4. **前端**：`workbench.html` 的 `run()` 改 `pollRun()` 轮询，运行中显示「取消 Run」按钮 + 状态，终态渲染结果/证据；badge 支持 cancelled（"取消"）。
- **理由**：FR-6 要求"支持取消和错误恢复"。原同步阻塞执行无法在 Run 进行中响应取消请求；异步 + 协作式取消（步骤间检查）不需硬中断正在进行的串口 IO，安全、可测。
- **影响**：`test_app.py` + `test_evidence.py` = 42 passed（新增 TestRunCancel 3 + cancel flow 2，含 CANCELLED 终态/report 标注/409/404）；全量 `pytest libs apps` = 585 passed / 66 skipped（无回归）。任务 4 取消 Run 完成；运行恢复（刷新后恢复 Run）、打包验收（B-03）待续。
- **被取代**：无（新增决策）。

---

## ADR-25 任务4剩余项：运行恢复 + 打包静态资源完整性门禁

- **日期**：2026-08-18
- **状态**：✅ 生效
- **决定**：
  1. **运行恢复（FR-6 刷新后恢复）**：前端 `restoreLastRun()` 页面加载时取最近 Run（`GET /api/runs?limit=1`）——终态渲染结果+证据+徽标，运行中继续 `pollRun` 恢复实时状态/取消按钮；失败静默降级不影响其他功能。
  2. **store 时间戳改毫秒级**：`created_at`/`updated_at` 原 `isoformat(timespec="seconds")` 秒级，同秒多条 Run 时 `list_runs` 倒序不稳定，恢复可能取错 Run；改 `timespec="milliseconds"`（ISO 兼容，前端 `slice(0,19)` 截断不受影响）。
  3. **B-03 静态资源完整性门禁**：新增 `apps/workbench/check_assets.py`——关键资产存在/非空、HTML 引用 `/static/` 资源完整性、空文件检测；`--strict` 退出码非 0 供打包 CI 门禁。真机 DLL 打包与干净机启动冒烟留 Windows 环境（B-03 阻塞解除需干净机证据）。
- **理由**：FR-6 要求"刷新后恢复 Run；DLL/串口缺失不阻断无关能力"。运行恢复是刷新可用性硬要求；时间戳毫秒化是排序稳定性的必要修复；静态资源完整性是本环境（WSL 无 DLL）能自动化的 B-03 部分，提前落地降低真机打包踩坑。
- **影响**：新增 TestRunRestore 1 + B-03 2 测试；全量 `pytest libs apps` = **588 passed / 66 skipped** 无回归。任务 4 除真机 DLL 打包验收（Windows）外全部完成。
- **被取代**：无（新增决策）。

---

## ADR-26 任务4 B-03 真机打包验收完成（Windows）

- **日期**：2026-08-18
- **状态**：✅ 生效
- **决定**：
  1. **真机 Windows 打包**：环境从 WSL 切到原生 Windows 后，DLL 已就位（`libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll`），执行 `PyInstaller --clean --noconfirm tools/packaging/workbench.spec` → `dist/工作台/工作台.exe`（onedir 7.8MB），`_internal/` 含 static（12 资产）、`dll/bin/Debug/GwHPLCAnalysis.dll`、scenarios（4 场景）、loghooks rules。
  2. **打包版启动冒烟**：新增 `tools/scripts/smoke_test_workbench_packaged.py`，`HPLC_NO_GUI=1` headless 启动 exe，验证 health / 首页 / 静态资源 / platform-version / module-serial+listener 子应用代理 / runtime 生成 = **9/9 PASS**。
  3. **B-03 阻塞解除**：任务 4 标记 ✅ 已完成；剩余阻塞仅真机串口实测（B-04，任务 6 已屏蔽）。
- **理由**：B-03 验收出口是"干净机打包与启动冒烟并保存证据"；headless 冒烟脚本即持久化证据，可在任何 Windows 机器复跑验证产物。
- **影响**：任务 4 收尾；打包冒烟脚本纳入 `tools/scripts/` 供回归。真机串口（COM4 集中器/电表）仍待硬件环境。
- **被取代**：无（新增决策）。

---

## ADR-27 修复 module-serial 页「启动串口无反应」：删除对不存在元素 ms-refresh-speed 的绑定

- **日期**：2026-08-20
- **状态**：✅ 生效
- **决定**：
  - **根因**：`03267f7` 引入的 `module-serial.js` 第 470 行 `$("ms-refresh-speed").addEventListener(...)` 所指向的 `<select id="ms-refresh-speed">` 元素在 `b6779da`（实时日志独占页签 UI 重写）时被从 HTML 删除，但 JS 绑定未同步删除。`bind()` 执行到该行时 `$()` 返回 `null`，`.addEventListener` 抛 `TypeError`，导致 `bind()` 中断；`boot()` 中其后的 `refreshPorts().then(ensureDefaultSession)` 不执行，页面无默认 session，用户点「启动」时 `currentSession()` 为 `null` → `if (!session) return;` **静默无反应**（按钮不变化、无事件打印）。
  - **修复**：从 `apps/module_log/static/module-serial.js` 与 `apps/workbench/static/pages/module-serial/module-serial.js`（同源拷贝）两处删除该 `addEventListener` 块；`setRefreshSpeed(DEFAULT_REFRESH_SPEED)` 在 `boot()` 中仍调用，默认 medium 轮询不受影响。
  - **防回归**：新增 `tools/scripts/check_module_serial_ids.js`（Node 静态检查），比对 JS 全部 `$("id")` 引用与 HTML 的 `id` 属性，缺一即非零退出；独立版与 workbench 副本 76 个引用全部有对应元素。
- **理由**：前端「点击启动无反应」是静默失败，无任何错误提示，排查难；根因是 HTML/JS 不同步的历史回归。
- **影响**：修复后 `bind()` 完整执行，默认 session 自动创建，启动串口有正常反馈（按钮变停止 + 事件打印）。相关测试 `test_module_serial_frontend.py` + `test_app.py` = **24 passed** 无回归。
- **被取代**：无（新增决策）。

---

## ADR-28 workbench 开放 0.0.0.0 局域网监听

- **日期**：2026-08-20
- **状态**：✅ 生效
- **决定**：
  - workbench 监听地址由 `127.0.0.1` 改为 `0.0.0.0`（`apps/workbench/run.py`、`apps/workbench/desktop.py` 两处），使局域网内其他设备可访问工作台页面（`http://<本机IP>:8790/`）与 AI 控制面 `/api/ai/v1/*`。
  - 前端全部使用相对路径（`/api/...`），不硬编码 IP，本机访问仍走 `127.0.0.1`，体验不受影响；workbench 已将 `/api/listener`、`/api/module-serial`、`/api/fs`、`/api/loghooks`、`/api/simcon` 全部代理进自身进程（ADR-18），局域网设备无需访问其他端口。
  - AI 控制面 `/api/ai/v1/*` 维持 token 鉴权（能力接口只认 Bearer token；发授权 `/admin/grants` 仍仅限本机 127.0.0.1 + 密钥），不受开放影响。
- **理由**：用户决定让局域网内其他设备（或 AI 客户端）通过本机 8790 使用工作台能力。
- **影响**：
  - **已知风险（已告知用户）**：workbench 的**页面操作接口**（`/api/module-serial/*`、`/api/listener/*`、`/api/fs/*` 等）**无鉴权**（仅有 AI 控制面 `/api/ai/v1/*` 有 token）。开放 0.0.0.0 后，局域网内任何设备可直接打开工作台页面操作真机串口/列文件。用户确认局域网环境可信，接受此风险。
  - 若未来部署到不可信网络，需先为页面接口增加访问口令（HTTP Basic / token），再开放监听。
  - 端口 8790 需在 Windows 防火墙放行，局域网设备方可连接。
- **被取代**：无（新增决策）。

---

## ADR-29 新增 `.agents/skills/ai-control-plane/` skill

- **日期**：2026-08-21
- **状态**：✅ 生效
- **决定**：在项目级 skills 目录 `.agents/skills/` 下新增 `ai-control-plane/` skill（SKILL.md），作为 AI 调用 AI 控制面 `/api/ai/v1` 的**操作 playbook**：拿授权 token（人/密钥）→ 查状态 → 串口会话（ensure/send/stop）→ 烧录 → 观察+取证 → 侦听台控制与帧查询 → 推荐调用顺序 → 与前端关系 → 错误码速查。
- **理由**：AI 需要通过 AI 控制面操作真机（cco/sta 串口、烧录、日志观察取证），但没有一份「AI 可直接执行的步骤说明书」；`docs/16-AI操作指南.md` 是给人看的完整文档，skill 是给 AI 的浓缩执行指引。位置沿用 ADR-19 的 `.agents/skills/` 约定，通用 agent 标准目录可加载。
- **影响**：`.agents/skills/ai-control-plane/SKILL.md` 新增（194 行），frontmatter 含 `name/description/argument-hint/metadata`，13 章节；内容基于 2026-08-20 对全部 AI 工具的实测（授权/状态/审计/会话/发送/观察/取证/烧录校验/侦听台均正常）。不修改任何代码。
- **被取代**：无（新增决策）。

---

## ADR-30 P1 AI 观察接口与有界证据

- **日期**：2026-08-23
- **状态**：✅ 生效
- **决定**：在既有模块日志与侦听台服务上实现**有界 AI observation**：
  - module matcher 支持 `literal`/`live` 兼容、`regex`、`loghook_rule`、`sequence`、`not_seen` 及 `cursor_range`；live absence 在未完成可信窗口时不误报成功（`timed_out + condition_met=false + reason=live_window_unverified`）。
  - listener 支持 index 固定的 `cursor_range`、首尾/越界/裁剪校验、复合 `index_id + frame_id` 深链和 Artifact。
  - observation 的 422 输入映射、路径型字段拒绝、malformed target 预检、Artifact 授权、幂等请求指纹/资源授权和跨资源冲突防泄露全覆盖。
  - 测试基础设施使用 `HPLC_TEST_DATA_ROOT` 优先、旧 `测试文件/` 回退；该根只在 pytest/test helper 消费，不进入生产包，也不移动或复制历史数据。
- **理由**：AI 需要声明式观察三源日志并取回结构化结论，同时必须把输入、安全与幂等边界收紧到可验证的程度（P1 是后续 P2-P8 的接口地基）。
- **影响**：9 个提交（`327a7db..1af6335`），验收离线证据：changed-test 集 194 passed + loghooks 34 + listener index/history 32 + listener app fixture 37 + packaging 14 + unsafe/malformed 定向 3。自动化边界**不证明**真实串口因果、发送、烧录或设备行为。
- **被取代**：无（新增决策）。

---

## ADR-31 P2 项目内 observe-workbench-logs 观察技能

- **日期**：2026-08-23
- **状态**：✅ 生效
- **决定**：新增项目内、显式调用的 AI 观察技能 `skills/observe-workbench-logs/`：
  - 仅提供 `status`/`observe`/`wait`/`artifact`/`listener-schema`/`frame-detail` 六命令；`allow_implicit_invocation=false`，只读观察不碰硬件。
  - Token 唯一来源 `WORKBENCH_AI_TOKEN`，无 CLI 参数/位置参数/配置文件值/URL userinfo/明文输出；`observe` 默认 dry-run 零 HTTP，仅 `--execute` 才 POST `/api/ai/v1/observations`。
  - 客户端无 ensure/start/stop/send/flash/烧录/串口打开/operation cancel/产品文件读取能力；wait 单次服务端 ≤30s、终态即停、不 cancel；Artifact 只读服务端登记对象。
  - 不安装个人技能、不启动服务、不 push、不碰硬件。
- **理由**：AI 改代码后需要声明式验证「真实运行中是否走到」，但技能必须是只读观察、默认零副作用，避免 AI 误触发硬件操作。
- **影响**：`SKILL.md` + `agents/openai.yaml` + `scripts/workbench_ai_client.py`（293 行）+ `references/api-contract.md` + mock 测试 16 passed；`quick_validate` Skill is valid。
- **被取代**：无（新增决策）。

---

## ADR-32 P3 串口 Profile Store（四槽默认禁用）

- **日期**：2026-08-23
- **状态**：✅ 生效
- **决定**：新增 `libs/shared/serial_profile.py` `SerialProfileStore`：
  - 固定四槽 `module_log.cco` / `module_log.sta` / `listener.main` / `simcon.main`，默认 `enabled:false`。
  - 首次无文件返回默认禁用槽、不落盘；`update_slot` 后原子写入 `runtime_dir`。
  - 选 `mapping_id` 从 `serial_ports.json` 回填默认波特率/数据位/校验/停止，可覆盖；`serial_ports.json` 只读不被修改。
  - 非法参数/未知映射/未知槽报错。
- **理由**：统一串口配置需要一份可保存、可校验、不破坏 `serial_ports.json` 现场映射的 Profile 存储，供后续一键应用与配置页使用。
- **影响**：`libs/shared/serial_profile.py` + `test_serial_profile.py` 11 passed（含既有 serial 测试 6 passed 无回归）。
- **被取代**：无（新增决策）。

---

## ADR-33 P4 串口 Profile 一键应用 + REST 三接口

- **日期**：2026-08-23
- **状态**：✅ 生效
- **决定**：
  - `SerialProfileApplier` 固定顺序**侦听台→CCO→STA→模拟集中器**；逐槽状态 `started/reused/stopped/unchanged/skipped/failed`；相同且已运行返回 unchanged/reused；禁用槽仅停托管会话（title 前缀 `托管-`，不影响人工动态会话）；单槽失败继续后续槽，不回滚成功项。
  - `serial_profile_api.py`：`GET/PUT /api/serial-profile`（只保存不碰硬件）+ `POST /api/serial-profile/apply`（只读已保存版本）。
  - `sim_concentrator/api.py` 暴露 `simcon_open_io/close_io`；workbench app 经 `SimconProfileAdapter` 注入 applier（不经 HTTP 回调）。
  - `serial_profile.py` 增 `device_for`（mapping_id→可打开设备）。
- **理由**：四槽串口需要「一键应用」把已保存 Profile 落到真实串口服务，同时要逐槽可观测、部分失败可继续，且托管会话与人工会话互不干扰。
- **影响**：applier 6 + REST 5 = 11 passed；workbench 全量 196 passed 无回归。
- **被取代**：无（新增决策）。

---

## ADR-34 P5 左侧分组导航 + hash 路由 + 抽屉/折叠

- **日期**：2026-08-23
- **状态**：✅ 生效
- **决定**：
  - `app.js` 三组导航（验证/设备/维护）；`ensureFrame` 保活契约保留（首次创建只赋 src，切页只隐藏/显示）；切页写 `location.hash` 支持深链/刷新/前进后退。
  - 桌面端侧栏可折叠为图标栏（localStorage 保存）；窄屏(<860px)改可键盘关闭抽屉（Escape/遮罩关闭、焦点回归）。
  - `index.html/styles.css` 新增 `wb-sidebar` 分组菜单 + `wb-overlay` 遮罩 + 折叠按钮 + `@media` 响应式。
  - 新增 `maintenance.html`（维护组：版本/主题/挂载状态）与 `serial-profile.html` 占位（P6 完善）。
- **理由**：功能页增多后需要分组导航与深链支持，同时保留 P2 起 iframe 保活语义，避免切换页签重载丢失会话。
- **影响**：`test_shell_navigation.py` 7 测试；`node --check app.js` 通过；workbench 全量 203 passed 无回归。
- **被取代**：无（新增决策）。

---

## ADR-35 P6 串口配置页（四槽保存/一键应用/刷新状态）

- **日期**：2026-08-23
- **状态**：✅ 生效
- **决定**：`serial-profile.html/js` 原生静态配置页，固定四槽（CCO/STA/侦听台/模拟集中器）：
  - 保存(PUT)/一键应用(POST apply)/刷新状态(GET) 三动作各自独立。
  - 每槽：启用、映射下拉（`port_details` 提供）、波特率/校验、状态/占用/应用结果。
  - 启用未选串口→内联报错；重复映射 apply 前阻止且零副作用；部分失败保留成功状态，提供可聚焦错误摘要（`tabindex`+focus）。
- **理由**：P4 的一键应用需要一个可视化操作页，让用户在浏览器里完成四槽配置与应用，并给出可访问的失败提示。
- **影响**：`serial-profile.js`（345 行）+ 前端测试 `test_serial_profile_frontend.py`（110 行，8 测试）；`node --check` 通过；workbench 全量 211 passed 无回归。
- **被取代**：无（新增决策）。

## ADR-36 按中央信标周期 + 网络隔离的网络承载能力评估

- **日期**：2026-08-25
- **状态**：✅ 生效
- **决定**：侦听台新增「网络承载能力评估」功能，按 CCO 实际发送的**中央信标周期**（实测值，非固定，随节点数变化，协议 1~10s）分桶评估网络健康度：
  - **网络隔离**：每个网络由 **NID(24bit) + CCO MAC(48bit)** 联合唯一标识（NID 为主键、CCO MAC 二次确认，避免 NID 重号误并）；不同网络互不混算。NID 从 MPDU 帧控制字节 0-3 提取（所有帧通用），CCO MAC 从中央信标载荷（组网序列号后 6B）提取。
  - **信标识别**：纯 Python 模块 `network_assessment.py`（无 DLL 依赖），识别中央信标帧（定界符=0 + 信标类型=2 + 源TEI=1），用日志时间戳求相邻到达间隔得实测周期；信标周期计数(32bit 每周期+1)作辅助校验；识别不出时退化为 SOF 帧簇检测或 fallback 标记不报错。
  - **评级模型**：复用记忆库 B 类三级规则（通信成功率 健康≥98%/亚健康 90~98%/故障<90%；离线率 ≤2%/2~10%/>10%；汇总：全健康=健康、有亚健康无故障=亚健康、有故障=故障，优先处理成功率与离线率）。
  - **接口**：`GET /api/network/assessment`（网络列表+每网络周期分桶+评级汇总）、`GET /api/network/status`（轻量快照，机器可读 healthy/degraded/fault，供 AI 调用查询网络状态）；log_service 未启用 503。
  - **前端**：listener 新增「网络承载评估」页签（原生 JS + canvas 趋势图，深色终端风格），按 ADR-18 同步复制进 workbench（`/api/` → `/api/listener/`）。
- **理由**：用户要求按中央信标周期（非固定值）评估每个周期网络承载能力，并按网络隔离分析（NID 网络内唯一）；记忆库已有 B 类运行指标健康度分级规则可复用。
- **影响**：新增 `network_assessment.py`（extract_nid/extract_cco_mac/scan_beacon_periods/assess_periods/assess_by_network）+ `log_service.py` 抽样与聚合方法 + `app.py` 2 路由 + `test_network_assessment.py`(16 单测) + `smoke_network_assessment.py` + 前端 6 文件（listener 3 + workbench 3）。实测：真实 11.8MB 日志检出 1 网络（NID=6375261, CCO MAC=26-09-13-46-60-00）实测周期 2100ms（独立测量吻合），37 周期分桶成功率 94.59%；NID 297/297 与 DLL 一致；回归 169+ passed。
- **被取代**：无（新增决策）。
