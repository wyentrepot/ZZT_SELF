# REQS-0023 — 模拟集中器从模块日志运行时解耦（拆为平级子应用）

> 状态：✅ 已完成（Commit 1 + Commit 2 均提交，验收通过）
> 创建：2026-09-04
> 关联：REQS-0008（simcon AI 控制面）、REQS-0015（simcon 表单化）、REQS-0010（工作台 UI 落地，simcon 页签）、REQS-0007（架构解耦）
> 分支：master

## 1. 背景与问题

`sim_concentrator`（模拟集中器，`libs/sim_concentrator/`）在**代码层面**自包含（其 `__init__.py` ADR-10 声明「独立模块，不侵入 listener」），
但**运行时**却被 `apps/module_log/app.py` 用 `app.mount("/api/simcon", …)` **内嵌为模块日志的子应用**，并把 10 个执行核心函数提升到 `module_log.state`。
workbench 统一入口再借道 module_log 把 `/api/simcon` 透传出来，并从 `_ml_sub.state` 二次 `getattr` 读取（`apps/workbench/app.py` 227 / 294-315 / 345-355 行）。

用户 2026-09-04 反馈：「模拟集中器不应该内嵌在日志模块里面」。

**问题本质**：
1. **职责混居**——模拟集中器（设备验证工具）与模块日志（日志/烧录）是两回事，却耦合在同一个子应用生命周期里：module_log 挂载失败时 simcon 一起挂。
2. **两层间接提升脆弱**——simcon 访问器经 `module_log` 再被 `workbench` 读取，状态先落到 `_ml_sub.state`、再经 `getattr` 搬运，任一对不上就静默变 `None`，AI 接口直接 503，且难排查。
3. **前端形态割裂**——workbench 已有独立「模拟集中器」页签（`static/pages/simcon/`），但 module-serial 页面仍残留旧第三页签（`ms-tab-simcon`）。

## 2. 目标

把 simcon 从 module_log 的**运行时生命周期**拆出，提升为与 listener / module_log 平级的子应用：

1. workbench 统一入口**直接挂载** simcon，不再借道 module_log（删 `/api/simcon` 透传）。
2. module_log 回归纯日志/烧录，**摘除内嵌 simcon**（挂载 + 状态提升 + 第三页签）。
3. AI 控制面 simcon 桥 / 串口 Profile 适配器改为从 simcon 子应用自身 state 读取，保持功能不回归。
4. 运行时形态：**进程内 ASGI 子应用**（用户已拍板，不引入独立进程/HTTP）。

## 3. 决策点（已确认）

| 决策 | 结论 |
| --- | --- |
| module_log 独立运行（`python -m module_log.run`，8766）是否保留 simcon 第三页签 | **彻底移除**——simcon 只在 workbench 统一入口下使用 |
| simcon 拆出后运行形态 | **进程内 ASGI 子应用**——仿 listener，最小改动，AI 桥继续进程内注入执行核心 |

## 4. 方案

### Commit 1 — workbench 直接挂载 simcon（后端解耦核心，只动 `apps/workbench/app.py`）

- **删**第 227 行借道 module_log 的透传：`_mount_proxied("module-serial-simcon", _ml_sub, "/api/simcon", sub_root="/api/simcon")`。
- **新增独立挂载**（try/except 降级，仿 listener 段）：
  ```python
  try:
      from sim_concentrator.api import create_simcon_app
      _simcon_sub = create_simcon_app(prefix="", resource_registry=app.state.serial_resource_registry)
      app.mount("/api/simcon", _simcon_sub, name="simcon")
      app.state.simcon_mounted = True
  except Exception as exc:
      _simcon_sub = None
      app.state.simcon_mounted = False
      app.state.simcon_error = str(exc)
  ```
  > `create_simcon_app(prefix="")` 生成相对路由（`/status`、`/ports`、`/open`…），workbench 直接 `app.mount("/api/simcon", …)` 天然命中，**无需 `_PrefixProxy`**；前端 `pages/simcon/` 本就调 `/api/simcon/*`，零改动。
- **AI 桥**（294–315 行）：`_simcon_accessors` / `_simcon_store_accessors` 读取源从 `_ml_sub.state` 改为 `_simcon_sub.state`（`_simcon_sub is None` 时兜底 `None`）。
- **串口 Profile 适配器**（345–355 行）：`simcon_open_io` / `simcon_close_io` 同样改从 `_simcon_sub.state` 读。
- 本 commit 后 module_log 内嵌 simcon 暂时冗余但无害 → **可独立回滚**。

### Commit 2 — module_log 摘除 simcon（`apps/module_log/` + 嵌入版前端）

- `apps/module_log/app.py`：删 78–96 行整段（`create_simcon_app` 挂载 + 10 个状态提升）。
- `apps/module_log/static/module-serial.html` + `module-serial.js`：删第三页签 `ms-tab-simcon` 区块、页签按钮及 `simconRefreshStatus` 等 JS。
- `apps/workbench/static/pages/module-serial/`（嵌入版）`module-serial.html` / `.js`：同步删第三页签（原靠透传，拆出后无源）。

### 明确不动

- `libs/sim_concentrator/` 包本身（自包含）。
- `apps/workbench/orchestration/adapters/stimulus.py`（直接 `from sim_concentrator.runner import execute_task`，不经 module_log）。
- `libs/shared/infra.py`（sys.path 处理）。

## 5. 验收

- [x] workbench 统一入口下 `/api/simcon/*`（status/ports/open/close/build/step/frames/responders/store）功能不回归。
- [x] module_log 挂载失败时，simcon 不再连带不可用（独立 try/except 降级）。
- [x] module_log 独立运行与嵌入形态均无 simcon 痕迹（无内嵌挂载、无第三页签、无 state 提升）。
- [x] AI 控制面 simcon 接口（verify/step/frames/store…）正常；simcon 执行核心缺失时按既有逻辑 503。
- [x] 串口 Profile 一键应用（simcon 槽）不回归。
- [x] `test_ai_simcon.py` / `test_ai_v2_api.py` / `test_serial_profile_applier.py` / `test_app.py` 通过（含把旧「module_log 内嵌 simcon」假设改为「workbench 直挂」）。

## 6. 变更记录

- 2026-09-04 需求建立；方案定稿并记录决策点（模块日志独立形态彻底移除、simcon 采用进程内 ASGI 子应用）。待实施。
- 2026-09-04 Commit 1（`4b3e05f`）：workbench 直挂 simcon（加 simcon_factory 注入参数、删透传、独立挂载段、AI 桥/Profile 读取源切 _simcon_sub）。
- 2026-09-04 Commit 2（`d70ba8f`）：module_log 摘除内嵌 simcon（app.py 挂载段 + 独立版/嵌入版第三页签 + 契约测试删除）。
- 2026-09-04 验收：module_log 全量 83 passed / 2 skipped；workbench simcon 相关测试全绿（14+19+8+5）；
  test_app 28 passed + 1 既有 trace/dict 版本号 drift 失败（与本次无关）。状态：✅ 已完成。
