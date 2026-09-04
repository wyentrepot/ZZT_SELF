# TODO — REQS-0023 模拟集中器从模块日志运行时解耦

> 只追加不覆盖。完成一项勾选一项，并在 `DONE.md` 追加记录。
> 阶段门：每完成一个 Commit 停下报告，等用户显式指定下一步。

## 前置 — 方案确认

- [x] Step 0：确认两个决策点——模块日志独立形态**彻底移除** simcon 第三页签；simcon 拆出后采用**进程内 ASGI 子应用**（2026-09-04 用户拍板）

## Commit 1 — workbench 直接挂载 simcon（后端解耦核心）

- [x] Step 1：`apps/workbench/app.py` 新增 simcon 独立挂载（try/except 降级，仿 listener 段），删第 227 行 `/api/simcon` 透传
- [x] Step 2：AI 桥 `_simcon_accessors` / `_simcon_store_accessors`（294-315 行）读取源由 `_ml_sub.state` 改为 `_simcon_sub.state`
- [x] Step 3：串口 Profile 适配器 `simcon_open_io` / `simcon_close_io`（345-355 行）读取源同步改为 `_simcon_sub.state`
- [x] Step 4：跑回归 `test_ai_simcon.py` / `test_ai_v2_api.py` / `test_serial_profile_applier.py` / `test_app.py`，确认 `/api/simcon/*`、AI 桥、串口 Profile 三条链路不回归
- [x] Step 5：提交 Commit 1（`4b3e05f`，只含 workbench 改动，可独立回滚）

## Commit 2 — module_log 摘除 simcon

- [x] Step 6：`apps/module_log/app.py` 删 78-96 行整段（挂载 + 10 个状态提升）
- [x] Step 7：`apps/module_log/static/module-serial.html` + `.js` 删第三页签 `ms-tab-simcon`、页签按钮及 `simconRefreshStatus` 等 JS
- [x] Step 8：`apps/workbench/static/pages/module-serial/`（嵌入版）`.html` / `.js` 同步删第三页签
- [x] Step 9：跑 module_log 回归（`test_module_serial_*`）+ workbench 回归，确认无 simcon 残留引用、无功能回归
- [x] Step 10：提交 Commit 2（`d70ba8f`，纯删除 612 行）

## 收尾

- [x] Step 11：全量回归 + 真机/接口抽查，更新 REQS.md 状态为完成
- [x] Step 12：DONE.md 归档；REQS-INDEX.md 登记 0023 状态
