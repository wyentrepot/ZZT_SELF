# TODO — REQS-0022 AI 侦听台已有解析复用与分层证据

> 只追加不覆盖。完成一项勾选一项，并在 `DONE.md` 追加记录。
> 阶段门：每完成一个 Phase 停下报告，等用户显式指定下一阶段；Phase 4 需单独确认。

## Phase 0 — REQS 基线与默认解析资格确认

- [x] Step 1：创建 REQS-0022 三件套，原样登记 Frozen API Contract 与四个 Phase 停止门
- [x] Step 2：写失败测试，用 `parser` fixture 验证一帧并发抄表摘要含 `FrmType`、NID、源/目的、`APP_ID`、`APP_RAW`
- [x] Step 3：运行失败测试（FAIL：`fixture 'parser' not found`）
- [x] Step 4：补 fixture 与断言；用临时 SQLite 为原始样本建立只读资格报告（**不作为最终验收**）
- [x] Step 5：提交仅 REQS 与资格测试（`c5e3af1`）

## Phase 1 — 复用 TraceService 的 AI 查询适配

- [ ] Step 1：写失败测试，验证 `trace_query` 把 `app_id`/`msg_seq`/NID/`dst_tei`/时间窗/信道/`APP_RAW` 原样传给 `TraceService`
- [ ] Step 2：运行失败测试（预期 FAIL：不支持 `match.kind=trace_query`）
- [ ] Step 3：在 `TraceService` 增加 `raw_hex_contains` 与方向投影
  - `_ROW_SQL` 读取现有 `raw_hex`；**不改数据库 schema**
  - `directions` 仅筛选 L2 展示/统计，round/flow 仍保留必要 ACK 和对端帧
  - 全帧 HEX 条件缺 `app_id`、NID、时间窗或 cursor 范围时抛 `FeatureError`
- [ ] Step 4：让 AI observation 的 `trace_query` 调用既有 `run_replay`，把每个 `frame_id` 转成稳定 `index_id + frame_id` 引用
- [ ] Step 5：跑回归（`test_trace_service` / `test_trace_api` / `test_ai_operations`）

## Phase 2 — minute_reports 适配和真正的 L1/L2/L3

- [ ] Step 1：写失败测试，验证 L1 不含 `raw_hex`、L2 含截图同类字段、L3 仅对已返回 ref 回传完整 JSON
- [ ] Step 2：运行失败测试（预期 FAIL）
- [ ] Step 3：增加 `minute_periods` observation，复用 `list_task_minute_periods`，每个 report 的 `frame_id` 作为 L2 ref
  - L2 同时提供 `log_time` 与 `freeze_time`，并明确 `freeze_time` 是分钟归属的权威字段
- [ ] Step 4：把 v2 listener evidence 改为分层投影（L1 ≤3 KiB / L2 ≤16 KiB 且 ≤50 条 / L3 最多 10 个同 job ref）
- [ ] Step 5：跑 v2 回归（`test_ai_v2_api` / `test_ai_operations` / `test_ai_api` / `test_ai_trace`）

## Phase 3 — 文档、能力发现与性能门

- [ ] Step 1：在 capabilities 声明 `listener.trace_query`、`listener.minute_periods`、`listener.evidence_l3_ref`，写入 API 合同与 AI 操作指南
- [ ] Step 2：写 API 库存测试，覆盖 `raw_hex_contains` 422 门、解析后端降级、跨 index 禁止关联、refs 越权
- [ ] Step 3：对 `trace_query` 加计时回归（温热 `app_id + NID + 时间窗` P95 ≤ 500 ms）
- [ ] Step 4：提交 scoped changes（allowlist 之外的既有脏文件不暂存）

## Phase 4 — 用户确认后的最终样本验收

> ⛔ 需**单独**的「开始 Phase 4」确认后才执行。

- [ ] Step 1：在 `D:\2-侦听台改造\.tmp\reqs-0022-listener-sample` 为每个源文件建立独立临时索引，记录源 SHA-256 与 parser backend
- [ ] Step 2：对实际存在的并发抄表调用 `trace_query`，验证 `APP_ID=0003`、NID、源/目的、上下行、指定 `APP_RAW`/全帧 HEX 命中和时间窗
- [ ] Step 3：对模块快日志中实际存在的分钟采集调用 `minute_periods`，验证 L2 中 `freeze_time`、`response_result`、方向和 ref
- [ ] Step 4：输出验收报告并执行全回归

## 待确认 / 风险登记

- Phase 4 源 `测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt` 约 **303 MB**，建临时索引耗时较长，需评估分段。
- `测试文件/` 被 `.gitignore` 整体忽略，样本不属于版本库；验收脚本须容忍样本缺失并记 `coverage_missing`。
