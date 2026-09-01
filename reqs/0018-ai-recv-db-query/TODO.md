# REQS-0018 TODO

> 变更记录只追加不覆盖。

## P0 — 后端端点 ✅ 已完成 2026-09-01

- [x] libs/sim_concentrator/api.py：`create_simcon_app` 挂 3 个 store 访问器到 state
      （`simcon_store_snapshots` / `simcon_store_snapshot_items` / `simcon_store_events`，
      store=None 时抛清晰错误）
- [x] apps/module_log/app.py：提升名单加入上述 3 个访问器
- [x] apps/workbench/app.py：`_simcon_accessors` 加入 3 个 + 传入 SimconAIService
      （与核心访问器解耦，库缺失不影响 verify/frames）
- [x] apps/workbench/app.py SimconAIService：加 `store_snapshots/snapshot_items/store_events`
      代理方法（缺访问器/库未启用 → SourceUnavailable 503）
- [x] apps/workbench/ai_operations.py AIControlService：加 `simcon_store_*` 与 `minute_periods`
      代理（缺 simcon store → 503；ValueError → 422）
- [x] apps/workbench/ai_api.py：新增 4 个 GET 路由（minute-periods / store/events /
      store/snapshots / store/snapshots/{id}），scope evidence:read / simcon:read

## P1 — 测试 ✅ 已完成 2026-09-01

- [x] 路由测试：200 结构、401/403/422/503 映射（test_ai_store_query.py，15 用例全绿）
- [x] 口径一致性：minute-periods 透传 log_service 同款方法（无分叉）

## P2 — 技能文档 ✅ 已完成 2026-09-01

- [x] references/listener.md 补 `listener/minute-periods` 端点与示例
- [x] references/simcon.md 补 `simcon/store/events|snapshots` 端点与示例
- [x] SKILL.md 最小路径表「离线数据排查」行更新为 API 优先
- [x] references/offline-analysis.md 标注「结构化查询优先走 API，原始日志才离线直查」

## 验收门

- [x] 4 端点可用且 scope 正确；全测试通过；技能文档就位

## 日志

- 2026-09-01 需求建立（用户拍板单独登记）。
- 2026-09-01 P0/P1/P2 完成：4 端点 + 15 路由测试全绿；回归 205 passed + 3 项既有环境失败
      （HEAD 复现，与本次无关）；技能文档 4 处更新。
