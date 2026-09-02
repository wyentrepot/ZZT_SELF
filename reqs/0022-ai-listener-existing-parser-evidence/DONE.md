# DONE — REQS-0022 AI 侦听台已有解析复用与分层证据

> 只追加不覆盖。每完成一个 Phase / Step 在末尾追加一条记录。

## 2026-09-02

- **Phase 0 Step 1（完成）**：新建 `reqs/0022-ai-listener-existing-parser-evidence/`
  三件套（`REQS.md` / `TODO.md` / `DONE.md`），原样登记计划的「修订结论」、
  「Global Constraints」、「Frozen API Contract」与四个 Phase 停止门。
- **前置（完成）**：补 `.gitignore` 忽略 `data/listener_*.sqlite*`；API 端点清单与
  本计划文档已推送远程 `4ed9384..1aad124`。
- **Phase 0 Step 2–5（完成）**：
  - 先写引用不存在 fixture 的失败测试，运行确认 FAIL（`fixture 'parser' not found`），
    再补 fixture 与断言，符合 TDD。
  - `parser` fixture 用**真实解析后端** `GwHPLCAnalysis.dll`（`ParserService(DotNetHplcParser)`）；
    DLL 缺失时 `skip`，**不降级为通过**。
  - `raw_0003` fixture 内联一帧真实并发抄表帧（176 字节，源自 `测试文件/并发抄表-样本.txt`），
    使资格测试**不依赖被 `.gitignore` 忽略的样本文件**。
  - 资格确认结果：默认解析已暴露 `FrmType=终端主动并发抄表`、`SNID=00947F69`、
    `SRC/DST`、`APP_ID=0003`、`APP_RAW`（222 hex 字符）。`ChType` 在该帧为 `None`，
    故资格测试不断言它。
  - 另加 `test_temp_index_qualification_report`：用临时 SQLite 建只读索引，只记录
    parser backend、字段是否存在与样本 SHA-256，并断言报告**不含** verdict/pass 之类
    结论字段（资格 ≠ 业务验收）。
  - `docs/api-contract.md` 新增 6.0.1 节登记 listener 分层证据契约。
  - 回归：`apps/listener/test_trace_service.py` + `test_trace_api.py` **29 passed**。
  - 提交 `c5e3af1`（仅 REQS 三件套、`REQS-INDEX.md`、`docs/api-contract.md`、资格测试）。

## 2026-09-03

- **Phase 1 Step 1–5（完成）**：复用 `TraceService` 的 AI `trace_query` 适配。
  - `apps/listener/trace_service.py`：
    - `NormalizedFeature` 新增 `raw_hex_contains: str | None`；`validate_feature` 增加
      `_normalise_hex_condition`（删空白、大写、偶数长度 2–512，否则 `FeatureError`）。
    - **收窄门**：`raw_hex_contains` 无 `app_id`/NID/时间窗/帧 ID 窗口任一收窄条件 → `FeatureError`。
    - `_ROW_SQL` 末尾追加读取现有 `raw_hex` 列（**不改 schema**）；`_to_frame` 透传 `raw_hex`；
      `_apply_l2_filters` 增加 `raw_hex_contains` 末端子串命中。
    - `run_replay` / `_flow_report` 增加 `directions` 参数；`_flow_report` 末尾构建 `frames`
      方向投影（downlink 含 sent/retransmission/confirm、uplink 为 response、ack 为 ack），
      按 `frame_id` 排序后再按 `directions` 白名单过滤。新增静态方法 `_frame_projection`
      输出 14 字段（frame_id/log_time/direction/role/frm_type/nid/src/dst/ori_s/ch_type/
      app_id/msg_seq/flow_dir/meter_addrs）。
  - `apps/workbench/ai_operations.py`：新增 `trace_query` 分发与五个方法
    `_normalise_trace_query_match` / `_trace_query_window` / `_create_listener_trace_query_observation` /
    `_run_trace_query_observation` / `_trace_query_result`。feature 原样透传；directions 校验
    `downlink/uplink/ack`；观察窗口转 trace 窗口（live → 422）；`raw_hex_contains` 收窄门 422；
    `validate_feature` 的 `FeatureError` → `InvalidObservation`；每帧产 `frame_key=listener:<index_id>:<frame_id>`。
  - 测试：`apps/listener/test_trace_service.py` + `test_trace_api.py` **33 passed**；
    `apps/workbench/test_ai_operations.py` **31 passed**（含 4 个新 trace_query 测试）。
  - v1 回归：`test_ai_trace.py` + `test_ai_api.py` **31 passed**（既有 `/api/ai/v1/listener/traces` 行为不变）。

- **Phase 2 Step 1–5（完成）**：`minute_periods` 适配 + 真正的 L1/L2/L3 分层证据。
  - `apps/workbench/ai_operations.py`：
    - 新增 `minute_periods` 分发与四个方法 `_normalise_minute_periods_match` /
      `_minute_periods_window` / `_create_listener_minute_periods_observation` /
      `_run_minute_periods_observation` / `_minute_periods_result`。
    - `match.kind="minute_periods"` 只收 `task_no`/`period_minutes`/`cco_tei`/`nid`，
      复用既有 `list_task_minute_periods`；窗口仅 `time_range`（live → 422）。
      每个 report 的 `frame_id` 转 `frame_key`，L2 同时给出 `log_time` 与 `freeze_time`。
    - `_trace_query_result` 增加 `total_frames`（回放报告全量帧数，供 L1 计数）。
  - `apps/workbench/ai_capability_service.py`：
    - `read_job_evidence(job_id, level, refs=())` 增加 `refs` 参数。
    - L1：`_listener_evidence_projection` 出范围摘要（index_id/scope/total_frames/flow_groups/
      frame_type_counts/direction_counts/correlation_status/parse_backend/refs），**不含完整帧与 raw_hex**，
      压到 ≤3 KiB。
    - L2：出解析投影（frm_type/nid/src/dst/direction/… + ref；minute 加 freeze_time/response_result），
      ≤16 KiB 且 ≤50 条。
    - L3：`_l3_items` 只对本 job 的 `listener:<index_id>:<frame_id>` ref 回传完整帧 JSON
      （raw_hex/summary/parse_error/analysis/trace_link），越权 `EvidenceRefForbidden` → 403、
      格式错 → 422、>10 个 → 422。
  - `apps/workbench/ai_v2_api.py`：证据端点加 `ref` query 参数，映射 `EvidenceRefForbidden` → 403。
  - 测试：`test_ai_operations.py` 33 passed（+2 minute_periods）、`test_ai_v2_api.py` 19 passed
    （+3 evidence 分层/L3 越权/minute freeze_time）；全量 v2 回归 83 passed in 7.58s。
  - 提交 `1c22360`（Phase 1）；Phase 2 scoped 提交待做。

- **Phase 3 Step 1–4（完成）**：能力声明、文档与性能门。
  - `apps/workbench/ai_operations.py`：`listener_schema()` 扩展声明 `match_kinds`
    （parsed_frame/frame_query/trace_query/minute_periods）与 `trace_query` / `minute_periods` /
    `evidence_l3_ref` 三个能力块。
  - `tools/scripts/verify_api_inventory.py`：库存校验新增 listener 能力声明检查
    （match_kinds 必须含 trace_query/minute_periods，且三能力块存在），报告打印 listener capabilities。
  - 计时回归：`test_listener_trace_query_warm_p95_under_500ms`（20 次温热调用，P95 ≤ 500ms）。
  - 文档：`docs/16-AI操作指南.md` §1.1 补「listener 语义查询与分层证据」；`docs/features.md`
    第 8 节加 REQS-0022 语义查询/分层证据行；`.agents/skills/ai-control-plane/references/listener.md`
    补 v2 trace_query/minute_periods/evidence_l3_ref 声明与 curl 样例。
    （`docs/api-contract.md` §6.0.1 已在 Phase 0 登记，无需改动。）
  - 行为覆盖：`raw_hex_contains` 422 门（Phase 1）、refs 越权 403（Phase 2）、
    跨 index 禁止关联（`test_listener_cursor_range_rejects_invalid_or_cross_index_input`）、
    解析后端降级（`apps/listener/test_remote_degrade.py` 的 `parse_backend=none`）。
  - 全量回归：**117 passed in 10.05s**（6 文件：trace_service/trace_api/ai_v2_api/
    ai_operations/ai_api/ai_trace）。
  - 备注：pytest 退出清理 tmp_path 时会触发环境「批量删除确认」护栏（>50 文件），
    用 `-o tmp_path_retention_policy=all` 保留临时目录即可干净退出；测试本身全部通过。

- **Phase 4 Step 1–4（完成）**：真实样本最终验收。
  - 新增 `tools/scripts/test_listener_ai_existing_parser_sample.py`：只读样本，
    分段读前 N 帧（`--max-lines`）建临时索引（`.tmp/reqs-0022-listener-sample/`），
    复用 `TraceService.run_replay` 与 `list_task_minute_periods`，输出 JSON + markdown 报告。
  - **trace_query（并发抄表）**：303MB 源读前 50000 帧，`app_id=0003` 宽查询得
    **1154 flows / 10743 帧**（downlink 9952 / uplink 249 / ack 542），NID=00947F69；
    L3 `get_index_frame` 含 raw_hex/summary/analysis。温热收窄（app_id+NID+时间窗）P95=8.0ms。
  - **minute_periods（分钟采集）**：4 个 COM4_*.txt 共 46296 帧，37 条 00E4 记录，
    task_no=1/cco_tei=001，00E2 配置推导 2 分钟周期；freeze_time（权威）/response_result/frame_id 验证通过。
  - 全量回归：**151 passed in 14.88s**（7 文件，含 test_log_service.py）。
  - 验收报告：`docs/验收记录/REQS-0022-侦听台默认解析AI检索验收.md`。
  - 覆盖说明：303MB 全量索引与全文件 SHA-256 记 `coverage_missing`（只读前 50000 帧）。

- **Phase 2 bug 修复（Phase 4 中暴露）**：`list_task_minute_periods` 实际返回信封 dict
  （`{"task_no","source","periods":[...],"unconfigured_reports":[...]}`），而 Phase 2 的
  `_minute_periods_result` 误按列表处理。已兼容 dict/list，并修正测试 fake 返回真实 dict 结构
  （此前 fake 用列表掩盖了该 bug）。另外无 00E2 配置且不传 period_minutes 时该方法返回空，
  属既有语义（需 period_minutes 回退），验收脚本据此传 `period_minutes=15`。
