# 双模通信（CCO/STA）AI 闭环研发验证平台 —— 项目设计需求文档

> **文档定位**：项目级设计需求文档（PRD + 总体设计），归档用。定义"AI 闭环编码—烧录—运行监控—结论"平台的完整需求、架构、数据模型、接口与验收标准。
> **适用范围**：侦听台（listener）、模块日志/烧录（module_log）、事件监控（loghooks）、模拟集中器（sim_concentrator）、共享解析库（shared / parser_lib）。
> **版本**：v1.1 ｜ **日期**：2026-08-14 ｜ **状态**：归档定稿（待评审迭代）
> **关联文档**：`DECISIONS.md`（ADR-1~13）、`docs/需求设计方案/loghooks-design.md`、`docs/开发与运维/模拟集中器验证工具使用手册.md`、`README.md`
>
> **v1.1 变更**：
> 1. 新增 **FR-6 统一集成程序（platform）**：最终形态为"一个程序、全链路打通"（响应需求方 2026-08-14 意见）。
> 2. 新增 **6.2 仓库目录结构规范**：根目录现状盘点、问题、目标布局（两阶段）与迁移纪律（响应"根目录乱七八糟"意见）。

---

## 1. 项目概述

### 1.1 背景

公司产品由三部分构成：

- **CCO**（Concentrator Controller / 集中器通信单元）：与集中器通过串口交互，遵循 **13762 协议**（Q/GDW 10376.2 / DL/T 1376.2 采集终端与通信模块接口协议）；同时通过电力线载波/微功率无线网络与 STA 交互。
- **STA**（Station / 站点单元）：网络侧从节点，通过双模网络与 CCO 通信。
- **侦听台**（listener）：抓取网络层交互报文，对 HPLC/双模帧做深度解析与分钟级报表分析。

研发过程中的核心诉求是**缩短"改代码 → 验证 → 结论"的迭代周期**：固件代码修改后需要烧录、复位、观察运行日志、判定功能是否按预期运行。海量轮询/状态机日志淹没了关键事件，人工核查效率低、结论不统一。

### 1.2 建设目标

建设一套**面向 AI（及工程师）的闭环研发验证平台**：

```
编码（代码修改）→ 编译/烧录 → 运行监控（事件化）→ 自动化验证（激励+断言）→ 结构化结论 → 归因反馈（喂回编码）
```

平台由两条已建成的主能力线支撑：

1. **事件监控（loghooks）**：只要给出"日志打印的 JSON 规则 + 事件名"，即可从 CCO/STA 打印日志中提取**完整的运行流程**（事件时间线、状态机路径、跨来源关联）。
2. **模拟集中器（sim_concentrator）**：把"要验证的流程"交给模拟集中器，即可在串口上自动执行 **13762 帧交互**（下发 → 接收 → 匹配 → 解析 → 逐步判定），返回结论 JSON。

**最终产品形态（v1.1 明确）**：交付**一个集成程序**（`platform` 一体化工作台）——一个桌面入口、一个统一后端、一个页签式界面，把侦听台、模块日志/烧录、对照解析、模拟集中器与 AI 验证工作台全部集成，全链路（编码 → 烧录 → 运行监控 → 激励验证 → 结论报告）在一个程序内闭环（见 FR-6）。

### 1.3 术语表

| 术语 | 说明 |
|------|------|
| CCO | 集中器通信单元，串口侧对接集中器（13762），网络侧对接 STA |
| STA | 站点单元，双模网络从节点 |
| 侦听台 | 网络层报文抓取与解析工具（listener，端口 8765） |
| 模块日志/烧录 | 模块串口实时日志 + XMODEM 固件烧录（module_log，端口 8766） |
| loghooks | 配置驱动的日志运行状态钩子：日志 → 事件 |
| sim_concentrator | 模拟集中器：13762 帧激励与应答、验证任务执行（可挂载 8781 / /api/simcon） |
| 13762 协议 | Q/GDW 10376.2（DL/T 1376.2）采集终端与通信模块接口协议；信封 AFN/SEQ/RTUA/MSAA/PW + 用户数据（可嵌套 645/698 帧） |
| 事件（Event） | loghooks 从日志/帧中提取的一条带类型、时间、字段的关键运行状态 |
| 验证任务（Task） | sim_concentrator 执行单元：若干步骤（下发帧 + 期望帧断言） |
| 结论（Verdict） | 验证任务的逐步 Pass/Fail + 汇总判定 |

---

## 2. 系统角色与拓扑

```
                     ┌──────────────────────────────┐
                     │          集中器               │
                     │  (真实 / 模拟 sim_concentrator)│
                     └──────────────┬───────────────┘
                                    │ 串口（13762 协议，可读写）
                     ┌──────────────▼───────────────┐
                     │            CCO                │
                     │  (串口交互 + 网络侧主节点)      │
                     └──────────────┬───────────────┘
                                    │ 双模网络（电力线载波/微功率无线）
                     ┌──────────────▼───────────────┐
                     │            STA                │
                     │  (网络侧从节点)                │
                     └──────────────┬───────────────┘
                                    │ 抓包（只读监听）
                     ┌──────────────▼───────────────┐
                     │          侦听台 listener       │
                     │  (8765: 帧解析/分钟报表/日志索引)│
                     └──────────────────────────────┘

     上位机工具（工程师 / AI 侧）：
     ┌────────────────────────────────────────────────────────┐
     │ module_log (8766)                                        │
     │   页签1 实时日志（CCO/STA 串口 RX/TX + XMODEM 烧录）        │
     │   页签2 对照解析（loghooks 事件 ↔ 原始日志双向联动）         │
     │   页签3 模拟集中器（simcon 子应用可视化）                    │
     │ loghooks CLI / REST：日志 → 事件流/摘要/关联                │
     │ sim_concentrator CLI / REST：验证任务 → 结论 JSON           │
     └────────────────────────────────────────────────────────┘
```

### 2.1 角色职责

| 组件 | 职责 | 与闭环的关系 |
|------|------|-------------|
| 集中器 | 13762 协议主站侧 | 真实集中器（外场）/ 模拟集中器（实验室） |
| CCO | 协议转换与网络主控 | **被测对象**之一：串口侧与集中器交互、网络侧管理 STA |
| STA | 网络从节点 | **被测对象**之一：入网、上报、接收下行 |
| 侦听台 | 网络层帧抓取 + 深度解析 | **观测面**：网络层交互的客观证据 |
| 模块日志/烧录 | 串口日志采集 + 固件烧录 | **执行面**：烧录代码、采集模块打印 |
| loghooks | 日志 → 事件化 | **观测面**：运行逻辑的事件化视图 |
| sim_concentrator | 13762 帧激励 + 应答 | **激励面**：向 CCO 注入业务流程并断言 |

---

## 3. 现状盘点（已建成能力，文档归档基线）

| 模块 | 已建成能力 | 依据 |
|------|-----------|------|
| 项目拆分 | listener / module_log 双应用 + shared 共享库，互不依赖 | ADR-1 |
| 侦听台 | 串口实时采集、HPLC 帧解析（C# DLL）、分钟报表、日志索引/分页、数据源互斥 | ADR-1/3，README |
| 模块日志 | CCO/STA 串口 RX/TX 实时日志、XMODEM 烧录、桌面 exe 化 | ADR-2，README |
| loghooks | 配置驱动规则（text/field 匹配、sequence 状态机）、cco/sta/common/省份规则隔离、行号弱约束 + 漂移清单、对照解析页双向联动、第三来源 concentrator_10376 已接入 | ADR-4/5/6/7/10 |
| 模拟集中器 | 13762 构帧/切帧/解析（adapter_10376）、可读写串口、应答引擎、验证任务闭环（下发→接收→匹配→解析→判定）、REST/CLI 双入口、module_log 第三页签可视化 | ADR-10/13 |
| 桌面化 | 侦听台与模块日志均支持 pywebview 桌面 exe + 网页双模式；bat 统一 GBK+CRLF | ADR-2/3/9/11 |
| 测试 | 全量 pytest 402 passed / 66 skipped；loghooks + sim_concentrator 覆盖单测 | ADR-5/10/13 |
| 记忆后端 | OpenViking 记忆后端接火山方舟 embedding/VLM | ADR-12 |

**与本文档的差距（后续迭代目标）**：目前 loghooks 与 sim_concentrator 是"工具级"能力，尚缺**编排层**把 烧录→监控→激励→结论 串成一条可追溯的验证流水线（见第 7 章 FR-5）。

---

## 4. 需求详述（FR）

### FR-1 事件监控（loghooks）—— 日志 → 完整运行流程

#### FR-1.1 输入

用户/AI 提供**规则配置**（JSON），描述"什么打印对应什么事件"：

- 来源：`module_log`（文本行）/ `listener`（hex 帧）/ `concentrator_10376`（13762 帧）。
- 匹配：`text` 正则或 `field` 字段断言；可选 `file`/`line`/`line_tolerance` 弱约束。
- 组合：单行触发（match）与跨行状态流（sequence）。
- 组织：按模块隔离（cco/sta/common）+ 省份规则文件，全部 json 同时加载、自动识别省份。

规则 schema 见第 8.1 节。

#### FR-1.2 输出

对一份（或多份关联）日志扫描后输出：

1. **事件流**：每条事件含类型/时间/标签/消息/来源行（可回溯原始日志行）。
2. **摘要**：按类别（join/collect/send/beacon/state/flash）聚合计数与样本。
3. **状态机流程**：sequence 规则的完整路径（各步命中时间、on_complete / on_timeout）。
4. **跨来源关联**：模块日志 ↔ 侦听台 ↔ 集中器帧，以业务锚点（NID/MAC/冻结时刻/RTUA）关联。
5. **规则漂移清单**：行号漂移（命中但 line 超容差）汇总，提示规则维护。
6. **省份识别**：`detected_provinces` 自动判定。

#### FR-1.3 使用场景

| 场景 | 说明 |
|------|------|
| 烧录后验证 | 烧录 → 复位 → 扫 CCO/STA 日志 → 确认"入网成功、分钟上报闭环" |
| 事后复盘 | 导入历史日志文件/目录，事件与原始日志双向定位 |
| 实时监控 | 串口采集同时实时出事件（异步队列，2s 刷新） |
| AI 集成 | CLI/REST 产出摘要 JSON 供 AI 读取判断 |

### FR-2 模拟集中器（sim_concentrator）—— 流程 → 13762 自动交互

#### FR-2.1 输入

**验证任务 JSON**：若干步骤，每步 = 下发帧（send）+ 期望（expect / expect_no_reply）+ 超时；可选应答规则（responders）。帧用 13762 信封描述（AFN/SEQ/RTUA/MSAA/PW + 用户数据，可含嵌套 645/698 帧）。

任务 schema 见第 8.2 节。

#### FR-2.2 输出

**结论 JSON**：每步 sent_hex / matched / parsed（信封字段 + 数据项 + 嵌套帧）/ result（pass|fail）/ reason；汇总 total/pass/fail/verdict。CLI 退出码 0=pass、1=fail，便于 AI 脚本判断。

#### FR-2.3 能力

1. **主动下发**：按任务构帧并发到串口。
2. **匹配接收**：按 AFN/信封字段/嵌套字段断言收到的帧。
3. **主动应答**：内置应答规则表 + 任务/步骤级覆盖规则，模块上行自动回下行。
4. **逐步判定 + 汇总**：fail_fast 控制是否提前中止。
5. **可视化**：module_log 第三页签人工监督同一串口。

### FR-3 侦听台（listener）—— 网络层交互抓取与解析

- 串口裸 7E 帧流实时采集，先落盘 `LOG/侦听台/` 再实时入库；串口监听与日志文件分析互斥。
- 国网/南网协议帧解析（C# DLL + Python 富化），输出结构化 simple dict。
- 分钟级报表分析、报文文件筛选（`/api/fs/*`）。
- 作为 loghooks 第二来源与跨来源关联的**客观证据面**。

### FR-4 模块日志/烧录（module_log）

- CCO/STA 串口实时日志，按行加 `YYYYMMDD-HH:MM:SS:mmm` 时间戳，分类落盘 `LOG/模块/{cco|sta}/`。
- XMODEM 固件烧录（同串口句柄，RX 监控不停）。
- 三个页签：实时日志 / 对照解析 / 模拟集中器。

### FR-5 AI 闭环编排层（本次归档新增的远期目标需求）

> 现状是"AI 人肉调用工具"（手工生成任务 JSON、手工跑 scan）。平台化后应由**编排层**把闭环串成可追溯的流水线。

#### FR-5.1 验证批次（Run）抽象

每次闭环验证 = 一个 **Run**，包含：

```
run_id（唯一批次号）
├── 输入：代码 commit / 固件版本 / 烧录文件 hash / 验证场景模板 id / 参数
├── 执行：烧录动作（module_log）→ 复位 → 事件监控（loghooks）→ 激励任务（sim_concentrator）
├── 产出：事件流摘要、验证结论、证据链（日志文件路径、帧 hex、截图）
└── 归档：结论入库，支持按 run_id / 固件版本 / 场景检索复盘
```

#### FR-5.2 统一验证报告（Report）Schema

loghooks 摘要、sim_concentrator 结论、侦听台分钟报表三者字段语义不统一，需归一为一份**验证报告**：

```json
{
  "run_id": "run-20260814-0001",
  "firmware": {"version": "v2.3.1", "commit": "a1b2c3d", "flash_file_sha256": "..."},
  "scenario": "minute_collect_anhui",
  "sources": {
    "module_log": {"files": ["LOG/模块/cco/xxx.log"], "events": 42, "summary": {...}},
    "listener": {"frames": 128, "minute_reports": {...}},
    "sim_concentrator": {"task_id": "...", "summary": {"total": 5, "pass": 5, "verdict": "pass"}}
  },
  "assertions": [
    {"id": "join.sta.ok", "expected": "STA 入网成功", "actual": "NID=0x61475d", "result": "pass"}
  ],
  "verdict": "pass",
  "artifacts": ["LOG/...", "data/reports/run-....jsonl"],
  "ts": "2026-08-14T10:00:00+08:00"
}
```

#### FR-5.3 期望流程比对（流程投影）

"输入 JSON 打印与事件名 → 得到完整运行流程"的语义落点为：声明式描述**期望流程**（步骤序列 + 时间窗 + 分支/可选步），工具把实际事件流**投影**到期望流程上，标出：

- ✅ 命中步骤（含时间）
- ❌ 缺失步骤（期望出现但未出现）
- ⚠️ 超时步骤
- 🔀 顺序错乱（步骤出现但次序不符）
- 🚫 负向断言触发（期望**不出现**的事件出现，如 `assoc err`）

输出流程比对图（JSON 数据 + 前端渲染），这是"运行监控得到结论"的核心判据。

#### FR-5.4 归因反馈

验证失败时，把结构化失败原因（缺失事件/超时/帧匹配失败/规则漂移）组装为**给编码模型/工程师的反馈**，形成"失败 → 归因 → 修复 → 再验证"回路。归因规则可配置（如"assoc err 出现 → 检查关联流程；分钟上报缺失 → 检查采集任务配置"）。

### FR-6 统一集成程序（platform）—— 全链路打通的最终形态（v1.1 新增）

#### FR-6.1 目标

最终交付**一个程序**：一个桌面入口 + 一个统一后端 + 一个页签式界面，把全部能力（侦听台、模块日志/烧录、对照解析、模拟集中器、AI 验证工作台）集成在一起，全链路在一个程序内闭环，不再需要分别打开 8765/8766/8781 三个服务与三个页面。

#### FR-6.2 形态

| 层 | 形态 | 说明 |
|----|------|------|
| 桌面入口 | pywebview 单窗口 | 复用现有 `desktop.py` 模式（ADR-2/3），一个 exe 即全部功能 |
| 统一后端 | 一个 FastAPI 主应用 | 挂载 listener app、module_log app、simcon app，并新增编排路由 |
| 前端 | 页签式 SPA | 侦听台 / 模块日志 / 对照解析 / 模拟集中器 / 验证工作台 |
| 编排层 | orchestration 子包 | Run 管理、场景模板、流程比对、归因反馈、报告归档（FR-5） |
| 数据 | runs 库 + 报告归档 | `data/runs.sqlite`（元数据索引）+ `data/reports/{run_id}.json`（报告） |

#### FR-6.3 目录形态（新增 `platform/` 顶层包）

```
platform/
├── app.py              # 统一 FastAPI：mount 各子应用 + /api/run 等编排路由
├── desktop.py          # 统一桌面入口（pywebview 单窗口）
├── orchestration/      # run / report / compare / feedback / scenarios
├── static/             # 页签式前端（index.html / app.js / styles.css）
└── test_*.py           # 集成测试
```

#### FR-6.4 设计原则

1. **不合并底层代码**：listener / module_log / sim_concentrator / loghooks 保持独立包，各自可独立运行、独立测试；platform 只做**挂载 + 编排**（延续 ADR-1/10/13 的解耦哲学，不推翻）。
2. **双模式并存**：独立服务（8765/8766/8781）保留为开发者模式；platform 统一入口（如 8790）为产品模式，`启动工具.bat` 新增对应选项。
3. **编排能力零重实现**：Run/报告/比对/反馈全部复用 loghooks 引擎、sim_concentrator runner、listener 解析链，platform 不重复造轮子。

#### FR-6.5 验收标准

1. 双击一个 exe → 单窗口内含全部页签，各功能可操作、可切换。
2. 验证工作台可一键执行"烧录 → 监控 → 激励 → 报告"闭环，报告按 run_id 归档可查。
3. 底层模块仍可独立运行与测试（全量 pytest 回归不破）。

---

## 5. 非功能需求（NFR）

| 编号 | 类别 | 需求 |
|------|------|------|
| NFR-1 | 性能 | 事件监控支持大文件（>100MB）流式扫描；实时模式事件产出不阻塞串口写盘（异步队列 + 背压） |
| NFR-2 | 可靠性 | loghooks 运行时接入失败**静默降级**，绝不影响日志主链路；串口异常返回可读错误（409） |
| NFR-3 | 可维护性 | 规则/任务/场景全部**配置驱动**（JSON），新增省份/业务 = 新增配置文件，不改代码 |
| NFR-4 | 可追溯 | 每个验证结论可回溯到：规则版本、日志文件、帧 hex、代码 commit、固件版本 |
| NFR-5 | 兼容性 | 解析口径统一走 `parser_lib.adapters.adapter_10376`（不混用其他 1376.2 实现） |
| NFR-6 | 安全性 | 测试数据与生产隔离；ESAM/密码（PW）等敏感字段不落明文报告；终端地址/表号按需脱敏 |
| NFR-7 | 可测试性 | 单测不依赖硬件（假串口注入）；协议一致性用真实抓包 golden data 回放 |
| NFR-8 | 部署 | 桌面 exe（pywebview）与网页双模式并存；AI 侧优先 CLI/REST headless 调用 |

---

## 6. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  platform 一体化工作台（统一集成程序，目标 FR-6）              │
│  ▸ 统一 FastAPI（挂载 listener/module_log/simcon）           │
│  ▸ AI 闭环编排层：Run 管理 / 场景模板 / 期望流程比对 /         │
│    归因反馈 / 报告归档（FR-5）                               │
│  ▸ 统一桌面入口（pywebview 单窗口）+ 页签式 SPA               │
└───────┬───────────────────────────────┬─────────────────────┘
        │ REST / CLI                     │ REST / CLI
┌───────▼───────────┐         ┌─────────▼──────────────────┐
│  loghooks          │         │  sim_concentrator          │
│  engine/sequence/  │         │  frame_codec/serial_io/    │
│  matchers/rules/   │         │  responder/matcher/runner/ │
│  sources/correlate │         │  api/cli                   │
│  rules/*.json      │         └─────────┬──────────────────┘
└───────┬───────────┘                   │ adapter_10376
        │                               ▼
┌───────▼───────────────────────────────────────────────────┐
│              共享层 shared / parser_lib                      │
│  infra / dotnet_parser / parser_service / application_service│
│  adapters（10376 / 645 / 698 / 双模43）                       │
└───────┬──────────────────────────────────┬─────────────────┘
        │                                  │
┌───────▼──────────┐         ┌─────────────▼───────────────┐
│  listener (8765) │         │  module_log (8766)           │
│  串口采集/帧解析/   │         │  串口日志/XMODEM烧录/          │
│  分钟报表/日志索引  │         │  对照解析/模拟集中器页签        │
└──────────────────┘         └─────────────────────────────┘
```

### 6.1 架构原则

**架构原则**（延续 ADR 体系）：

1. 模块解耦：listener / module_log 互不 import，仅共享 shared/parser_lib。
2. 观测与激励分离：侦听台只读监听，模拟集中器独立可读写串口。
3. 配置驱动：规则、应答、任务、场景全部外部化 JSON。
4. 解析口径单一：13762 帧统一走 adapter_10376。
5. 决策可追溯：架构决策继续以 ADR 追加记录（DECISIONS.md）。
6. **集成不合并（FR-6）**：platform 只做挂载与编排，底层模块保持独立可测。

### 6.2 仓库目录结构规范（v1.1 新增）

#### 6.2.1 现状盘点（2026-08-14 实测）

| 顶层项 | git 跟踪 | 性质 |
|--------|---------|------|
| `listener/ module_log/ shared/ parser_lib/ loghooks/ sim_concentrator/` | ✅ | 6 个顶层代码包，**互相以绝对导入引用**（仓库根即 sys.path，全仓 143 处 `from shared/...`、`from parser_lib/...` 等） |
| `docs/` `docs/协议/` | ✅ | 项目文档 / 协议规范（国网、南网 PDF、报文格式、DLL 接口） |
| `data/`（含 `graphify-out/`） | ✅ | 数据与代码分析输出 |
| `legacy/` | ✅ | 历史归档（`use/` C# 测试工程、`dll_Tesll/` 编译产物快照） |
| `tools/`（原 `packaging/` `scripts/`） `reqs/` | ✅ | 打包 / 辅助脚本 / 需求会话 |
| 根文件 | ✅ | README / DECISIONS / REQS-INDEX / oad_todo(→docs/需求管理/归档/) / conftest / DLL.sln / ov.conf / reasonix.toml / 启动工具.bat / .git* |
| `build/ dist/ LOG/ packages/ use/ graphify-out/ 测试文件/ .venv/ .pytest_cache/ __pycache__/` | ❌ 全部未跟踪 | PyInstaller 中间/发布产物、运行时日志、NuGet 还原包、历史遗留（`use/`、`graphify-out/` 已在 .gitignore 标注"待清理"）、本地测试大文件、虚拟环境 |

**结论**：git 视角的根目录并不乱（15 项顶层）；"乱"主要来自**本地磁盘上大量未跟踪噪音目录与代码目录混杂**（资源管理器视角），以及 6 个顶层代码包平铺、无法一眼区分"应用"与"库"。

#### 6.2.2 问题清单

1. 顶层 6 个代码包平铺，分不清"应用（可独立运行）"与"库（被引用）"。
2. 本地噪音目录（build/dist/LOG/packages/use/graphify-out/测试文件）与代码混杂。
3. 文档分散三处（docs/、docs/协议/、docs/需求管理/归档/oad_todo.md）。
4. 工具分散（tools/scripts/、tools/packaging/）。
5. 历史遗留（use/、graphify-out/）未清理。

#### 6.2.3 目标布局（渐进式，两阶段）

> **执行状态（2026-08-14）**：两阶段已按 ADR-14 一并执行完毕（`git mv` 保留历史，sys.path 注入、导入名不变）。以下为迁移后实际布局：

```
侦听台改造/
├── apps/            # 应用层：listener/ module_log/ platform/(FR-6 落地时新增)
├── libs/            # 库层：shared/ parser_lib/ loghooks/ sim_concentrator/
├── docs/            # 项目文档 + docs/协议/（协议规范）+ docs/需求管理/（需求汇总与归档）
├── tools/           # 工具：tools/scripts/ + tools/packaging/
├── reqs/            # 需求会话归档（配合 REQS-INDEX.md）
├── data/            # 运行日志 data/logs/（frozen exe 下为 exe 同目录 LOG/）；graphify-out 已清理
├── legacy/          # 历史归档（use/ C# 测试工程、dll_Tesll/ 编译产物快照）
├── 根文件            # README「根目录速览」表 / .gitignore（data/graphify-out/ 补漏、dist 产物说明）
└── conftest.py      # 仓库级 pytest 配置：注入仓库根 + apps/ + libs/ 到 sys.path
```

**阶段一（已完成）**：归组非代码项 + 清理遗留：
- `侦听台文档/` → `docs/协议/`（南网/国网子目录）、`oad_todo.md` → `docs/需求管理/归档/oad_todo.md`，交叉引用全量同步。
- `scripts/` + `packaging/` → `tools/scripts/` + `tools/packaging/`（spec 的 ROOT 解析、bat 相对路径、冒烟脚本路径全量同步）。
- 运行日志 `LOG/` → `data/logs/`（listener/module_log/loghooks 的 `_log_dir()` 与默认落盘路径同步；frozen 形态仍为 exe 同目录 `LOG/`；本地历史日志随目录移动保留现场）。
- `data/graphify-out/` 76 个跟踪文件 `git rm` 清理，`.gitignore` 追加 `data/graphify-out/`。

**阶段二（已完成）**：代码包分层 `apps/` + `libs/`：

```
├── apps/            # listener/ module_log/ platform/(新增)
├── libs/            # shared/ parser_lib/ loghooks/ sim_concentrator/
```

- 依赖改造：**全部 ~131 处顶层绝对导入零改动**（sys.path 注入决策），仅改入口与配置层：
  - `conftest.py`、`apps/*/run.py`、`apps/*/desktop.py`、`apps/module_log/flash_module.py`、`libs/*/__main__.py` 注入仓库根 + apps/ + libs/（收敛到 `shared.infra.ensure_paths()`）。
  - PyInstaller spec：`pathex` 同时含 apps/ 与 libs/；`datas`（static、DLL）、入口脚本路径按新布局。
  - 启动脚本（`启动工具.bat`、`apps/*/启动*.bat`）、`DLL.sln`、测试路径（`test_ui_layout` 等 CWD 相对路径改为 `__file__` 相对）全量同步。
- 冷启动验证：`python -m listener.run` / `module_log.run`（Windows 启动脚本注入 PYTHONPATH）、`python -m loghooks scan` / `sim_concentrator verify` 均可 import。

#### 6.2.4 迁移纪律

1. 一律 `git mv` 保留历史；未迁移的包禁止手改 import。
2. 每次迁移后跑全量 pytest（基线 402 passed / 66 skipped；本 WSL 无 DLL 环境 326 passed / 66 skipped / 9 DLL 失败为环境基线）。
3. 目录变更同步更新 README，并追加 ADR 记录（DECISIONS.md 只追加不覆盖）——已追加 **ADR-14 目录结构两阶段迁移**。

---

## 7. 功能需求与现有实现的差距矩阵

| 需求 | 现状 | 差距 | 优先级 |
|------|------|------|--------|
| FR-1 事件监控 | ✅ 已实现（engine/sequence/rules/对照解析） | sequence 需支持可选步/循环/负向断言；命中率量化 | P1 |
| FR-2 模拟集中器 | ✅ 已实现（runner/api/cli） | 场景库模板化；协议一致性 golden 回放 | P1 |
| FR-3 侦听台 | ✅ 已实现 | — | — |
| FR-4 模块日志/烧录 | ✅ 已实现 | — | — |
| FR-5.1 Run 抽象 | ❌ 无 | 新增 run 管理 | P0 |
| FR-5.2 统一报告 | ❌ 无 | 新增 report schema 与聚合服务 | P0 |
| FR-5.3 期望流程比对 | ⚠️ sequence 有雏形 | 升级为通用流程投影 + 前端渲染 | P0 |
| FR-5.4 归因反馈 | ❌ 无 | 新增归因规则 + 反馈组装 | P1 |
| FR-6 统一集成程序 | ❌ 无 | 新增 `platform/` 包 + 统一前端 + 编排路由 | P0（产品形态） |
| 仓库目录结构 | ⚠️ 顶层平铺 + 本地噪音混杂 | 阶段一归组（零风险）；阶段二 apps/libs 分组（中风险） | P1 |
| 跨来源三方关联 | ⚠️ correlate 已设计、来源已注册 | 端到端验证（帧↔事件↔抓包） | P1 |
| 规则维护闭环 | ✅ rules diff 已实现 | 增加命中率/覆盖率指标与告警 | P2 |

---

## 8. 数据模型（核心 Schema 归档）

### 8.1 loghooks 规则

```json
{
  "id": "common.join_onnet",
  "category": "join",
  "level": "info",
  "scope": "common",
  "province": null,
  "source": ["module_log"],
  "match": {
    "mode": "text",
    "pattern": "onnet cnt = (\\d+)",
    "flags": ["i"],
    "file": "aps_ioctrl_nwk.c",
    "line": 950,
    "line_tolerance": 10
  },
  "capture": {"node_count": 1},
  "event": {"type": "network.onnet", "label": "入网节点数", "message": "入网节点数 = {node_count}"}
}
```

```json
{
  "id": "common.join_sta_flow",
  "category": "join",
  "scope": "common",
  "source": ["module_log"],
  "event": {"type": "join.sta", "label": "STA 入网流程"},
  "sequence": [
    {"step": "disc_done",   "pattern": "nwk disc done"},
    {"step": "assoc_start", "pattern": "start nwk assoc"},
    {"step": "track_succ",  "pattern": "nwk track done ind.*succ"},
    {"step": "bcn_recv",    "pattern": "recv bcn, from.*NID=([0-9a-fA-F]+)"}
  ],
  "capture": {"nid": 4},
  "window_ms": 30000,
  "on_complete": {"type": "join.sta.ok", "message": "STA 入网成功 NID={nid}"},
  "on_timeout": {"type": "join.sta.timeout", "level": "warn"}
}
```

**演进方向（FR-1 差距）**：sequence 步骤增加 `optional`（可选步）、`repeat_until`（循环重试）、`negate`（负向断言）；支持 `timing`（如 step2 必须在 step1 后 5s 内）。

### 8.2 验证任务（sim_concentrator）

```json
{
  "id": "verify.add_node",
  "port": "COM3",
  "baudrate": 115200,
  "enable_responder": true,
  "fail_fast": true,
  "responders": [
    {"match": {"afn": 0x11}, "reply": {"afn": 0x00, "userdata_builder": "confirm"}}
  ],
  "steps": [
    {
      "name": "模拟集中器下发 11H-F1 添加从节点",
      "send": {"afn": 0x11, "seq": 1, "rtsa": "070919051620", "msaa": 1, "pw": 0,
               "userdata": "00 01 68 12 34 56 78 90 12 68 91 08 00 00 00 01 00 12 34 56 78 34 16"},
      "expect": {"afn": 0x00},
      "expect_timeout": 5.0
    },
    {
      "name": "查询从节点数量 10H-F1，期望返回数量",
      "send": {"afn": 0x10, "rtsa": "070919051620", "userdata": "00"},
      "expect": {"afn": 0x10, "nested": true},
      "expect_timeout": 5.0
    },
    {
      "name": "期望无响应的广播帧",
      "send": {"afn": 0x14, "rtsa": "999999999999", "userdata": "00"},
      "expect_no_reply": true,
      "expect_timeout": 2.0
    }
  ]
}
```

### 8.3 结论（sim_concentrator 输出）

```json
{
  "task_id": "verify.add_node",
  "port": "COM3",
  "baudrate": 115200,
  "steps": [
    {"index": 0, "name": "...", "sent_hex": "68 11 ...", "matched": "68 0F ...",
     "parsed": {"structure": "1376.2", "fields": {...}, "nested": [...]},
     "result": "pass", "reason": "匹配成功"}
  ],
  "summary": {"total": 2, "pass": 2, "fail": 0, "verdict": "pass"}
}
```

### 8.4 事件流（loghooks 输出）

```json
{
  "source": "module_log",
  "files": ["LOG/模块/cco/xxx.log"],
  "province": "anhui",
  "summary": {"join": {"count": 3, "last": {...}}, "collect.minute": {"count": 12}},
  "events": [
    {"type": "network.onnet", "time": "...", "label": "入网节点数",
     "message": "入网节点数 = 1", "source_line": "[2026...] [RX] ... onnet cnt = 1"}
  ],
  "correlations": [...],
  "rule_drifts": [{"rule_id": "common.join_onnet", "expected_line": 950, "actual_line": 962}],
  "detected_provinces": [{"province": "anhui", "confidence": "high"}]
}
```

### 8.5 统一验证报告（FR-5 目标）

见第 4 章 FR-5.2 示例。

---

## 9. 接口设计（归档现状 + 目标新增）

### 9.1 现状接口

| 应用 | 接口 | 说明 |
|------|------|------|
| module_log (8766) | `/api/module-serial/*` | 串口状态/日志/烧录 |
| module_log | `/api/loghooks/scan` | 扫描日志文件/目录 → 事件+行绑定 |
| module_log | `/api/loghooks/realtime` | 扫内存日志缓冲（实时模式） |
| module_log | `/api/loghooks/sources` | 来源/模块信息 |
| module_log | `/api/simcon/*` | 模拟集中器子应用（挂载） |
| module_log | `/api/fs/pick` | 文件/目录选择 |
| sim_concentrator (8781) | `GET /api/simcon/status|ports|responders`、`POST /api/simcon/open|close|verify` | 独立运行入口 |
| listener (8765) | `/api/fs/*`、解析/报表/索引接口 | 侦听台 |
| loghooks CLI | `python -m loghooks scan <log> [--module cco|sta] [--province X] [--correlate <log2>]` | 离线扫描 |
| loghooks CLI | `python -m loghooks rules diff --old <a> --new <b>` | 规则差异 |
| sim_concentrator CLI | `python -m sim_concentrator verify <task.json> [--json]` | 验证任务（退出码 0/1） |

### 9.2 目标新增接口（FR-5 / FR-6）

| 接口 | 说明 |
|------|------|
| `POST /api/run` | 创建验证批次（Run），入参：场景 id、固件信息、参数 |
| `GET /api/run/{run_id}` | 查询批次状态与报告 |
| `GET /api/scenarios` | 场景模板库列表 |
| `POST /api/compare` | 期望流程 vs 实际事件流 → 流程比对结果 |
| `POST /api/feedback` | 由失败结论生成归因反馈文本 |
| `GET /api/platform/status` | platform 统一入口健康检查（各挂载子应用状态） |
| `GET /api/platform/pages` | 页签注册表（前端按此渲染页签） |

---

## 10. 验收标准

### 10.1 事件监控（FR-1）

1. 输入 CCO/STA 日志 + 规则 JSON，扫描输出事件流/摘要/漂移清单，`rule_drifts` 非空时事件仍命中。
2. cco 规则不匹配 sta 日志、sta 规则不匹配 cco 日志（模块隔离）。
3. sequence 状态机在多节点并行日志中不串台（bucket 隔离）。
4. 跨来源关联以业务锚点（NID/冻结时刻）正确配对。
5. 规则命中率可量化（命中规则数 / 扫描结果总数），随版本回归可比对。

### 10.2 模拟集中器（FR-2）

1. 任务按步骤下发 → 接收 → 匹配 → 判定，结论 JSON 字段完整，verdict 与逐步结果一致。
2. 应答规则（内置 + 任务覆盖 + 步骤级）按预期生效。
3. `expect_no_reply` 场景正确判定（超时 pass / 收到帧 fail）。
4. CLI 退出码：pass=0、fail=1。
5. 与真实 CCO 联调（硬件在环）冒烟：入网/抄读/拉合闸等典型场景通过。

### 10.3 闭环（FR-5，目标）

1. 一次 Run 可串起 烧录 → 监控 → 激励 → 结论 → 报告，全部产物按 run_id 可检索。
2. 期望流程比对能输出缺失/超时/顺序错乱/负向触发四类差异。
3. 失败场景可生成归因反馈并回喂编码侧。
4. 报告含完整证据链（日志路径/帧 hex/规则版本/固件 commit）。

### 10.4 非功能

1. 大日志（>100MB）扫描不 OOM；实时事件产出不阻塞串口写盘。
2. loghooks 运行时故障不影响日志主链路（降级验证用例）。
3. 全量 pytest 保持通过；新增需求配套单测。

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 规则维护成本随固件迭代上升 | 验证失效/漏检 | 命中率量化 + 漂移告警；rules diff 半自动闭环；扫描库自动聚类辅助精选规则 |
| 13762 嵌套 645/698 字段级解析覆盖不足（OAD/OI 覆盖率低，见 `docs/需求管理/归档/oad_todo.md`） | 深层次断言做不了 | 按业务场景优先级补 OAD；用真实抓包 golden data 回放守住解析不回退 |
| 模拟集中器"不够真"骗不过 CCO 状态机 | 假通过/假失败 | 时序参数（帧间隔/超时窗）可配置；协议一致性测试；硬件在环冒烟 |
| 三时基不一致（模块时钟/PC 时钟） | 跨来源关联错位 | 以业务锚点（NID/MAC/冻结时刻/RTUA）关联 + 宽松时间窗；批次统一烧录时刻=0 相对对齐 |
| 海量轮询日志噪音 | 事件流淹没问题 | 规则双重约束（file+msg）；阈值/聚合类规则；事件流分片落盘 |
| 测试数据/凭据污染生产 | 合规问题 | 环境隔离；敏感字段脱敏；报告不落 ESAM 明文 |
| 编排层复杂度失控 | 平台难维护 | 编排层只做"串流程 + 归档"，业务能力全部复用 loghooks/sim_concentrator，不重复实现 |
| 目录迁移破坏导入（143 处绝对导入） | 回归、脚本失效 | 阶段一不移动代码包；阶段二需排期 + 全量回归；一律 `git mv` |
| platform 挂载多应用串口冲突 | 同一串口争用 | 沿用 409 冲突约定（ADR-13），不做后端互斥锁；人工/AI 明确接管权 |

---

## 12. 里程碑规划（建议）

| 阶段 | 内容 | 出口标准 |
|------|------|----------|
| M1（基线） | 现状归档（本文档 v1.1）；**目录阶段一归组（零风险项）**；能力矩阵确认 | 文档评审通过；根目录噪音清理 |
| M2（闭环骨架） | **platform 骨架**：统一入口 + Run 抽象 + 统一报告 schema + /api/run | 一次真实烧录验证可生成完整报告 |
| M3（流程比对） | 期望流程 DSL + 比对器 + 前端渲染（platform 验证工作台页签） | 4 类差异（缺失/超时/乱序/负向）输出正确 |
| M4（强化） | 场景模板库（入网/分钟采集/拉合闸/搜表/升级）；规则命中率指标 | 典型场景一键可跑 |
| M5（归因反馈） | 归因规则 + 反馈组装 + AI 回路演示 | 失败→修复→再验证回路跑通 |
| M6（加固） | 协议 golden 回放、脱敏、性能压测、**platform 一体化 exe 打包** | 全量验收通过 |

---

## 13. 附录

### 13.1 相关文档索引

| 文档 | 位置 |
|------|------|
| 架构决策记录 | `DECISIONS.md`（ADR-1~13） |
| loghooks 设计定稿 | `docs/需求设计方案/loghooks-design.md` |
| 模拟集中器使用手册 | `docs/开发与运维/模拟集中器验证工具使用手册.md` |
| 模块日志使用说明 | `docs/开发与运维/module-serial-usage.md` |
| 打包发布方案 | `docs/需求设计方案/一键打包发布方案.md` |
| 任务交接需求与进度表 | `docs/需求管理/归档/任务交接需求与进度表.md` |
| OAD/OI 覆盖清单 | `docs/需求管理/归档/oad_todo.md` |
| 协议规范 | `docs/协议/`（国网/南网协议、报文格式、DLL 接口说明） |

### 13.2 待评审问题（归档时遗留，供下轮对齐）

1. platform 统一入口端口（建议 8790）与页签顺序；桌面 exe 名称（如「AI 闭环工作台」）。
2. 目录阶段一（零风险归组）是否立即执行、由谁执行；阶段二（apps/libs 分组）的排期与回归窗口。
3. "输入 JSON 打印与事件名"的具体交付形态：规则文件 + 事件名映射表？还是可视化规则编辑器？
4. 验证场景库首期范围（建议：入网 / 分钟采集 / 拉合闸 / 搜表 / 远程升级）。
5. 归因反馈由谁消费：AI 编码模型（接口对接）还是工程师（报告阅读）？两者都要，接口先行。
6. 报告存储：SQLite（与现有索引一致）还是 JSONL 归档目录 + 索引？

---

*本文档为项目级设计需求归档，后续需求变更以追加章节方式演进，架构决策同步追加至 DECISIONS.md。*
