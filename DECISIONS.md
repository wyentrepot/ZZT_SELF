# 决策记录（DECISIONS.md）

本文件采用 ADR（Architecture Decision Record）精简版模式：**决策只追加、不覆盖**。已有记录永不修改、不删除；被取代的只把活动决策表里状态改为「❌ 已取代」，正文不动。

**归档重启规则（源自 req-mgmt 技能流程 G）**：ADR 记录（含已被取代的）累计到 **10 条**时触发归档——整份移入 `archives/DECISIONS-YYYYMMDD-HHmm.md`，新建文件复制末尾 3 条作缓存种子重排为 #1/#2/#3，之后从 #4 起新增。每个生命周期 = 3 条缓存 + 最多 7 条新增。本生命周期自 2026-08-28 起，缓存种子为归档前的 ADR-42/43/44（重排为本文件的 ADR-1/2/3）。

## 活动决策表

| # | 标题 | 状态 |
|---|------|------|
| 1 | 高频采集失败分析收口为工具包 + 技能：tools/scripts/hf_collect_analyze + skills/hf-collect-analysis + 精简样例入库 | ✅ 生效（原 ADR-42；被 #2 部分取代目录位置） |
| 2 | 高频采集分析迁入 tools/taiti/高频采集 扩展系列：按日志来源分 台体/CCO/侦听台 子目录 + run.py 四命令入口 | ✅ 生效（原 ADR-43） |
| 3 | 13762 协议库重构：彻底重构为 Q/GDW 10376.2 单 68 标准帧（推翻双 68 信封，同步改 sim_concentrator/loghooks/测试） | ✅ 生效（原 ADR-44） |
| 4 | DECISIONS.md 启用 10 条归档重启（首次归档 44→3 缓存，生命周期=3 缓存+最多7 新增） | ✅ 生效（2026-08-28） |
| 5 | 模拟集中器用例语义化 + Profile：send 只写 afn/fn+params，全局信息进 profile 共享，构帧交 13762 库 | ✅ 生效（2026-08-28） |
| 6 | 首期架构规范解耦重构（parser facade、编排 ports、canonical/DTO） | ✅ 生效 |
| 7 | canonical 审计模型以兼容投影接入 Workbench | ✅ 生效 |
| 8 | 模拟集中器会话帧日志持久化 + AI 控制面 simcon 接口（verify/step/frames） | ✅ 生效 |

---

## ADR-1 高频采集失败分析收口为工具包 + 技能

- **日期**：2026-08-26
- **状态**：✅ 生效
- **决定**：将临时目录 `_tmp_hf_analyze/` 的两个高频采集失败分析脚本（`analyze_taish.py` / `analyze_cross.py`）正式收口为**项目内可复用能力**，采用「命令行工具包 + 项目技能」双形态：
  1. **工具包**：`tools/scripts/hf_collect_analyze/`（`taish` + `cross` 两个子命令，`python -m hf_collect_analyze` 入口），复用 `libs/parser_lib` 与 `libs/sim_concentrator.frame_codec`，新增单测 `tools/scripts/test_hf_collect_analyze.py`（14 用例）。
  2. **技能**：`skills/hf-collect-analysis/`（SKILL.md + agents/openai.yaml + references/analysis-rules.md + scripts/run_analysis.py 包装入口），显式调用、只读日志文件、不碰硬件，分析口径沉淀在 references。
  3. **精简样例入库**：`tools/scripts/hf_collect_analyze/samples/` 三份样例（台体 GBK / CCO UTF-8 / 侦听台 UTF-8，由原始 8MB/4MB/12MB 日志筛选，共 ~978KB），供单测与演示回归。
- **理由**：用户要求把高频采集分析做成扩展脚本，下次直接用；`_tmp_hf_analyze` 本就是临时目录（README 注明"后续可并入台体报文分析扩展"）。日志筛选这类机械活尝试分发给 codebuddy/opencode（MCP），但当前 opencode provider 未认证模型不可用，改由父代理直接完成。
- **影响**：`_tmp_hf_analyze/` 保留为历史参考（其 README/analyze_taish.py/analyze_cross.py/.gitignore 为既有已跟踪文件，测试数据/精简样例由 .gitignore 排除不入库）；新增 `tools/scripts/hf_collect_analyze/` 与 `skills/hf-collect-analysis/`；REQS-INDEX 登记需求 0006。`analyze_taish.py` 的 `_REPO` 路径改为 `parents[3]`（适配新目录层级），CCO 读取编码由 `utf-8` 改为 `utf-8-sig`（样例带 BOM，原脚本用 `replace` 吞掉 BOM 头仍能跑，收口后更稳健）；taish 收口版删除未使用的 frame_codec import 与死代码 `_decode_addr_from_frame`。
- **被取代**：无（新增决策；`_tmp_hf_analyze` 为临时目录，不取代任何既有 ADR）。

## ADR-2 高频采集分析迁入 tools/taiti/高频采集 扩展系列（按日志来源分目录）

- **日期**：2026-08-26
- **状态**：✅ 生效
- **决定**：将 ADR-1（原 ADR-42）收口的高频采集分析从 `tools/scripts/hf_collect_analyze/` 迁入
  **`tools/taiti/高频采集/`**（台体扩展系列），并按日志来源分类：
  - `台体/` —— 台体日志分析（analyze_taish.py + samples）
  - `CCO/` —— CCO 日志二次证据（analyze_cco.py + samples）
  - `侦听台/` —— 侦听台 HPLC 报文二次证据（analyze_sniff.py + samples）
  - 根：`run.py`（taish/cco/sniff/cross 四命令统一入口）+ `README.md` + `分析规则.md` + 单测
  - 技能 `skills/hf-collect-analysis/` 同步升级 2.0.0，入口指向 `tools/taiti/高频采集/run.py`
- **理由**：用户要求"这类分析放到扩展系列中，tools 目录下，并分类好台体文件夹"。
  高频采集属台体（taiti）分析扩展，按日志来源分子目录比扁平包更清晰、可扩展
  （后续台体其他分析可并列 `tools/taiti/<主题>/`）。
- **影响**：
  - 新目录 `tools/taiti/高频采集/`（README/分析规则/run.py/三子目录+各自 samples/单测 16 用例）。
  - 删除 `tools/scripts/hf_collect_analyze/` 与旧单测 `tools/scripts/test_hf_collect_analyze.py`。
  - `_REPO` 路径推导从 `parents[3]` 改为 `parents[4]`（新目录深一层）。
  - cross 拆分为 CCO/侦听台两模块，由 run.py 组合；修复 run.py 参数解析 bug
    （--start/--end 的值被误当表地址）。
  - 入口用 `run.py` 而非 `python -m`（中文包名在 `-m` 下不可用）。
  - REQS-INDEX 0006 标题更新为 tools/taiti 路径。
- **被取代**：取代 ADR-1（原 ADR-42）中"目录位置为 tools/scripts/hf_collect_analyze"的部分；
  ADR-1 的"收口为工具包+技能+精简样例入库"本质仍生效。

## ADR-3 13762 协议库重构：彻底重构为 Q/GDW 10376.2 单 68 标准帧（推翻双 68 信封）

- **日期**：2026-08-27
- **状态**：✅ 生效
- **决定**：
  - 将 `libs/parser_lib/adapters/adapter_10376` 从现有的**双 68 信封**
    （`68|L(2B)|68|AFN|SEQ|RTUA(6)|MSAA|PW(2)|用户数据|CS|16`）**彻底重构**为
    **Q/GDW 10376.2—2019 单 68 标准帧**：
    `68H | L(2B) | C(1B) | 用户数据(L1) | CS(1B) | 16H`，其中用户数据 =
    `R(信息域) | A(地址域) | AFN(应用功能码) | 应用数据`。
  - 帧格式基准以 codebuddy/hy3 核查蒸馏目录结论为准（已存 `docs/协议/13762库设计/帧格式核查结论.md`）：
    1376.2 权威帧格式为单 68，不存在双 68；AFN/Fn 应用层语义保留现有正确映射。
  - **同步改造既有调用方**：`sim_concentrator`（frame_codec/runner/responder/matcher/serial_io）、
    `loghooks/sources.py` 的 `parse_concentrator_10376`、相关测试（约 60+ 用例）全部适配单 68。
  - 新库能力：**输入 JSON → 构帧 bytes；输入 bytes → 解析 JSON**，无 IO、纯函数；
    645/698 作为内嵌帧继续复用现有适配器递归解析。
  - 本重构允许临时保留一个双 68 兼容入口（如 `decode_legacy_dual68`）供过渡期测试比对，
    不作为长期 API。
- **理由**：
  - 用户重新设计需求："构建 13762 协议的解析与构建库，参考蒸馏目录文档，能解析能构建，
    645/698 内嵌可复用对应库"。
  - codebuddy/hy3 核查确认：现有双 68 信封与 Q/GDW 10376.2 权威标准不符，
    `test_10376_doc.py` 亦自述"文档帧为单68H，适配器为双68H，文档帧直接 decode 会被拒"。
  - 双 68 是"应用层正确、链路层错配"的历史妥协（按抓包帧实现），本次重构根除该病根。
- **影响**：
  - `adapter_10376` 全部重写：build_frame / try_extract / confidence / decode 改为单 68。
  - `sim_concentrator.frame_codec` 的 build_13762_frame/decode_frame/scan_frame 适配单 68。
  - 6 省 doc 测试 + sim_concentrator 测试 + loghooks 测试需同步改写为单 68 帧样本。
  - 风险：sim_concentrator 与真实设备/抓包帧的互操作行为改变，需回归验证。
- **被取代**：取代归档前 ADR-10 中"帧格式统一走 adapter_10376（双 68 AFN/SEQ/RTUA/MSAA/PW 信封）"
  的口径；ADR-10 的模块定位/验证闭环/串口通道/REST-CLI 架构不受影响，仍生效。

## ADR-4 DECISIONS.md 启用 10 条归档重启（首次归档，3 缓存重启）

- **日期**：2026-08-28
- **状态**：✅ 生效
- **决定**：对项目 DECISIONS.md 应用 req-mgmt 技能流程 G 的**归档重启**规则（ADR 记录含已被取代的累计满 10 条即归档）：
  1. **首次归档**：归档前文件含 ADR-1~44（活动决策表 #1~44）共 44 条，已整份移入
     `archives/DECISIONS-20260828-0025.md`（全量历史保留，可回查）。
  2. **3 缓存重启**：新文件复制归档前末尾 3 条（ADR-42/43/44）作缓存种子，重排为本文件的
     ADR-1/2/3（正文原样保留，仅编号重排）；本文件从 ADR-4 起继续新增。
  3. **生命周期**：每个生命周期 = 3 条缓存 + 最多 7 条新增；再次累计满 10 条（新增到 #10）
     时再次归档重启。
- **理由**：原「只追加、永不删除」无上限，44 条堆积导致阅读与加载成本上升；套用全局
  req-mgmt 的 10 条归档规则（DECISIONS.md #5「3 条缓存 + 7 条新增滑动窗口」），
  既保留全量历史可回溯，又强制收敛到「缓存种子 + 精简正文」。
- **影响**：本文件现为 3 条缓存 + 本 ADR（#4）；后续新增到 #10 触发再次归档；
  `archives/DECISIONS-20260828-0025.md` 保留 ADR-1~44 全量。归档脚本可参考
  `D:/05-reasonix/skill+rule/shared/req-mgmt/scripts/archive.js`（该脚本针对 req-mgmt 自身
  格式，项目用 `## ADR-NN` 格式需手动按本规则执行）。
- **被取代**：无（新增决策；取代的是"无上限只追加"的隐含口径，原始正文仍在归档中）。

## ADR-5 模拟集中器用例语义化 + Profile（send 只写 afn/fn+params，构帧交 13762 库）

- **日期**：2026-08-28
- **状态**：✅ 生效
- **决定**：对模拟集中器验证任务的用例结构做语义化改造，让用例只保留"必要信息"，
  完整帧交给 13762 库生成：
  1. **用例 send 语义化**：task step 的 `send` 不再写 `raw` 整帧 hex 或
     `format:"local"+buff` 手写 hex，只写 `afn/fn + params`（最小业务参数）。
     `raw` 字段**彻底移除**，传入即报错，无回退路径。
  2. **Profile 全局信息**：新增 `apps/workbench/scenarios/profiles/<id>.json`
     存放全局信息（`cco_addr`、`sta_archives` 档案、`comm_mode`、`seq_auto`、
     `task_range`）；task 顶层用 `profile` 引用，可用 `profile_overrides` 覆盖。
  3. **地址域语义（module_id=1 带地址域）**：A1/A3 按方向装配——下行 A1=cco_addr、
     A3=sta_addr（无具体目标时 A3 同址 cco）；上行 A1=sta_addr、A3=cco_addr；
     广播 A3=999999999999H。旧 `rtsa` 同址填 src/dst 的兼容映射废弃。
  4. **13762 库补构建侧编码**：`adapter_10376` 新增 `encode_app_data(afn, fn, params)`
     与解析侧 `_app_items` 对称，覆盖现有用例聚焦的 AFN/Fn（00H-F1、01H-F1、
     03H-F10、10H-F2/F4/F230/F231、11H-F1/F231/F232）。**未覆盖 Fn 抛 UnsupportedFn
     明确报错**，不静默产出错帧（替代 raw 兜底）。11H-F231 配置任务用**扁平 items**
     （`{meter_type, item, reply_len}`），codec 内部分组编码（单相/三相/其他表）。
  5. **转换层**：新增 `libs/sim_concentrator/scenario_codec.py`（`build_send`），
     把 `send + profile` 翻译成一次 `build_13762_frame` 调用；runner 的
     `build_send_frame` 以此为语义化主路径，`execute_task` 自动加载 task.profile。
- **理由**：用户要求"每个测试用例要执行哪些 13762 命令，应当是输入给 13762 库生成对应
  的帧再下发，用例只保留帧的必要信息（全局的 cco 地址、sta 档案信息；单个用例用到的
  afn/fn 与参数）"。手写 hex 用例难维护、地址/档案信息重复散落；语义化 + profile 让
  用例可读、全局信息单点维护、帧由协议库按模板生成（减少手工编帧错误）。
- **影响**：
  - 新增 `profiles/anhui.json`（+ 测试专用 `test.json`）、`scenario_codec.py`、
    契约文档 `docs/协议/13762库设计/用例语义化与Profile契约.md`、
    测试 `test_scenario_codec.py` / `test_scenario_contract.py` /
    `test_10376_encode.py` / `orchestration/test_profile_loading.py`。
  - `runner.build_send_frame` 语义化主路径；`format:"local"+buff` 保留为迁移期兼容
    分支（迁移完成可删）；`raw` 移除。
  - 两个安徽存量 task（`anhui_698_meter_collect.json` / `anhui_minute_collect.json`）
    迁移到新结构；`test_anhui_task.py` 断言适配。
  - 帧字节序约定：多字节 BIN 小端（据安徽已验证帧核查）。
  - 既有 ADR-3（单 68 标准帧）不受影响；本决策在其框架内做应用层语义化。
- **被取代**：无（新增决策；取代的是"用例手写 hex/raw 直发"的隐含口径，
  原口径无独立 ADR，历史帧仍可在归档/文档回查）。

## ADR-6 首期架构规范解耦重构：parser facade、编排 ports 与 canonical/DTO 边界

- **日期**：2026-08-28
- **状态**：✅ 生效
- **决定**：批准需求 0007 的首期范围与 `reqs/0007-architecture-decoupling/DESIGN.md`：
  1. `parser_lib` 为 1376.2 解析唯一公开入口；`loghooks` 不得依赖 `sim_concentrator.frame_codec`。
  2. Workbench `RunExecutor` 只依赖 `MonitorPort` 与 `StimulusPort`；具体 `loghooks`/`sim_concentrator` 调用隔离至组合层适配器。
  3. `test_automation.models` 持有执行/审计 canonical model；Workbench 用语义明确的 DTO/view 与显式 mapper，不直接合并字段不同的同名模型。
  4. 解耦优先于既有 REST/JSON 兼容；若需改变外部契约，必须提供显式版本或迁移器、fixture/快照和重新落地测试。
  5. 首期仅允许离线单元/集成回归；真实串口联调在离线门通过后单列执行并先核实侦听台 `CON4`/ `COM4`。
- **理由**：当前跨库内部导入与 Runner 直接依赖具体实现违反骨架设计的分层要求；直接替换 Workbench 模型会丢失 REST/报告语义，显式边界可在保持可验证性的前提下完成收敛。
- **影响**：新增公开 parser facade、ports、适配器、DTO/mapper 和架构测试；首期不涉及 `shared`、大文件拆分或包管理。
- **被取代**：无。

## ADR-7 canonical 审计模型以兼容投影接入 Workbench（不破坏现有 REST/SQLite）

- **日期**：2026-08-28
- **状态**：✅ 生效
- **决定**：需求 0007 的 G3 采用 `reqs/0007-architecture-decoupling/G3-MIGRATION.md` 所定义的兼容投影切片：
  1. `test_automation.models` 为执行/审计的 canonical source；Workbench DTO/view 仅为边界投影。
  2. 保留既有 `/api/run`、run/report JSON 字段和 SQLite 旧列；新增审计列与 schema migration 必须 additive、幂等、事务化。
  3. Scenario 缺失 version 时固定采用 `1.0.0`；旧数据库行使用明确 legacy 标记，绝不伪造历史 fingerprint。
  4. canonical Report.summary 以具名键保存 Workbench 旧报告信息，mapper 恢复等价 ReportView；Artifact 增加 additive size 字段。
  5. migration 与回归只使用临时 SQLite/Fake ports/FakeIO，不访问 runtime 数据或硬件。
- **理由**：字段矩阵证明当前 DTO/mapper 未接入生产，直接替换会丢失报告字段和审计信息；兼容投影可先完成单一事实源，再在后续需求单独评估 API 版本演进。
- **影响**：runner/store/api 与 mapper 接入 canonical 模型；新增 runs 审计列和旧 schema 回读测试；本 ADR 不删除对外字段。
- **被取代**：无。

## ADR-8 模拟集中器会话帧日志持久化 + AI 控制面 simcon 接口

- **日期**：2026-08-29
- **状态**：✅ 生效
- **决定**：补齐模拟集中器（sim_concentrator）面向 AI 的闭环能力：
  1. **会话帧日志（FrameJournal）**：串口每收发一帧记录 `{seq, ts, dir: tx|rx, kind, run_id, frame_hex, afn, fn, updown, parsed}`，逐行持久化到 `data/logs/simcon/sc-<时间戳>-<端口>.jsonl`；一个会话 = 一次 open→close（或一次 verify 自建临时串口的生命周期），最近 10 个会话保留可查；TX 在 `SerialIO.send_frame`、RX 在读线程记录，`journal.scope(run_id, kind)` 给帧打 run 归属（step_send/manual_send/auto_reply）。
  2. **simcon 层新端点**（`/api/simcon/*`，页面兼容、同步、只做增量字段）：`POST /step`（单步语义下发或 `recv_only` 等一帧感知 CCO 主动上报，串口未开自动打开）、`GET /frames`（按 direction/updown/afn/fn/kind/run_id/after_seq 过滤）、`GET /session`；`/status`、`/verify`、`/open` 增补 `session`/`session_id`/`run_id`/`frames_seq` 字段。
  3. **AI 控制面门面**（`/api/ai/v1/simcon/*`，token+scope+audit，层间进程内注入不走 HTTP）：`POST /simcon/verify`（异步 operation，202+wait，并发 409、不可取消）、`POST /simcon/step`（同步+幂等）、`GET /simcon/frames`、`GET /simcon/session`、`POST /simcon/open`、`POST /simcon/close`；新增 scope `simcon:verify` / `simcon:send` / `simcon:read`，resource 固定 `simcon`；执行核心经 module_log→workbench 状态提升链注入 `AIControlService(simcon_service=...)`。
  4. 单步/任务下发一律遵守 ADR-5 语义化（`send.raw` 报错）；串口独占沿用 `SerialResourceRegistry`（冲突 409）。
- **理由**：此前 simcon TX 帧完全不记录、RX 仅内存环形缓冲（关串口即丢）、CCO 主动上报无消费记录、无 run/会话概念，AI 只能拿到每次 verify 的结果 JSON；「本次下发过什么帧 / CCO 主动上报过什么帧 / 有无某类 afn 上行帧」三类问题无法回答。JSONL 追加写与 loghooks 事件日志、module_log 串口日志同构，内存镜像支撑过滤查询，比 SQLite 更贴合"会话级、追加型、低频"的帧日志形态。
- **影响**：新增 `libs/sim_concentrator/journal.py` 与三个测试文件；`serial_io.py`/`runner.py`/`api.py` 增量改造（`send_frame` 签名不变）；`module_log/app.py` 提升访问器；`workbench/app.py` 新增 `SimconAIService` 桥并注入 AI 控制面；`ai_api.py` 新增 6 条路由；docs/16 新增第 9 节（原 9-12 顺延）、ai-control-plane skill 升级 1.2.0；REQS-INDEX 登记需求 0008。全部测试用 FakeIO/FakeSerial（0007 红线），487 通过。
- **被取代**：无。
