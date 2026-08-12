# loghooks：配置驱动的日志运行状态钩子 —— 设计方案

> 状态：**待确认**（本文档用于对齐设计，尚未实现）
> 适用范围：`listener`（侦听台）/ `module_log`（模块日志）双来源日志
> 目标：让 AI 在改代码、烧录验证时，能通过一组**钩子规则**抓出日志中的关键运行状态事件（入网、采集上报、往网络层发送等），生成摘要 JSON 供 AI 核查功能逻辑，排除海量轮询/状态机噪音。

---

## 1. 背景与现状

### 1.1 两个日志来源（形态完全不同）

| 来源 | 目录 | 行格式 | 内容形态 | 解析能力 |
|------|------|--------|----------|----------|
| **模块日志** `module_log` | `LOG/模块/{cco|sta}/` | `[YYYYMMDD-HH:MM:SS:mmm] [RX|TX|EVENT] 内容` | 文本行，如 `序列号 | info | 文件.c (行号) | 消息` | 靠正则/关键词抠 |
| **侦听台** `listener` | `LOG/侦听台/{port}_{stamp}_自动保存.txt` | `[序号][HH:MM:SS.mmm]7E...7E` | hex 帧 | 可经 `parse_summary` 深度解析出结构化 `simple` dict |

### 1.2 现有解析链路（不侵入）

- `listener`：`LogFileService` 逐行 → `extract_log_record` 抠 hex 帧 → `ParserService.parse_summary`（DLL）出 `simple` dict → `ApplicationAnalysisService.enrich_summary`（`DualMode43Adapter`）富化 → 落库 `frames` / `minute_reports`。
- `simple` dict 关键字段：`FrmType`、`APP_ID`、`SNID`（24 位网络标识）、`ORI_S`（源 TEI）、`FINL_D`（目的 TEI）、`APP_RAW`、`application.fields`（富化后含源 MAC/任务号/冻结时刻/上报数量等）。
- `module_log`：`_SerialChannel._append_line` 写文本行，方向 `RX/TX/EVENT`。

### 1.3 现状痛点

- 模块日志文本行以状态机轮询为主（如 `bpsCheck_state0 trycnt 0..N` 每行重复），关键事件（入网、上报、发送）淹没在海量噪音里。
- AI 核查烧录/运行结果时，面对整份日志难以聚焦"这次烧录后网络是否入网成功、分钟采集是否上报"这类功能逻辑。
---

## 2. 设计目标与决策（已与需求方对齐）

| 决策项 | 结论 |
|--------|------|
| 钩子形态 | **两者都要**：独立离线扫描 CLI + 应用内复用接口 |
| 抓取来源 | **两者都要**：模块日志文本行 + 侦听台 hex 帧 |
| 通用/省份组织 | **配置驱动**：JSON/YAML 规则表，省份=一份规则文件 |
| AI 集成 | **生成摘要文件/JSON 供 AI 读取** |
| 引擎能力 | **支持跨行状态关联**（状态机，把分散多行聚合成一条事件） |
| 应用内接入 | **同时接入 module_log**（运行时产事件，异步降级安全） |
| 跨来源 | **同时关联模块日志与侦听台日志**（同一时间段互相印证） |
| 多规则加载 | **全部 json 同时加载 + 自动识别省份**（不强制指定；`--province` 仅作可选过滤） |
| 来源可扩展 | **来源注册表化**：预留第三来源 `concentrator_10376`（集中器模拟脚本下发 13762 帧） |

---

## 3. 总体架构

```
loghooks/                          # 新增独立包（不侵入现有解析链路）
  __init__.py
  engine.py            # 匹配器 + 状态机 + 事件流（离线/在线共用）
  sequence.py          # 跨行状态机（sequence 原语）
  matchers.py          # text 正则 / field(simple dict) 两种匹配器
  rules.py             # 规则表加载/校验/schema
  sources.py           # 来源注册表：module_log / listener / (预留)concentrator_10376
  correlate.py         # 跨来源关联（模块日志 ↔ 侦听台 ↔ 集中器帧）
  output.py            # 摘要 JSON / 表格
  cli.py               # python -m loghooks scan <log> [--province X] [--auto-detect]
  rules/
    common.json        # 通用规则（所有分支内置）：入网/采集/发送/信标
    provinces/
      anhui.json       # 安徽专属：分钟采集上报闭环
      henan.json       # 河南专属（占位，随需添加）
    extra/
      concentrator_10376.json  # （预留）集中器模拟脚本下发的 13762 帧规则

module_log/
  module_serial_service.py   # （唯一现有代码改动）加可选 run_loghooks 调用点
```

**不改动**：`listener` 现有解析链路、`shared`、`parser_lib`、DLL。

---

## 4. 核心概念：钩子 = 声明式规则

每个钩子是**一条 JSON 规则**，描述"在哪种来源、匹配什么、归为哪类事件、提取哪些字段"。新增省份 = 新增一份规则文件，无需写 Python 类。

### 4.1 两种匹配原语

| 原语 | 说明 | 场景 |
|------|------|------|
| `match`（单行/单帧触发） | 一行/一帧命中即出事件 | `onnet cnt`、00E4 分钟上报帧 |
| `sequence`（跨行状态流） | 多行依序聚合为一条事件 | STA 入网流程、采集闭环 |

### 4.2 单行规则示例（文本，模块日志）

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
    "flags": ["i"]
  },
  "capture": {"node_count": 1},
  "event": {
    "type": "network.onnet",
    "label": "入网节点数",
    "message": "入网节点数 = {node_count}"
  }
}
```

### 4.3 单帧规则示例（field，侦听台）

```json
{
  "id": "anhui.minute_report_e4",
  "category": "collect",
  "scope": "province",
  "province": "anhui",
  "source": ["listener"],
  "match": {
    "mode": "field",
    "field": "APP_ID",
    "op": "==",
    "value": "00E4"
  },
  "capture": {
    "cco_tei": "FINL_D",
    "source_mac": "application.fields.#源MAC地址.value",
    "report_count": "application.fields.#上报数量.raw"
  },
  "event": {
    "type": "collect.minute.e4",
    "label": "分钟采集数据上报",
    "message": "分钟采集上报 CCO={cco_tei} 源MAC={source_mac} 上报数={report_count}"
  }
}
```

### 4.4 跨行状态流示例（sequence，STA 入网流程）

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
  "on_complete": {
    "type": "join.sta.ok",
    "message": "STA 入网成功 NID={nid}"
  },
  "on_timeout": {
    "type": "join.sta.timeout",
    "level": "warn"
  }
}
```

---

## 5. 规则 Schema 字段总表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | str | ✅ | 规则唯一 ID（全局去重） |
| `category` | str | ✅ | 事件大类：`join`/`collect`/`send`/`beacon`/`state`/`flash`，省份可自定义 |
| `level` | str | | `info`/`warn`/`error`，默认 `info` |
| `scope` | str | ✅ | `common`（所有分支）/ `province`（省份专属） |
| `province` | str | 当 scope=province | 省名，如 `anhui` |
| `source` | list | ✅ | 适用的来源：`module_log`/`listener` |
| `match` | obj | 二选一 | 单行触发规则（mode=text 或 field） |
| `sequence` | list | 二选一 | 跨行状态流规则（与 match 互斥） |
| `capture` | obj | | 从匹配组/字段提取的事件字段 |
| `event` | obj | ✅ | 事件类型/标签/消息模板 |

### 5.1 `match` 两种模式

**`mode: "text"`**（文本正则）：
```json
{"mode": "text", "pattern": "onnet cnt = (\\d+)", "flags": ["i"]}
```

**`mode: "field"`**（simple dict 字段，侦听台帧）：
```json
{"mode": "field", "field": "APP_ID", "op": "==", "value": "00E4"}
```
- `op` 支持：`==`、`!=`、`contains`、`startswith`、`regex`。
- `field` 支持点号路径 + `#字段名`（按中文名取 application.fields 里的字段）：如 `application.fields.#源MAC地址.value`。

### 5.2 `capture` 取值方式

- 文本模式：`{字段名: 捕获组序号}`，如 `{"node_count": 1}`。
- 字段模式：`{字段名: simple字段路径}`，如 `{"cco_tei": "FINL_D"}`。
- 事件 `message` 用 `{字段名}` 模板插值。

---

---

## 5.5 规则加载与自动识别（多 json 同时支持）

**核心诉求**：不强制用户每次指定省份；默认**加载全部规则文件 + 自动识别适用规则**，让"全部省份一起跑、能命中就命中"成为默认行为。

### 5.5.1 目录即规则源，全部加载

```
loghooks/rules/
  common.json        # 通用规则（永远加载）
  provinces/
    anhui.json       # 安徽
    henan.json       # 河南
    ...              # 新省份 = 放一个 json，无需改代码
```

- **加载规则**：默认把 `common.json` + `provinces/` 下**所有** json 一并加载，不做任何省选择。
- 规则通过 `scope` / `province` 字段**自带归属**，引擎加载后按其归属组织，而不是按目录区分运行与否。
- `--province` 仍是**可选过滤器**：不传 = 全部加载；传了 = 只保留该省的 province 规则 + 全部 common 规则。

### 5.5.2 自动识别省份（无需指定）

每条规则命中时，引擎自动记录**命中的 `scope`/`province`**。扫描结束后，根据命中分布自动给出"这份日志疑似来自哪个省份"的判定：

```
"detected_provinces": [
  {"province": "anhui", "rules_hit": ["anhui.minute_report_e4", ...], "confidence": "high"},
  {"province": null,    "rules_hit": ["common.join_onnet", ...], "confidence": "common-only"}
]
```

- 命中**省份专属规则**（如安徽 00E4 分钟上报）→ 判定该省，confidence=high。
- 只命中 common 规则 → 判定"无省份特征"（common-only）。
- 多条省份规则同时命中不同省 → 全部列出，由上层（AI/人）决定，**不武断二选一**。

### 5.5.3 多 json 带来的规则冲突与隔离

- **ID 全局唯一**：加载时校验所有 json 的 `id` 不重复，冲突直接报 schema 错误（而不是静默覆盖）。
- **优先级**：若同名事件类型来自 common 与 province，province 规则**覆盖/补充** common 的同 `event.type` 标签与消息（province 更具体），但两者都计入命中统计。
- **命名空间**：建议 `id` 前缀体现来源，如 `common.*` / `anhui.*` / `henan.*`，便于日志里溯源是哪个规则命中。

### 5.5.4 供 CLI 的交互

```
python -m loghooks scan <log>
    --province anhui      # 可选：只保留该省规则（不传 = 全部）
    --auto-detect         # 默认开：扫描后给出 detected_provinces 判定
    --list-provinces      # 只列出可用省份与规则数，不扫描
```
## 6. 双来源适配（sources.py）

规则通过 `source` 声明适用来源，引擎按来源路由到不同解析器：

| 来源 | 输入 | 预处理 | 匹配对象 |
|------|------|--------|----------|
| `module_log` | 文本行 `[ts][dir] 内容` | 解析出 `时间/方向/内容`；内容形如 `序列号|info|文件.c(行)|消息`，再拆出 `文件/消息` | 对 `消息` 跑 text 正则 |
| `listener` | hex 帧行 `[序号][ts]7E...7E` | 调 `ParserService.parse_summary` 或读已落库 `summary_json` | 对 `simple` dict 跑 field 匹配 |

**离线 scan 对侦听台**：优先复用现有 `LogFileService`/`ParserService`（`listener.log_service`、`shared.parser_service`）对帧做解析，得到 `simple` dict 再喂 field 规则——不重复实现帧解析。

---

---

## 6.5 第三来源：集中器模拟脚本（13762 帧下发）—— 预留空缺

**背景**：后续会新增一个脚本，模拟 **13762（Q/GDW 10376.2）集中器模块**，向 CCO 模块**主动下发 13762 帧**（AFN/PN-FN 信封，用户数据内嵌套 645/698 帧，如集中器抄读、路由查询/控制等）。这会成为继"模块日志文本 / 侦听台 hex 帧"之后的**第三种帧来源**。

**现状**：项目已内置 `parser_lib/adapters/adapter_10376`（Q/GDW 10376.2 采集终端与通信模块接口适配器），能解 AFN/SEQ/RTUA 信封并递归调用 645/698 适配器解内部嵌套帧。因此 13762 帧的**解析能力已具备**，loghooks 只需把它接入为一种 `source`。

### 6.5.1 把"来源"抽象为可扩展注册表

规则表里的 `source` 字段从固定枚举扩展为**可注册的来源类型**，每种来源对应一个解析器（输入 → 待匹配对象）：

| source 值 | 说明 | 解析器 |
|-----------|------|--------|
| `module_log` | 模块日志文本行 | 文本 → 消息字符串 |
| `listener` | 侦听台 hex 帧 | 帧 → simple dict |
| `concentrator_10376`（新增） | 集中器模拟脚本下发的 13762 帧 | 帧 → `adapter_10376` 解析出的信封+嵌套字段 dict |

- 新增来源 = 在 `sources.py` 注册一个解析器函数，**规则表结构不变**。
- 13762 帧规则的 `match.mode` 用 `field`，可匹配：
  - 信封字段：`AFN`（如 `02H 数据转发`）、`RTUA`（终端地址）、`SEQ`。
  - 嵌套字段：`nested.fields.#数据标识`、内部 645/698 帧的字段。
  - 例：`{"mode":"field","field":"AFN","op":"==","value":"02"}`（数据转发）。

### 6.5.2 13762 帧可能的日志落点

模拟脚本下发的 13762 帧可能以两种方式进入日志，loghooks 的 `sources` 都要兼容：

1. **脚本自身落盘**：脚本把发出的 13762 帧写成文本行（`[ts] TX 68...16`），走 `concentrator_10376` 来源解析。
2. **经串口/侦听台抓包**：帧出现在侦听台采集文件里，此时仍走 `listener` 来源；需按"是否 10376 帧"区分（帧头 `68H L 68H` 且非 7E 双模帧）。engine 可对同一帧尝试多来源解析器，命中哪个算哪个。

### 6.5.3 与现有来源的关联印证

13762 帧来源同样参与**跨来源关联**（见第 8 节）：集中器下发 13762 抄读帧 ↔ CCO 模块日志的对应处理打印 ↔ 侦听台抓到的上行帧，三者以 **RTUA/地址/任务标识** 为业务锚点互相印证。

### 6.5.4 预留结论

- **loghooks 不因新增 13762 来源而改结构**：`source` 是开放注册表，加一个解析器即可。
- 首版实现 `module_log` / `listener` 两来源；`concentrator_10376` 作为**已预留的第三来源**，解析器接口（输入 → dict）从首版就按此设计，后续脚本就绪后补一个注册项即接入。
- `rules/provinces/` 与 `rules/extra/`（非省份的其他帧类型，如集中器帧）都可承载 13762 规则。
## 7. 跨行状态机（sequence.py）

- 引擎维护**进行中的状态机集合**，按 `bucket` 键分桶（默认取 sequence 各步捕获的第一个 id 类字段，如 NID/MAC；也支持显式 `bucket_field`），支持**多节点并行入网/上报不串台**。
- 状态机**依序推进** step，每个 step 命中后记录时间；进入下一 step 前校验 `window_ms` 超时。
- 全部 step 依序命中 → 触发 `on_complete` 事件；中途超时 → `on_timeout` 事件（可设 level=warn）。
- 支持**数值变化事件**（如 onnet 计数 0→1 的跨行比较）：通过 `delta` 配置比较上一值。

---

## 8. 跨来源关联（correlate.py）—— 关键设计点

### 8.1 时间戳差异（已确认的事实）

| 来源 | 时间戳 | 时基 |
|------|--------|------|
| 模块日志 | `YYYYMMDD-HH:MM:SS:mmm`（带日期） | 模块时钟 |
| 侦听台帧 | `HH:MM:SS.mmm`（无日期，日期在文件名） | PC 时钟 |

**两者时基可能不同（模块时钟 vs PC 时钟），不能裸对时间戳。**

### 8.2 关联策略：以"文件级时间窗口 + 业务锚点"为主

AI 验证时，模块日志与侦听台是**同一时间段**采集的。关联分两层：

1. **文件级窗口**：用户提供"同一时间段"的两个日志（或目录），引擎并行扫描，各自产出带 `time` 的事件流。
2. **业务锚点关联**：用**业务标识**而非原始时间戳互相对齐：
   - **SNID（24 位网络标识 NID）**：模块日志 `NID=0x61475d` ↔ 侦听台 `simple.SNID`。
   - **MAC/TEI**：模块日志 `源MAC/ORI_S/FINL_D` ↔ 侦听台 `application.fields.#源MAC地址` / `ORI_S` / `FINL_D`。
   - **冻结时刻（分钟采集）**：模块日志分钟上报 ↔ 侦听台 00E4 帧的 `freeze_time`——这是分钟采集跨来源验证的**最可靠锚点**（两边都是业务生成的时间，非日志时间戳）。

3. **宽松时间窗**：同一业务锚点下，两边事件在**可配置时间窗**（如 ±5s）内出现，即判定为"互相印证"。

### 8.3 关联输出

摘要中新增 `correlations` 段，例如：
```json
"correlations": [
  {
    "anchor": "nid:61475d",
    "module_log": {"type": "join.sta.ok", "time": "20260811-19:15:08:510"},
    "listener":   {"type": "beacon.recv", "time": "19:15:09:012", "snid": "0061475d"},
    "matched": true
  },
  {
    "anchor": "freeze:2026-07-31 23:55:00",
    "module_log": {"type": "collect.minute.processed", "time": "..."},
    "listener":   {"type": "collect.minute.e4", "time": "...", "source_mac": "340100141223"},
    "matched": true
  }
]
```

---

## 9. 输出产物（output.py）

`scan` 产出两份：

**摘要 JSON**（主产物，`status-summary.json`）：
```json
{
  "source": "module_log",
  "files": ["LOG/模块/cco/xxx.log"],
  "province": "anhui",
  "summary": {
    "join": {"count": 3, "last": {"time": "...", "node_count": 1}},
    "collect.minute": {"count": 12, "samples": [...]},
    "error": {"count": 2, "samples": [...]}
  },
  "events": [
    {"type": "network.onnet", "time": "...", "label": "入网节点数",
     "message": "入网节点数 = 1", "source_line": "[2026...] [RX] ... onnet cnt = 1"}
  ],
  "correlations": [...],
  "unmatched_unknown": 0
}
```

**精简表格**（可选 `--format table`，供人读）。

---

## 10. 应用内接入（module_log 运行时产事件）

在 `module_log/module_serial_service.py` 的 `_SerialChannel._append_line` 写盘之后，加**可选**调用点：

```
_append_line(direction, text)  →  写原始日志（现有）  +  run_loghooks(direction, text)（新增）
```

设计约束：
- **异步 + 队列**消费（不阻塞串口写盘、不拖慢日志采集）。
- 事件实时落盘到 `LOG/模块/事件/<channel>/*.jsonl`（每行一条事件），与原始日志分离。
- **可开关**：配置/环境变量 `LOG_HOOKS_ENABLED`，默认开但**失败静默降级**——绝不影响现有日志主链路。
- 规则从 `loghooks/rules/` 自动加载，加省份规则 = 放一个 json，无需改 module_log 代码（接入点是通用的，规则是配置的）。

---

## 11. 首版落地范围（本迭代）

首版聚焦"AI 烧录验证能跑通"，覆盖：

1. `loghooks` 包骨架：`engine / sequence / matchers / rules / sources / correlate / output / cli`。
2. `rules/common.json` 通用规则（先写最确定的几条）：
   - 入网：`onnet cnt`（CCO）、`devCntToNwk`（发送/入网计数）、STA 入网流程（sequence：nwk disc done → start nwk assoc → nwk track done succ → recv bcn）。
3. `rules/provinces/anhui.json`：安徽分钟采集上报闭环（00E4 帧 field 规则 + 冻结时刻锚点）。
4. 规则加载：**默认加载 common + provinces/ 下全部 json**，`--province` 可选过滤；扫描后输出 `detected_provinces` 自动识别判定。
5. `cli.py` 离线 scan：支持 `--source module_log|listener`、`--province`、`--auto-detect`、`--list-provinces`、`--correlate <另一日志>`、`--format json|table`。
6. module_log 运行时接入点（异步队列 + 降级安全）。
7. 来源注册表化：首版实现 `module_log`/`listener` 两来源，按"输入 → dict"统一解析器接口，**为第三来源 `concentrator_10376`（13762 帧）预留注册位**（解析能力复用现有 `adapter_10376`，首版不接入，仅留接口）。
8. 测试：真实日志样例 fixture + 引擎/状态机/规则 schema 校验（含多 json 冲突/自动识别用例）。

**本迭代不改**：listener 现有链路、shared、parser_lib、DLL。

---

## 12. 后续可扩展（不在首版）

- 侦听台深度关联可视化（在现有 listener 界面加"运行状态"页）。
- 更多省份规则（河南/重庆等，随需求添加）。
- 阈值/趋势类聚合规则（同一事件出现 N 次才报）。
- 把关联结果回写现有 `minute_reports` / 状态库。

---

## 13. 待确认/风险点

1. **时基对齐**：模块日志（模块时钟）与侦听台（PC 时钟）存在时钟偏差风险，跨来源关联以业务锚点（NID/MAC/冻结时刻）+ 宽松时间窗为主。若同一验证批次中两边时钟一致，可进一步精确对齐。
2. **规则匹配误报**：`trycnt` 等高频轮询行的正则可能误命中，需在规则里用"源文件 + 消息"双重约束降低误报。
3. **module_log 接入**：需要确认现有 `_append_line` 调用频度（是否每条 RX 行都调），以设计合理的异步队列背压，避免内存积压。

---

*本文档为设计方案，待你确认后进入实现。*
