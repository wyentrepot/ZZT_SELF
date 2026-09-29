# REQS-0036 — DONE（完成日志，只追加，最新在上）

## v1.0 全量交付 ｜ 2026-09-29

- **BR-1 代码**：
  - `libs/sim_concentrator/matcher.py`：新增 `_norm_fn`（"F230"/230/"230" 等价，
    非法记法返回 None → 人话原因 `fn 记法非法: 'XX2'（应写 "F230"/230/"230"）`）；
    docstring 写明 afn/fn 记法口径（afn 字符串按 2 位 hex，数值按原始字节值）。
  - `libs/sim_concentrator/runner.py`：expect 等待循环采样未匹配原因（去重），
    超时 reason 追加 `；未匹配原因: AFN 不匹配: 期望0x0A, 实际0x10 | ...`（≤3 条）。
- **BR-2 代码**：
  - `apps/workbench/ai_capability_service.py`：`read_job` 实现 wait_seconds
    （`_OP_PENDING_STATES` 轮询 0.1s，≤30s 上限由路由层约束）；`_summary` 对
    verification_run 终态追加步骤判定 `verification_run succeeded: fail (0 pass/1 fail)`。
- **BR-3 技能（事实源 /home/02-skill-fc/.../ai-control-plane，v2.7.0 → v2.8.0）**：
  - `references/simeter.md` 新增（_meta 优先 / 最小调用链 / 对档联调易踩点）；
  - `SKILL.md`：description+argument-hint 补模拟电表；用途路由表 +2 行；
    v2 速查表 +1 行（investigations 补 simeter）；v1 表 +1 行（并发抄表直连）；
    实测校准 +2 条（expect 记法、v2 job 阻塞读与 summary 判定）；
  - `references/simcon.md`：expect 记法说明 + 修正 recv_only 示例 +
    新增"并发抄表 / 档案 / recipes"直连段（batch_read/recipes/archive/online/
    report_buckets/batch/stats）；
  - `references/auth.md`：scope 表 13→14 种，补 `simeter:read` 行；
  - `references/api-contract.md`：新增 §5.1 simeter 契约表（13 端点 + 环境注记）；
  - `references/operation-guide.md`：同步 docs/16 两处修正 + §9.6 simeter 速览。
- **BR-4 文档**：
  - `docs/16-AI操作指南.md`：§1.1 步骤 3 真实 wait 语义；§9.2 示例字符串记法 +
    记法说明；新增 §9.6 模拟电表速览。
  - `docs/_references/protocols/安徽集中器交互报文.md`：10H-F2 下行补正
    （起始序号 2B 小端 + 数量 1B；实帧 `00 00 10`、CS=0x6B 核对注记）。
- **测试**：`test_runner.py` +3（TestExpectNotation）、
  `apps/workbench/test_ai_capability_read_job.py` +4（新文件）；
  两套件 16 passed。当日此前已完成（REQS-0035 缺口当日修）：
  `ai_v2_api.py` simeter 示例键名 `sources`→`observations`、
  `libs/sim_meter/api.py` _meta 默认地址说明改为出厂 000000000001 +
  `test_serial_api.py` 断言同步（45 passed）。
- **REQS-INDEX**：登记 0036（✅ 已完成）；补登 0035（sim_meter，昨日报表 docs/20）。
