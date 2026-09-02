# REQS-0022 — AI 侦听台已有解析复用与分层证据

> 状态：🚧 Phase 0 进行中（基线登记 + 默认解析资格确认）
>
> 创建：2026-09-02
>
> 代码基线：`master` / `1aad124`
>
> 计划来源：[`docs/superpowers/plans/2026-09-02-ai-listener-semantic-search.md`](../../docs/superpowers/plans/2026-09-02-ai-listener-semantic-search.md)
>
> 关联：REQS-0009（listener trace 通信流追踪）、REQS-0017（AI 排查方法论）、REQS-0018（只读接收库查询）、REQS-0019（Windows 解析服务）、REQS-0021（AI v2 能力任务门面）

## 1. 背景

侦听台默认解析已经能表达「帧类型、NID、源→目的、信道、长度、状态」。`LogFileService`
把解析摘要存为 `summary_json`，并物化 NID、帧类型、`APP_ID`、`msg_seq`、`flow_dir`、
TEI、表地址和 ACK 对端。`TraceService` 已可按 `app_id`、`msg_seq`、`dst_tei`、NID、
时间窗、帧 ID 窗口、帧类型、信道、`APP_RAW` 子串筛选，并按
`app_id + msg_seq + 时间簇` 组成多跳通信流。

真正的缺口在 AI 侧：**AI v2 尚未把既有 trace / minute 能力组合成一次受控查询**。
其通用 listener observation 目前只允许 `frame_kind=central_beacon` 和深层 `eq`；
v2 的 L1/L2 对 listener 没有语义分层，L3 只返回引用、不带完整帧。

本需求让 AI 控制面**直接复用**侦听台已解析并索引的字段，按 L1/L2/L3 返回检索范围、
解析摘要和受控的完整 JSON。

## 2. 修订结论（不做什么）

本需求**不再**新增 `business_kind`、地址倒排表、紧凑 HEX 列或第二个搜索服务：

- 并发抄表、单表、00A1、0020、0008 一律复用既有 `TraceService`；
- 分钟采集一律复用既有 `minute_reports` / `listener/minute-periods`；
- 地址已被解析时优先放入 `app_raw_contains` / L2 `meter_addrs`，不新建地址索引；
- 本需求只增加 AI 侧的适配与投影，**不复制也不重新分类**已有解析数据。

## 3. 架构约束（Global Constraints）

1. `LogFileService` 仍是唯一的帧索引与 `index_id` 真相；`TraceService` 仍是并发抄表
   等应用帧的双向、多跳、同 `msg_seq` 关联内核；`minute_reports` 仍是分钟采集的权威
   数据源。AI v2 只增加适配/投影。
2. 不修改 `frames`、`minute_reports` 的表结构、现有索引规则、默认页面解析路径或
   v1 `/api/ai/v1/listener/*` 合同。
3. 分钟采集的业务归属**仍以 `freeze_time` 为准**，不得用上报时间替代冻结归属。
4. `sequence` 是日志采集序号，**不能作为协议关联键**；多中转关联只使用同一
   `index_id` 内的 `app_id + msg_seq + NID + 时间簇`。无 `msg_seq` 的帧必须标记
   `correlation_status=unavailable`。
5. `app_raw_contains` 复用现有 APP 层 HEX 条件；新增 `raw_hex_contains` 时**仅作为**
   既有 `app_id`、NID、时间窗或帧 ID 窗口收窄后的末端验证。它删除空白后必须为偶数个
   十六进制字符，长度 2–512；**没有任何收窄条件时返回 422**。
6. 分层上限：L1 最大 3 KiB；L2 最大 16 KiB 且最多 50 条；L3 每次最多 10 个、且只允许
   读取同一 job 的 `listener:<index_id>:<frame_id>` 引用。
7. `parse_backend=none` 时保留索引和原始帧查询；缺解析字段返回 `parse_unavailable`，
   **不得把未解析帧归入并发抄表或分钟采集**。
8. 样本验收中任何无法覆盖的条件一律记 `coverage_missing` 或 `blocked`，**不做虚假通过**。

## 4. 冻结 API 契约（Frozen API Contract）

保持 `POST /api/ai/v2/investigations`。新增 listener 的 `match.kind="trace_query"`，
其中 `feature` 复用现有 TraceService 字段：

```json
{
  "source": "listener",
  "target": {"index_id": "idx-20260630-a"},
  "window": {"mode": "time_range", "start": "00:00:00.000", "end": "00:05:00.000"},
  "match": {
    "kind": "trace_query",
    "scope": "round",
    "feature": {
      "app_id": "0003",
      "msg_seq": "1EC2",
      "frm_type": "终端主动并发抄表",
      "dst_tei": "087",
      "nid": "00947F69",
      "channel": "载波",
      "app_raw_contains": "123456789012",
      "raw_hex_contains": "123456789012"
    },
    "directions": ["downlink", "uplink", "ack"]
  },
  "completion": {"match_count": 1}
}
```

- `directions` 是 L2 展示与计数筛选；同一通信流默认**保留必要 ACK 和对端帧**。
- `raw_hex_contains` 对整帧 `raw_hex` 验证；`app_raw_contains` 对已解析应用载荷验证。
- 分钟采集继续用 `match.kind="minute_periods"`，内部只调用既有
  `list_task_minute_periods(task_no, period_minutes, cco_tei, nid, start_time, end_time)`；
  **不得通过 raw HEX 猜测分钟采集**。

`GET /api/ai/v2/jobs/{job_id}/evidence?level=L1|L2|L3` 规定为：

| 级别 | 内容 |
| --- | --- |
| L1 | 固定 `index_id`、时间窗、过滤器、总帧数、按帧类型/方向计数、通信流组数、`correlation_status`、解析后端和可下钻 refs；**不返回完整帧** |
| L2 | 最多 50 个既有解析投影：`index_id`、`frame_id`、`log_time`、`FrmType`、NID、`SRC`、`DST`、`ORI_S`、`ChType`、`APP_ID`、`msg_seq`、`flow_dir`、`meter_addrs`、HEX 命中片段、分钟采集 `freeze_time` / `response_result`（若适用）和 ref |
| L3 | 使用一个或多个 `ref=listener:<index_id>:<frame_id>`；服务端只对该 job 的 L2 refs 调用既有 `get_index_frame`，返回 `raw_hex`、`summary`、`parse_error`、`analysis` 和 trace 链接。**无关 ref 为 403，格式错误为 422** |

## 5. 目标与验收

| 目标 | 可验证出口 |
| --- | --- |
| 默认解析字段可复用 | 一帧并发抄表摘要含 `FrmType`、`SNID`、`SRC`、`DST`、`APP_ID`、`APP_RAW` 的资格测试通过 |
| trace 能力被 AI 复用 | `trace_query` 把 `app_id`/`msg_seq`/NID/`dst_tei`/时间窗/信道/`APP_RAW` 原样传给 `TraceService` |
| 全帧 HEX 受控 | `raw_hex_contains` 无收窄条件恒为 422；有收窄条件时仅末端验证 |
| 分钟采集权威不变 | `minute_periods` 走既有 `list_task_minute_periods`，L2 同时给出 `log_time` 与 `freeze_time` |
| 证据分层不失真 | L1 无 `raw_hex`；L2 含截图同类字段且 ≤16 KiB/50 条；L3 只认同 job ref，越权 403 |
| 历史隔离 | 全部三级 `index_id` 保持一致；跨 index 禁止关联 |
| 性能可核验 | 带 `app_id + NID + 时间窗` 的温热查询 P95 ≤ 500 ms |

## 6. 非目标

- 不新增业务分类索引、不新增 `business_kind`、不改 `frames` / `minute_reports` 表结构。
- 不新建第二个搜索服务、不做向量检索或全量日志 embedding。
- 不修改 v1 `/api/ai/v1/listener/*` 合同与页面默认解析路径。
- 不把未解析帧归入并发抄表或分钟采集。

## 7. 阶段与人机门

| 阶段 | 交付 | 状态 | 退出条件 |
| --- | --- | --- | --- |
| Phase 0 | REQS-0022 基线 + 默认解析资格确认 | 🚧 进行中 | 三件套、`REQS-INDEX.md`、`docs/api-contract.md` 与资格测试通过 |
| Phase 1 | 复用 `TraceService` 的 AI `trace_query` 适配 | ⏸ 待启动 | `raw_hex_contains` / 方向投影 / `run_replay` 接线；既有 v1 行为不变 |
| Phase 2 | `minute_reports` 适配与真正的 L1/L2/L3 | ⏸ 待启动 | 三层投影、越权 403、格式错 422、旧 v2 investigation 不破坏 |
| Phase 3 | 能力发现、文档与性能门 | ⏸ 待启动 | capabilities 声明、API 库存校验、P95 计时回归通过 |
| Phase 4 | 真实样本最终验收 | ⏸ 待人工确认 | 原始报文与模块快日志只读验收报告；缺项为 `coverage_missing` |

**每个阶段完成后必须停下报告**（做了什么、未做什么、边界、验证证据），等待用户显式
指定下一阶段；不得自动越过阶段。Phase 4 需**单独**的「开始 Phase 4」确认。

## 8. 变更记录

### 变更 1 ｜ 2026-09-02 ｜ 用户 / WorkBuddy

- **改成什么**：新建 REQS-0022，原样登记计划的「修订结论」「Global Constraints」
  「Frozen API Contract」与四个 Phase 停止门。
- **为什么**：让 AI 控制面复用侦听台既有解析与 trace/minute 能力，而不是新建第二套
  搜索服务或再分类一遍业务数据。
- **影响**：后续涉及 `apps/listener/trace_service.py`、`apps/workbench/ai_operations.py`、
  `ai_contracts.py`、`ai_capability_service.py`、`ai_v2_api.py`、相关测试与文档；
  不改变 REQS-0009/0018/0021 已交付行为。
- **被取代**：无（新增需求）。

### 变更 2 ｜ 2026-09-02 ｜ WorkBuddy

- **改成什么**：补 `.gitignore` 忽略 `data/listener_*.sqlite*`，并把 API 端点清单与
  本计划文档推送远程（`4ed9384..1aad124`）。
- **为什么**：侦听台运行时帧索引库是运行时产物，且计划第 3 节明确要求「临时 SQLite
  与 runtime 索引不进入 Git」。
- **影响**：仅 `.gitignore` 与两份文档；无生产代码改动。
