# DONE.md — 需求 0004 收尾验收记录

> 状态：实现与自动化验证已完成；真机硬件动作未执行（需用户当轮明确授权）。
> 日期：2026-08-20（核对/收尾）
> 范围以 `REQS.md` 为准；当前硬件配置以 `docs/15-硬件资源速查.md` 与用户确认值为准：COM4 侦听台、COM8 CCO 日志、COM9 STA 日志均 115200；COM24 模拟集中器 9600/E/8/1。
> 测试只在 WSL 工作区执行，仅使用 FakeSerial / 临时 SQLite；未启动、发送、烧录任何真实串口。

## 测试命令与结果基线

| 命令 | 结果 |
|---|---|
| `./.venv/bin/python3 -m pytest libs/shared/test_serial_mapping.py libs/shared/test_serial_resources.py -q` | **6 passed** |
| `./.venv/bin/python3 -m pytest apps/module_log -q` | **82 passed**（含 sessions API、动态页签前端、loghooks） |
| `./.venv/bin/python3 -m pytest apps/workbench -q` | **107 passed**（含 AI 控制 API、iframe 保活、打包） |
| `./.venv/bin/python3 -m pytest apps/listener -q --ignore=apps/listener/test_concurrent_meter_e2e.py` | **137 passed, 5 failed**（5 项均为 `FsApiTests`：tkinter 原生对话框在 WSL 不可用，既有测试，非本需求引入） |
| `PYTHONPATH=apps ./.venv/bin/python3 -m workbench.check_assets --strict` | **通过：12 个静态资产完整** |
| `./.venv/bin/python3 -m pytest apps/listener/test_index_registry.py` | **通过**（版本化索引 catalog 回归） |
| `./.venv/bin/python3 -m pytest apps/workbench/test_ai_api.py apps/workbench/test_ai_operations.py` | **通过**（AI 授权/操作/观察任务） |
| `./.venv/bin/python3 -m pytest tools/packaging/test_packaging_resources.py` | **通过**（7 项，含 serial_ports.json 首次安装复制、不覆盖现场配置） |

环境说明：`libs/shared/test_dll_python_meter_consistency.py` 与 `apps/listener/test_concurrent_meter_e2e.py` 在 WSL 下 DLL 加载触发 core dump（SIGABRT），属执行环境问题，与需求 0004 改动无关，未计入上述结果。

## 验收场景对照（REQS §3）

### 1. 侦听台切页保活
- 状态：✅ 实现 + 自动测试。
- 证据：工作台 `apps/workbench/static/app.js` 以 `ensureFrame` 懒加载一次并永久保活的 iframe 容器；切页只改 `hidden`，不改 `src`。`apps/workbench/test_app.py::test_shell_keeps_lazy_iframes_instead_of_reassigning_one_frame` 覆盖。
- 未执行：真实串口启动后切页冒烟（需真机授权）。

### 2. 侦听台刷新状态恢复
- 状态：✅ 实现 + 自动测试。
- 证据：`apps/workbench/static/pages/listener/app.js` 与 `apps/listener/static/app.js` 初始化请求 `/api/listener/serial/status`，`running/starting` 时切到 serial 数据源、回填配置并启动轮询；不因 unload/切页调用 stop。
- 未执行：真机刷新冒烟。

### 3. 统一 JSON 映射生效
- 状态：✅ 实现 + 自动测试。
- 证据：`config/serial_ports.json`（COM4 listener / COM8 cco-main / COM9 sta-main / COM24 simcon，含 115200 与 9600/E/8/1）；`libs/shared/serial_mapping.py` + `serial_resources.py` 共享加载器；侦听台、模块日志、模拟集中器均调用同一 catalog（`libs/sim_concentrator/serial_io.py` 已接入）。
- 未执行：改 JSON 后真机重启软件冒烟。

### 4. 模块日志单页独占
- 状态：✅ 实现 + 自动测试。
- 证据：`apps/module_log/static/module-serial.js` 默认单会话、`renderActiveSession` 只渲染当前会话；`test_module_serial_frontend.py::test_live_view_has_one_current_session_panel_and_dynamic_tab_controls` 覆盖。

### 5. 新增独立页签
- 状态：✅ 实现 + 自动测试。
- 证据：会话 CRUD（`POST/DELETE /api/module-serial/sessions`）、`renderSessionTabs`、每会话独立 `viewStateBySessionId`/游标/端口；测试覆盖新增三页与独立状态。

### 6. 切页不停止后台会话
- 状态：✅ 实现 + 自动测试。
- 证据：`switchSession` 仅切换 `activeSessionId`，不停止会话、不清空 DOM/游标；后端会话独立线程持有串口。

### 7. 关闭页签确认 + 末页补空
- 状态：✅ 实现 + 自动测试。
- 证据：关闭运行页签二次确认（confirm），先 stop 再 delete；删除运行会话后端返回 409 冲突；最后页关闭后自动创建空白默认页。前端/后端测试均覆盖。

### 8. 同物理端口冲突
- 状态：✅ 实现 + 自动测试。
- 证据：`libs/shared/serial_resources.py` 进程内占用表；同映射 id（Windows COM 别名与 Linux 设备归一）重复启动拒绝并指出占用会话；`module_serial_service.py` 冲突路径 + `test_module_serial_service.py` 覆盖。

### 9. 日志文件身份
- 状态：✅ 实现。
- 证据：会话日志命名使用映射 id / label，首条 EVENT 含模块类型、映射名、COM 号与实际设备名（`module_serial_service.py` 日志命名逻辑）。
- 未执行：真机产生日志文件核对内容。

### 10. 对照解析 / 模拟集中器可用
- 状态：✅ 实现 + 自动测试。
- 证据：对照解析按 `session_id` 选择实时来源（`test_module_serial_frontend.py::test_mapping_details_and_dynamic_session_are_available_to_compare_view`）；模拟集中器读取统一 catalog 并保留 `simcon` 映射（9600/E/8/1）。

## 新增/变更文件清单

- 新增：`config/serial_ports.json`、`libs/shared/serial_mapping.py`、`libs/shared/serial_resources.py`、`libs/shared/test_serial_mapping.py`、`libs/shared/test_serial_resources.py`、`apps/listener/index_registry.py`、`apps/listener/test_index_registry.py`、`apps/workbench/ai_api.py`、`apps/workbench/ai_auth.py`、`apps/workbench/ai_operations.py`、`apps/workbench/ai_store.py`、`apps/workbench/test_ai_api.py`、`apps/workbench/test_ai_operations.py`、`tools/packaging/README.md`、`tools/packaging/runtime_hooks/ensure_serial_ports_config.py`、`tools/packaging/test_packaging_resources.py`、`reqs/0004-workbench-serial-sessions/`。
- 修改：`apps/listener/{app,log_service,serial_service,static/app}.py|js`、`apps/module_log/{app,module_serial_service,loghooks_api,static/module-serial.*}`、`apps/workbench/{app,static/app.js,static/index.html,static/styles.css,static/pages/*}`、`libs/sim_concentrator/*`、`tools/packaging/*.spec` 与 `build_exe.bat`、`docs/15-硬件资源速查.md`。

## 已知未完成 / 待办

1. **保留策略接口**：`apps/listener/index_registry.py` 无 delete/prune/retention，`/api/listener/indexes` 仅有 GET——历史索引无清理入口，依赖该接口的"文件清理边界"评审未闭环。
2. **AI 观察能力子集**：module 侧仅支持 `literal` 匹配与 `live/start=now`，无 `loghook_rule/regex/sequence/not_seen` 与 `cursor_range`；listener 侧无 `cursor_range`。与 REQS §25 完整契约有差距，待技能落地前补齐。
3. **API/冒烟使用说明**：独立 API 使用说明与真实硬件冒烟步骤文档未单独成文。
4. **真机动作全部未执行**：COM4/8/9/24 只读枚举、实时串口启动、发送、烧录、模拟集中器交互——均等待用户当轮明确授权。

## 结论

需求 0004 的代码实现与自动化验证已完成；文档收尾（TODO 勾选、DONE.md、docs 波特率修正）已在本轮补齐。剩余为真机验证与上述 3 项待办，均不阻塞代码合入，但真机动作必须由用户授权后单独执行。
