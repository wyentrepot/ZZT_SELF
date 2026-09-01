# REQS-0018 — AI 控制面新增 13762 接收库 / 分钟采集只读查询接口

> 状态：🚧 进行中
> 创建：2026-09-01
> 关联：REQS-0017（AI 排查能力强化）、REQS-0013（1376.2 帧页面化）、ai-control-plane 技能

## 1. 背景与问题

REQS-0017 P0 为 ai-control-plane 技能补充了「离线数据排查」文档，教 AI 直查
`listener_13762.sqlite` / 侦听台索引库。复盘后判定：**结构化库查询更适合走 HTTP 接口**：

1. **无 AI 预留接口**：`/api/ai/v1/*` 已覆盖 侦听台索引帧（`listener/indexes/.../frames`）、
   simcon 会话帧（`simcon/frames`），但**分钟采集分析（minute_reports）与 13762 库
   （report_event / report_meter_data / query_snapshot）无任何 AI 侧接口**。
2. `/api/simcon/store/*` 虽存在但：不在 AI 前缀下、无 token 鉴权、技能文档未收录——AI 不可用。
3. 离线直查的代价：记路径（`.build_plain` vs 开发树）、记表结构、踩列名坑（`time_seconds`
   实为毫秒）、处理 WAL、绕过鉴权/审计。
4. 复用优于新建：页面方法 `list_task_minute_periods` 与 store 方法
   `list_snapshots/snapshot_items/list_report_events` 已存在且被 UI 测试覆盖——AI 侧只需薄包装代理。

## 2. 目标

新增 4 个只读端点，复用现有 service 方法，scope 复用既有读 scope（不发明新 scope）：

| 端点 | 包装方法 | scope | 数据 |
| --- | --- | --- | --- |
| `GET /api/ai/v1/listener/minute-periods` | `log_service.list_task_minute_periods` | `evidence:read` + listener resource | 分钟采集分桶/缺报判定（权威口径） |
| `GET /api/ai/v1/simcon/store/events` | `store.list_report_events` | `simcon:read` | 06H 主动上报事件历史 |
| `GET /api/ai/v1/simcon/store/snapshots` | `store.list_snapshots` | `simcon:read` | 下发查询快照列表 |
| `GET /api/ai/v1/simcon/store/snapshots/{id}` | `store.snapshot_items` | `simcon:read` | 快照明细行 |

同时：技能文档补端点（listener.md / simcon.md + SKILL.md 最小路径表），
offline-analysis.md 标注「结构化查询优先走 API，原始日志/CCO grep 仍离线直查」。

## 3. 边界

- **只读**：全部 GET，不新增任何写能力。
- **不重写查询**：直接代理既有 service/store 方法，杜绝口径分叉。
- **原始日志/CCO grep 不迁移**：帧 hex、串口会话事件、7E 原文仍走离线直查（offline-analysis.md）。
- store 未启用（未装配）时返回 503（与既有 simcon 读接口一致）。

## 4. 验收

- [x] 4 个端点可用，scope 校验正确（evidence:read / simcon:read），无新 scope。
- [x] 参数校验与错误映射：缺 store 503、非法入参 422、无 token 401、无权限 403。
- [x] 新端点返回结构与页面/store 方法一致（口径零分叉）。
- [x] 路由测试通过（test_ai_store_query.py，15 用例全绿）。
- [x] 技能文档更新：listener.md / simcon.md 补端点、SKILL.md 最小路径表、offline-analysis.md API 优先标注。

> 验收通过：2026-09-01。回归 205 passed + 3 项既有环境失败（HEAD 复现，与本次无关）。

## 5. 变更记录

- 2026-09-01 需求建立（用户拍板：单独登记，与 REQS-0017 P1 分析改进解耦）。
- 2026-09-01 全部完成并验收通过（4 端点 + 测试 + 技能文档）。
