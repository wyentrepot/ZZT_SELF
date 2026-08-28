# TODO.md — 需求 0008：模拟集中器帧日志持久化 + AI 控制面 simcon 接口

> 执行纪律：RED 先行（先失败测试再实现）；全部测试假串口，不打开真实 COM。

## 1. 帧日志核心（✅ 已完成）

- [x] `libs/sim_concentrator/journal.py`：FrameJournal（append/scope/查询过滤/JSONL/内存镜像）+ SessionManager（保留 10 会话）
- [x] `serial_io.py`：可选 journal 注入；`send_frame` 记 tx、读线程记 rx（签名不变）
- [x] `journal.py` 单测 11 例（过滤/游标/打标/落盘/环形上限/会话保留）

## 2. 执行与 API 层（✅ 已完成）

- [x] `runner.py`：`execute_task` journal scope（run_id + frames_seq）+ `run_single_step`（send/recv_only 语义）
- [x] `api.py`：`POST /step`、`GET /frames`、`GET /session`；`/status`、`/verify`、`/open` 增量字段；state 访问器（run_verify/run_step/frames/session/open）
- [x] API 测试 12 例（TestClient + FakeSerialIO，journal_dir=tmp_path）

## 3. AI 控制面（✅ 已完成）

- [x] `module_log/app.py` 提升访问器 → `workbench/app.py` `SimconAIService` 桥 → `AIControlService(simcon_service=...)`
- [x] `ai_operations.py`：`simcon_verify`（异步 operation + 并发闸门）、`simcon_step`（同步+幂等）、`simcon_frames/session/open/close`
- [x] `ai_api.py`：6 条 `/api/ai/v1/simcon/*` 路由 + 3 新 scope + audit + 错误映射
- [x] AI 测试 14 例（401/403/503/202 异步流/409 并发/幂等/422/404/audit）

## 4. 文档与决策（✅ 已完成）

- [x] `docs/16-AI操作指南.md` 第 9 节（模拟集中器）+ scope 表 + checklist（原 9-12 顺延 10-13）
- [x] `.agents/skills/ai-control-plane/SKILL.md` 1.2.0：第七步 + 授权示例 scope + checklist
- [x] `DECISIONS.md` ADR-8
- [x] 本需求登记 REQS-INDEX 0008
