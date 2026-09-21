# REQS-0034 — DONE（完成日志，只追加，最新在上）

## P0 工作台使用优化（BR-1/BR-2/BR-3）完成 ｜ 2026-09-21 ｜ 基线 v1.1

- **BR-1 启动前置检查 + 401 区分**：`run.py` 新增 `_print_startup_preflight()`（启动横幅三行：
  `WORKBENCH_LOCAL_FULL_ACCESS` 当前值+缺省提示、`HPLC_OPEN_WORKBENCH`、健康检查地址）；
  `ai_v2_api._bearer_grant` 缺 token 401 detail 改为
  「缺少 Bearer token；本机 loopback 可在启动时设 WORKBENCH_LOCAL_FULL_ACCESS=1 免 token」，
  无效/过期/撤销分支保留原文案。
- **BR-2 investigation 必读 job 提示**：选**显式字段方案**（取舍：语义清晰、契约自描述，优于 summary 纯文本）——
  `JobEnvelope.follow_up: str | None = None`（可选，默认 null，旧客户端无影响）；`_envelope` 对
  investigation 且非终态（QUEUED/RUNNING）填 `GET /api/ai/v2/jobs/{job_id}`。
- **BR-3 L3 ref 格式示例**：`_l3_items` 格式错误 422 文案带
  「合法格式 listener:<index_id>:<frame_id>，如 listener:idx-xxx:123」。
- **测试**：新增 6 用例（`test_run_preflight.py` 2 + `test_ai_v2_api.py` 4），RED→GREEN 全通过；
  `test_ai_v2_api.py`+contracts+preflight 39 passed；`apps/workbench/` 全量 351 passed，
  7 failed 全为存量（store 5 + profile_loading 2，与 REQS-0033 基线一致，无新增）；
  `verify_api_inventory.py` EXIT=0。
- **范围收口**（变更 2）：P1（mclt 三改进，涉 CCO 日志/06F230 集中器侧）与 P2（BR-5 README 首屏）**暂缓**，另行排期。
