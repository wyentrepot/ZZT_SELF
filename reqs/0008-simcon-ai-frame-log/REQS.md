# REQS.md — 需求 0008：模拟集中器帧日志持久化 + AI 控制面 simcon 接口

> **状态**：✅ 已完成（2026-08-29）
> **决策**：ADR-8
> **授权范围**：simcon 帧日志、AI 控制面 simcon 门面；不动 listener/.NET 解析与 13762 协议库语义。

## 1. 目标

1. 模拟集中器与 CCO 的交互报文（tx/rx）**逐帧保存**（内存 + JSONL 落盘），
   可按会话回查；CCO 主动上报帧（上行）可被感知与检索。
2. AI 能：**运行测试用例**（异步 operation）、**单步执行下发指定 AFN**
   （语义化 afn/fn+params）、**感知主动上报**（recv_only 等一帧）。
3. 暴露**会话级查询接口**：本次运行下发过什么帧、CCO 主动上报过什么帧、
   有没有某类 AFN 的上行帧。
4. 接口经 AI 控制面（`/api/ai/v1/simcon/*`）**充分暴露**：token + scope +
   audit，文档与 skill 同步。

## 2. 已确认边界

- 遵守 ADR-5：`send` 只写 `afn/fn + params`，`raw` 传入即报错。
- 串口独占沿用 `SerialResourceRegistry`；冲突 409；测试不碰真实 COM（0007 红线）。
- simcon 层 `/api/simcon/*` 保持页面兼容（只增字段）；AI 门面新增 3 个 scope：
  `simcon:verify` / `simcon:send` / `simcon:read`，resource 固定 `simcon`。
- 层间（simcon → module_log → workbench AI 控制面）进程内状态提升注入，不经 HTTP 回调。

## 3. 验收标准

- [x] 串口收发的每一帧都能在帧日志中检索到（含 afn/fn/方向/解析结果）。
- [x] verify 任务响应携带 `run_id` 与 `frames_seq`，可精确圈定本次运行的帧。
- [x] 单步下发（自动开串口）与 recv_only 等上报可用；raw 被拒绝（422）。
- [x] `/frames` 支持 direction/updown/afn/fn/kind/run_id/游标翻页过滤。
- [x] AI 路由无 token 401、越权 403、并发 verify 409、幂等复用、audit 落账。
- [x] JSONL 落盘至 `data/logs/simcon/`，最近 10 个会话（含临时串口会话）可查。
- [x] docs/16 第 9 节 + ai-control-plane skill 1.2.0 同步；全部测试假串口（487 通过）。
