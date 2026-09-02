# AI 侦听台已有解析复用与分层证据 Implementation Plan

> **状态：⛔ 暂不执行。** 本文只修改计划，不修改生产代码、不建立样本索引、不执行最终测试。开始任一 Phase 前必须获得用户明确指令；Phase 4 的样本最终验收还必须获得单独的“开始 Phase 4”确认。
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 控制面直接复用侦听台已经解析并索引的帧类型、NID、源/目的 TEI、方向、APP_ID、APP_RAW、时间和通信流状态机，按 L1/L2/L3 返回检索范围、解析摘要和受控的完整 JSON。

**Architecture:** `LogFileService` 仍是唯一的帧索引与 `index_id` 真相；`TraceService` 仍是并发抄表等应用帧的双向、多跳、同 `msg_seq` 关联内核；`minute_reports` 仍是分钟采集的权威数据源。AI v2 只增加适配/投影：将既有 trace 或 minute 查询压缩为 L1/L2，并按已返回的帧引用下钻 L3，绝不复制或重新分类已有解析数据。

**Tech Stack:** Python 3、FastAPI、Pydantic、SQLite、现有 GwHPLCAnalysis 解析摘要、pytest/TestClient。

## 修订结论

侦听台默认解析已经能表达截图里的“帧类型、NID、源→目的、信道、长度、状态”。`LogFileService` 将解析摘要保存为 `summary_json`，并物化 NID、帧类型、APP_ID、`msg_seq`、`flow_dir`、TEI、表地址和 ACK 对端。

`TraceService` 已可按 `app_id`、`msg_seq`、`dst_tei`、NID、时间窗、帧 ID 窗口、帧类型、信道、`APP_RAW` 子串筛选；它加载上下行和 ACK，再按 `app_id + msg_seq + 时间簇` 组成多跳通信流。并发抄表使用 `APP_ID=0003`，分钟采集直接走既有 `minute_reports` / `listener/minute-periods`。

本计划**不再**新增 `business_kind`、地址倒排表、紧凑 HEX 列或第二个搜索服务。真正缺口是：AI v2 尚未把既有 trace/minute 能力组合成一次受控查询；其通用 listener observation 目前只允许 `frame_kind=central_beacon` 和深层 `eq`，而 v2 的 L1/L2 对 listener 没有语义分层，L3 只返回引用。

## Global Constraints

- 新需求登记为 `REQS-0022`，确认后才创建 `reqs/0022-ai-listener-existing-parser-evidence/`；变更只追加，不覆盖任何现有 REQS。
- 不修改 `frames`、`minute_reports` 的表结构、现有索引规则、默认页面解析路径或 v1 `/api/ai/v1/listener/*` 合同。
- 并发抄表、单表、00A1、0020、0008 一律使用既有 TraceService；分钟采集一律使用既有 `list_task_minute_periods`，其业务归属仍以 `freeze_time` 为准。
- `sequence` 是日志采集序号，不能作为协议关联键；多中转关联只使用同一 `index_id` 内的 `app_id + msg_seq + NID + 时间簇`。无 `msg_seq` 的帧必须标记 `correlation_status=unavailable`。
- `app_raw_contains` 复用现有 APP 层 HEX 条件；新增 `raw_hex_contains` 时仅作为既有 `app_id`、NID、时间窗或帧 ID 窗口收窄后的末端验证。它删除空白后必须为偶数个十六进制字符，长度 2–512；没有任何收窄条件时返回 422。
- L1 最大 3 KiB；L2 最大 16 KiB 且最多 50 条；L3 每次最多 10 个、且只允许读取同一 job 的 `listener:<index_id>:<frame_id>` 引用。
- `parse_backend=none` 时保留索引和原始帧查询；缺解析字段返回 `parse_unavailable`，不得把未解析帧归入并发抄表或分钟采集。

## Frozen API Contract

保持 `POST /api/ai/v2/investigations`。新增 listener 的 `match.kind="trace_query"`，其中 `feature` 复用现有 TraceService 字段：

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

`directions` 是 L2 展示与计数筛选；同一通信流默认保留必要 ACK 和对端帧。`raw_hex_contains` 对整帧 `raw_hex` 验证；`app_raw_contains` 对已解析应用载荷验证。地址已被解析时优先放入 `app_raw_contains` / L2 `meter_addrs`，不要求新建地址索引。

分钟采集继续用 `match.kind="minute_periods"`，内部只调用已有 `list_task_minute_periods(task_no, period_minutes, cco_tei, nid, start_time, end_time)`；不得通过 raw HEX 猜测分钟采集。

`GET /api/ai/v2/jobs/{job_id}/evidence?level=L1|L2|L3` 规定为：

- L1：固定 `index_id`、时间窗、过滤器、总帧数、按帧类型/方向计数、通信流组数、`correlation_status`、解析后端和可下钻 refs；不返回完整帧。
- L2：最多 50 个既有解析投影：`index_id`、`frame_id`、`log_time`、`FrmType`、NID、`SRC`、`DST`、`ORI_S`、`ChType`、APP_ID、`msg_seq`、`flow_dir`、`meter_addrs`、HEX 命中片段、分钟采集 `freeze_time` / `response_result`（若适用）和 ref。
- L3：使用一个或多个 `ref=listener:<index_id>:<frame_id>`；服务端只对该 job 的 L2 refs 调用既有 `get_index_frame`，返回 `raw_hex`、`summary`、`parse_error`、`analysis` 和 trace 链接。无关 ref 为 403，格式错误为 422。

## Task 0 / Phase 0: REQS 基线与默认解析资格确认

**Files:**
- Create: `reqs/0022-ai-listener-existing-parser-evidence/REQS.md`
- Create: `reqs/0022-ai-listener-existing-parser-evidence/TODO.md`
- Create: `reqs/0022-ai-listener-existing-parser-evidence/DONE.md`
- Modify: `REQS-INDEX.md`
- Modify: `docs/api-contract.md`
- Modify: `apps/listener/test_trace_service.py`

**Interfaces:**
- Consumes: 默认解析字段 `FrmType`、`SNID`、`SRC`、`DST`、`ORI_S`、`ChType`、`APP_ID`、`APP_RAW` 和既有 trace 物化字段。
- Produces: REQS-0022 生效基线，以及样本中可用于并发抄表/分钟采集验收的真实字段目录。

- [ ] **Step 1: 创建 REQS-0022，并原样登记 Frozen API Contract 与四个 Phase 停止门。**

- [ ] **Step 2: 写失败测试，使用 parser fixture 验证一帧并发抄表摘要含 `FrmType`、NID、源/目的、APP_ID 和 APP_RAW。**

```python
def test_default_summary_exposes_parallel_meter_reading_fields(parser, raw_0003):
    simple = parser.parse_summary(raw_0003)["simple"]
    assert simple["APP_ID"] == "0003"
    assert simple["FrmType"] == "终端主动并发抄表"
    assert simple["SNID"]
    assert simple["SRC"] and simple["DST"]
```

- [ ] **Step 3: 运行失败测试。**

Run: `pytest apps/listener/test_trace_service.py -k "default_summary_exposes_parallel_meter_reading_fields" -v`

Expected: FAIL，测试尚未写入。

- [ ] **Step 4: 添加 fixture 和断言；使用临时 SQLite 为原始样本建立只读资格报告，但不作为最终验收。**

Run: `pytest apps/listener/test_trace_service.py -k "default_summary_exposes_parallel_meter_reading_fields" -v`

Expected: PASS；报告只记录 parser backend、字段是否存在和样本 SHA-256，不声明业务最终验收通过。

- [ ] **Step 5: 提交仅 REQS 与资格测试。**

Run: `git add REQS-INDEX.md reqs/0022-ai-listener-existing-parser-evidence docs/api-contract.md apps/listener/test_trace_service.py && git diff --check --cached && git commit -m "docs: define AI listener parser evidence contract"`

Expected: 暂存清单仅含上述路径；样本原文件、临时 SQLite 和 runtime 索引均不进入 Git。

## Task 1 / Phase 1: 复用 TraceService 的 AI 查询适配

**Files:**
- Modify: `apps/listener/trace_service.py`
- Modify: `apps/listener/test_trace_service.py`
- Modify: `apps/listener/test_trace_api.py`
- Modify: `apps/workbench/ai_operations.py`
- Modify: `apps/workbench/test_ai_operations.py`

**Interfaces:**
- Consumes: `TraceService.run_replay(feature, index_id)`、`NormalizedFeature` 和现有 `frames` 物化列。
- Produces: `AIControlService.create_observation(..., match.kind="trace_query")`，返回既有 trace report 和逐帧 refs。

- [ ] **Step 1: 写失败测试，验证 trace_query 将 `app_id=0003`、`msg_seq`、NID、目的 TEI、时间窗、信道和 APP_RAW 条件原样传给 TraceService。**

```python
assert captured["feature"]["app_id"] == "0003"
assert captured["feature"]["dst_tei"] == "087"
assert captured["window"]["mode"] == "time_range"
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest apps/workbench/test_ai_operations.py -k "trace_query" -v`

Expected: FAIL，提示不支持 `match.kind=trace_query`。

- [ ] **Step 3: 在 TraceService 增加 `raw_hex_contains` 与方向投影。**

```python
def _apply_l2_filters(frames, nf):
    expected = str(nf.raw_hex_contains or "").replace(" ", "").upper()
    return [frame for frame in frames
            if not expected or expected in frame.raw_hex.replace(" ", "").upper()]
```

实现规则：`_ROW_SQL` 读取现有 `raw_hex`；不修改数据库 schema。`directions` 仅筛选 L2 展示/统计，round/flow 仍保留必要 ACK 和对端帧。全帧 HEX 条件没有 `app_id`、NID、时间窗或 cursor 范围时抛 `FeatureError`。

- [ ] **Step 4: 让 AI observation 的 trace_query 调用既有 `run_replay`，把 report 中的每个 frame_id 转成稳定 `index_id + frame_id` 引用。**

- [ ] **Step 5: 运行回归。**

Run: `pytest apps/listener/test_trace_service.py apps/listener/test_trace_api.py apps/workbench/test_ai_operations.py -v`

Expected: PASS；既有 `/api/ai/v1/listener/traces` 回放/实时行为和原有 `frame_query` 保持不变。

## Task 2 / Phase 2: minute_reports 适配和真正的 L1/L2/L3

**Files:**
- Modify: `apps/workbench/ai_contracts.py`
- Modify: `apps/workbench/ai_capability_service.py`
- Modify: `apps/workbench/ai_v2_api.py`
- Modify: `apps/workbench/ai_operations.py`
- Modify: `apps/workbench/test_ai_v2_api.py`
- Modify: `apps/workbench/test_ai_operations.py`

**Interfaces:**
- Consumes: trace_query report、`list_task_minute_periods` 和既有 `EvidenceRef`。
- Produces: v2 的 L1 范围摘要、L2 解析摘要、按 job ref 受控读取的 L3 完整帧 JSON。

- [ ] **Step 1: 写失败测试，验证 L1 不含 `raw_hex`，L2 含截图同类的帧类型/NID/源目的/方向字段，L3 仅对已返回 ref 回传完整 JSON。**

```python
assert "raw_hex" not in client.get(url + "?level=L1").text
assert client.get(url + "?level=L3&ref=listener:idx-a:1").status_code == 200
assert client.get(url + "?level=L3&ref=listener:idx-b:1").status_code == 403
```

- [ ] **Step 2: 运行失败测试。**

Run: `pytest apps/workbench/test_ai_v2_api.py -k "listener and evidence" -v`

Expected: FAIL，提示 L3 不接受 ref 或 L2 没有结构化投影。

- [ ] **Step 3: 增加 `minute_periods` observation，直接复用 `list_task_minute_periods`，将每个 report 的 `frame_id` 作为 L2 ref。**

实现规则：L2 同时提供 `log_time` 与 `freeze_time`，并明确 `freeze_time` 是分钟归属的权威字段；不得用上报时间替代冻结归属。

- [ ] **Step 4: 将 v2 listener evidence 改为分层投影。**

```python
def read_job_evidence(job_id, *, level, refs=()):
    allowed = {f"listener:{ref.index_id}:{ref.frame_id}" for ref in job_refs(job_id)}
    if level == EvidenceLevel.L3 and any(ref not in allowed for ref in refs):
        raise EvidenceRefForbidden()
    return project_listener_evidence(job_id, level=level, refs=refs)
```

- [ ] **Step 5: 运行 v2 回归。**

Run: `pytest apps/workbench/test_ai_v2_api.py apps/workbench/test_ai_operations.py apps/workbench/test_ai_api.py apps/workbench/test_ai_trace.py -v`

Expected: PASS；历史 index_id 在全部三级保持一致，旧 v1 endpoint 与现有 v2 investigation 不破坏。

## Task 3 / Phase 3: 文档、能力发现与性能门

**Files:**
- Modify: `docs/api-contract.md`
- Modify: `docs/16-AI操作指南.md`
- Modify: `docs/features.md`
- Modify: `D:/2-侦听台改造/.agents/skills/ai-control-plane/references/listener.md`
- Modify: `tools/scripts/verify_api_inventory.py`
- Modify: `apps/workbench/test_ai_v2_api.py`

**Interfaces:**
- Consumes: 已通过测试的 trace_query、minute_periods 和 L1/L2/L3 API。
- Produces: capabilities 声明、最小调用样例、HEX 安全边界和速度可核验门。

- [ ] **Step 1: 在 capabilities 声明 `listener.trace_query`、`listener.minute_periods`、`listener.evidence_l3_ref`，并写入 API 合同和 AI 操作指南。**

- [ ] **Step 2: 写 API 库存测试，覆盖 `raw_hex_contains` 422 门、解析后端降级、跨 index 禁止关联和 refs 越权。**

Run: `python tools/scripts/verify_api_inventory.py`

Expected: PASS；命令只构造惰性 stub，不打开串口、不启动侦听台、不烧录。

- [ ] **Step 3: 对 trace_query 添加计时回归。**

Run: `pytest apps/listener/test_trace_service.py apps/workbench/test_ai_v2_api.py -v`

Expected: PASS；带 `app_id + NID + 时间窗` 的温热查询在测试 fixture 的 P95 不高于 500 ms；全帧 HEX 条件缺少收窄条件始终 422。

- [ ] **Step 4: 提交 scoped changes。**

Run: `git add apps/listener apps/workbench tools/scripts docs reqs/0022-ai-listener-existing-parser-evidence REQS-INDEX.md && git diff --check --cached && git commit -m "feat: expose parsed listener evidence to AI"`

Expected: allowlist 之外的既有脏文件不被暂存。

## Task 4 / Phase 4: 用户确认后的最终样本验收

**Files:**
- Create: `tools/scripts/test_listener_ai_existing_parser_sample.py`
- Create: `docs/验收记录/REQS-0022-侦听台默认解析AI检索验收.md`

**Interfaces:**
- Consumes: `测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt` 与 `测试文件/模块快日志/侦听台 - 副本/`，均为只读输入。
- Produces: SHA-256、独立 `index_id`、实际业务数量、L1/L2/L3 报告、并发抄表多跳关联报告和性能结果。

- [ ] **Step 1: 在 `D:\\2-侦听台改造\\.tmp\\reqs-0022-listener-sample` 为每个源文件建立独立临时索引，记录源 SHA-256 与 parser backend。**

Run: `python tools/scripts/test_listener_ai_existing_parser_sample.py --input "测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt" --listener-input "测试文件/模块快日志/侦听台 - 副本" --output "D:\\2-侦听台改造\\.tmp\\reqs-0022-listener-sample" --p95-ms 500`

Expected: 原始日志不变；每个临时索引有独立 index_id；解析后端为 none 时报告 `deep_validation=blocked` 并停止，不把结果记为通过。

- [ ] **Step 2: 对实际存在的并发抄表调用 trace_query，验证 APP_ID=0003、NID、源/目的、上下行、指定 APP_RAW/全帧 HEX 命中和时间窗。**

Expected: L1 返回范围和组数；L2 返回至少一组同 index_id 内的多跳帧；L3 与 `get_index_frame` 的完整 JSON 相同。样本中没有的条件记录 `coverage_missing`，不得记为 PASS。

- [ ] **Step 3: 对模块快日志中实际存在的分钟采集调用 minute_periods，验证 L2 中 `freeze_time`、`response_result`、方向和 ref。**

Expected: 报告明确上报时间与冻结时间；缺少分钟帧时为 `coverage_missing`，不构造替代数据。

- [ ] **Step 4: 输出验收报告并执行全回归。**

Run: `pytest apps/listener/test_log_service.py apps/listener/test_trace_service.py apps/listener/test_trace_api.py apps/workbench/test_ai_operations.py apps/workbench/test_ai_v2_api.py apps/workbench/test_ai_api.py apps/workbench/test_ai_trace.py -v`

Expected: PASS；温热 trace_query P95 不高于 500 ms；报告包含所有源 SHA-256、index_id、实际计数、关联组和无法覆盖项。

## Self-Review

- 默认解析字段、并发抄表 trace、分钟采集、上下行/ACK、地址/HEX、时间窗、同序号多跳、L1/L2/L3、性能、历史 index 隔离和两个样本目录都被 Task 0–4 覆盖。
- 该方案只增加 AI 适配与投影，不新增业务分类索引或改变页面默认解析；已有 `TraceService` 与 `minute_reports` 是唯一业务解释来源。
- 每个 Task 完成后停下；Task 4 需独立确认，任何样本缺少覆盖均为 `coverage_missing` 或 `blocked`，不做虚假通过。

## Execution Handoff

计划已修订并停在此处。若要实施，请明确说“开始 Phase 0”；完成 Phase 0 后我会停下报告。只有在 Phase 0–3 全部通过后，并收到单独的“开始 Phase 4”，才会对指定原始报文和模块快日志执行最终样本验收。
