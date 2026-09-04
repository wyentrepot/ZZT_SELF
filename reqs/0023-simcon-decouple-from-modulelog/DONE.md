# DONE.md — REQS-0023 模拟集中器从模块日志运行时解耦

> 2026-09-04 完成 Commit 1 + Commit 2，验收通过。

## 目标回顾

把 simcon 从 module_log 的运行时生命周期拆出，提升为与 listener / module_log 平级的子应用：

1. workbench 统一入口直接挂载 simcon，不再借道 module_log。
2. module_log 回归纯日志/烧录，摘除内嵌 simcon。
3. AI 控制面 simcon 桥 / 串口 Profile 适配器改从 simcon 子应用自身 state 读取。
4. 运行时形态：进程内 ASGI 子应用（用户拍板，不引入独立进程/HTTP）。

## 交付

### Commit 1 — workbench 直接挂载 simcon（`4b3e05f`，+78/-15）

- `apps/workbench/app.py`：
  - `create_workbench_app` 新增 `simcon_factory=None` 注入参数（方案外补充：不加则测试无法隔离真实 simcon）。
  - 删 `/api/simcon` 经 module_log 的透传（原 227 行）。
  - 新增独立 try/except 降级挂载段（紧跟 listener）：`create_simcon_app(prefix="")` → `app.mount("/api/simcon")`，存 `app.state.simcon_mounted`。
  - AI 桥 `_simcon_accessors` / `_simcon_store_accessors`、串口 Profile 适配器 `simcon_open_io` / `simcon_close_io` 读取源 `_ml_sub.state` → `_simcon_sub.state`。
- 测试同步：`test_ai_simcon.py` / `test_ai_v2_api.py` / `test_app.py` 改用 `simcon_factory` 注入假子应用。

### Commit 2 — module_log 摘除 simcon（`d70ba8f`，纯删除 612 行）

- `apps/module_log/app.py`：删 `create_simcon_app` 挂载 + 10 个执行核心状态提升（原 78-96 行）。
- 独立版 `apps/module_log/static/module-serial.html` + `.js`：删第三页签 `ms-tab-simcon`（串口控制 / 应答规则 / 验证任务）+ `simcon*` JS 函数 + `boot()` 里 `simconBind()`。
- 嵌入版 `apps/workbench/static/pages/module-serial/`：`.html` / `.js` 同步删第三页签。
- `test_module_serial_frontend.py`：删「module_log 前端含 simcon 第三页签」契约测试。

## 验收结果

| 验收项 | 结果 |
|---|---|
| workbench `/api/simcon/*` 不回归 | ✅（test_app `test_simcon_mounted` + AI 桥测试通过）|
| module_log 挂载失败 simcon 不再连带 | ✅（独立 try/except 降级）|
| module_log 无 simcon 痕迹 | ✅（grep 零残留；独立/嵌入形态均无挂载、无页签、无 state 提升）|
| AI 控制面 simcon 接口正常，核心缺失 503 | ✅（test_ai_simcon `test_missing_simcon_service_returns_503`）|
| 串口 Profile 一键应用不回归 | ✅（test_serial_profile_applier 8 passed）|
| 四测试文件通过 | ✅（14 + 19 + 8 + 5 全绿）|

- module_log 全量：**83 passed / 2 skipped**。
- workbench：test_app **28 passed + 1 既有失败**（`test_trace_dict_pages_no_cache`：trace/dict 页面 JS 版本号 drift，断言 v1 实为 v2，与本次无关，需单独登记修复）。

## 明确不动（按方案）

- `libs/sim_concentrator/` 包本身（自包含）。
- `apps/workbench/orchestration/adapters/stimulus.py`（直接 `from sim_concentrator.runner import execute_task`）。
- `libs/shared/infra.py`（sys.path 处理）。

## 遗留

1. **`test_trace_dict_pages_no_cache` 既有 drift 失败**：`apps/workbench/static/pages/trace/trace.html` / `dict.html` 已引用 `-v2`，测试仍断言 `-v1`。属独立问题，建议单独登记修复，未混入本次 commit。
2. **真机/接口抽查**：本次为代码层回归（TestClient + 前端静态契约），未做真机串口联调。REQS 验收 5.1 的「真机抽查」建议在日常使用中覆盖。
