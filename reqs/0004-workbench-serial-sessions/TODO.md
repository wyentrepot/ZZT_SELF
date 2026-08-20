# TODO.md — 需求 0004 可执行实施计划

> 状态：执行中。范围以 `REQS.md` 为准；当前硬件配置以 `../../docs/15-硬件资源速查.md` 和用户确认值为准：COM4 侦听台、COM8 CCO 日志、COM9 STA 日志均 115200；COM24 模拟集中器为 9600/E/8/1。
>
> WSL 原生仓库只在当前默认 WSL 中编辑、测试、构建和运行 Git。真实串口启动、发送、烧录均不得在自动测试中执行；测试只使用 FakeSerial/临时 SQLite。

## 0. 执行纪律与基线

- [x] 需求、设计和 AI 控制面补充已登记于 `REQS.md`。
- [x] 记录并保留现有用户文档修改与 `docs/_evidence/` 未跟踪内容，不覆盖或删除。
- [x] 运行 `./.venv/bin/python3 -m pytest apps/listener apps/module_log apps/workbench -q`，记录现有失败而非掩盖。（既有失败：`FsApiTests` 5 项——tkinter 原生对话框在 WSL 不可用；`test_dll_python_meter_consistency` / `test_concurrent_meter_e2e` 在 WSL 下 DLL 加载 core dump，均与需求 0004 无关，且为执行环境问题。）
- [x] 运行 `./.venv/bin/python3 -m workbench.check_assets --strict`，记录静态资源基线。（实测：12 个静态资产完整，无缺失/空文件/引用断裂。）
- [x] 每一阶段遵循 RED → GREEN → REFACTOR；阶段完成后运行 `git diff --check` 与 UTF-8 校验。
- [x] 真实硬件阶段前只做只读枚举；实际启动、发送、烧录必须由用户在当次操作明确授权。

## 1. 硬件映射与统一配置

文件：`config/serial_ports.json`、`libs/shared/serial_mapping.py`、`libs/shared/test_serial_mapping.py`、`apps/listener/serial_service.py`、`apps/module_log/module_serial_service.py`、`docs/15-硬件资源速查.md`。

接口：`SerialPortMapping`、`SerialPortCatalog.load()`、`catalog.merge_system_ports()`；端口 API 继续返回旧 `ports`，新增 `port_details` 和可选 `mapping_error`。

- [x] 先写映射加载、重复 ID、非法 module、Windows/WSL 别名、未映射设备、损坏 JSON 降级测试；运行 `./.venv/bin/python3 -m pytest libs/shared/test_serial_mapping.py -q`，预期模块不存在或断言失败。
- [x] 新建唯一外部 JSON：COM4=`listener`/115200、COM8=`cco`/115200、COM9=`sta`/115200、COM24=`simcon`/9600/E/8/1；保留 Linux 别名字段且不把未知 COM23 填作运行目标。
- [x] 最小实现共享加载器和系统端口合并；侦听台、模块日志和模拟集中器读取同一 catalog。
- [x] 更新硬件速查表的 CCO/STA 波特率与当前用户确认值；不得修改无关历史记录。
- [x] 复跑映射测试和相关 ports API 测试，预期全绿；评审接口兼容性和 JSON 缺失降级。

## 2. 工作台保活与侦听台状态恢复

文件：`apps/workbench/static/index.html`、`apps/workbench/static/app.js`、`apps/workbench/static/styles.css`、`apps/workbench/test_app.py`、`apps/listener/static/app.js`、`apps/workbench/static/pages/listener/app.js`、对应前端测试。

接口：一级页签使用加载一次的 iframe 面板；`refreshSerialStatus()` 返回运行态时强制选中 serial 数据源并启动轮询。

- [x] 写 DOM/静态回归测试：切换一级页签不重设已经加载 iframe 的 `src`；运行针对性测试，预期当前单 iframe 行为断言失败。
- [x] 写 listener 初始化回归：后端 `/serial/status` 为 running 时页面切到 serial、回填配置并启动轮询；独立页面和 workbench 副本都覆盖，预期失败。
- [x] 最小实现懒加载且永久保活的三级 iframe 容器，主题广播到所有已加载 iframe。
- [x] 最小实现 listener 后端状态优先恢复；不得在 unload 或切页时调用 stop。
- [x] 运行 workbench/listener 前端测试与 `node --check`，预期通过；人工浏览器冒烟仅打开页面，不启动真实串口。

## 3. 模块日志动态会话后端

文件：`apps/module_log/module_serial_service.py`、`apps/module_log/app.py`、`apps/module_log/test_module_serial_service.py`、`apps/module_log/test_module_serial_api.py`、`apps/module_log/loghooks_api.py`。

接口：`ModuleSerialService.create_session/list_sessions/get_session/update_session/delete_session`；会话 API 位于 `/api/module-serial/sessions`；旧 cco/sta API 保留兼容层。

- [x] 写三会话独立运行、重复物理端口冲突、停止一个不影响其余、日志/发送/烧录按 session 隔离、关闭运行会话为 409 的失败测试。
- [x] 运行 `./.venv/bin/python3 -m pytest apps/module_log/test_module_serial_service.py apps/module_log/test_module_serial_api.py -q`，预期新测试失败。
- [x] 以现有 `_SerialChannel` 为单会话实现，增加 session registry、UUID、标题、模块类型、映射身份、端口占用表和日志命名；不更改 XMODEM 协议。
- [x] 实现 session CRUD、start/stop/write/flash/logs 路由；旧 channel API 映射到兼容会话。
- [x] 复跑目标测试；评审同端口 COM/WSL 别名冲突、异常释放和旧 API 响应。

## 4. 模块日志独占页签 UI与对照解析

文件：`apps/module_log/static/module-serial.html`、`apps/module_log/static/module-serial.js`、`apps/workbench/static/pages/module-serial/*`、`apps/module_log/test_module_serial_frontend.py`、`apps/module_log/test_loghooks_api.py`。

接口：默认一个会话页；`+ 新增页面`、切换、关闭、当前页发送/烧录/日志轮询；对照解析按 `session_id` 选择实时来源。

- [x] 写前端回归：初始仅一个页签、新增三页、各页独立游标/端口、运行页关闭需确认、最后一页关闭自动补空页；运行前端测试预期失败。
- [x] 写对照解析 session 选择和新日志文件递归扫描测试，预期失败。
- [x] 最小替换双列固定 CCO/STA 面板为可渲染当前 session 的单页面板；同步独立和 workbench 静态副本。
- [x] 接入 mapping label、module 默认值、映射/未映射端口展示；发送框只能作用当前页。
- [x] 复跑 module frontend/API/loghooks 测试与 `node --check`；评审不停止后台会话的切页行为。

## 5. 版本化侦听台索引与深链

文件：`apps/listener/log_service.py`、`apps/listener/serial_service.py`、`apps/listener/app.py`、`apps/listener/test_log_service.py`、`apps/listener/test_app.py`、`apps/listener/static/app.js`、workbench listener 副本。

接口：`index_id` + `frame_id` 是唯一帧引用；`GET /api/listener/indexes/{index_id}/frames/{frame_id}`；旧当前索引接口保持可用。

- [x] 写新索引创建不覆盖旧索引、同一 frame_id 在不同 index_id 可独立取回、current 指针、按 index_id 查帧和 UI query 深链的失败测试。
- [x] 运行 listener 测试目标，预期失败。
- [~] 将固定 `log_index.sqlite3` 演进为 index catalog + `runtime/indexes/{index_id}.sqlite3`；保持当前索引兼容入口并添加保留策略接口。（index catalog 与兼容入口已完成；**保留策略接口未实现**——`index_registry.py` 无 delete/prune/retention，`/api/listener/indexes` 仅有 GET。）
- [x] 让实时采集、新文件索引、帧详情、时间筛选和状态返回 index_id；深链按 index_id/frame_id 复原。
- [~] 复跑 listener 测试；评审 WAL、旧数据库迁移、解析 DLL 不可用降级和文件清理边界。（WAL/迁移/DLL 降级已评审；文件清理边界依赖保留策略接口，待补。）

## 6. AI 控制、授权与观察任务

文件：`apps/workbench/ai_api.py`、`apps/workbench/ai_operations.py`、`apps/workbench/ai_auth.py`、`apps/workbench/ai_store.py`、`apps/workbench/app.py`、`apps/workbench/test_ai_api.py`、`apps/workbench/test_ai_operations.py`。

接口：`/api/ai/v1/status`、`module-sessions/ensure`、`listener/ensure`、`flash-operations`、`observations`、`operations/{id}`、`wait`、`listener/indexes/{index_id}/frames/{frame_id}`、Artifact 读取。

- [x] 写 token scope/资源/过期、状态脱敏、ensure 幂等、client_request_id 幂等、module literal/loghook/time-range 匹配、listener central_beacon + first_per_minute、wait、取消、source_stopped、复合数据库键的失败测试。
- [x] 运行 `./.venv/bin/python3 -m pytest apps/workbench/test_ai_api.py apps/workbench/test_ai_operations.py -q`，预期模块不存在。
- [x] 最小实现持久化授权摘要、操作/Artifact manifest 和审计记录；不得提供任意 shell 或任意路径读写。
- [x] 将后端串口会话作为唯一句柄持有者；UI 与 AI 共同读取状态/日志/索引，控制命令在 session 动作队列串行化。
- [~] 实现有界长轮询、live/time_range/cursor_range 观察、日志片段限制和 listener frame_key 返回。（有界 wait 0-30s、live/time_range 观察、日志片段限制、listener frame_key 已实现；**module 侧仅支持 literal/live，无 loghook/regex/sequence/not_seen 与 cursor_range**，见 `ai_operations.py` `create_observation`/`_create_listener_observation`。）
- [x] 复跑 AI、workbench、listener、module 测试；评审硬件写操作必须授权、Token 不泄漏、客户端断线不停止采集。

## 7. 打包、文档、技能计划与验证

文件：`tools/packaging/workbench.spec`、`tools/packaging/module_log.spec`、`docs/15-硬件资源速查.md`、`reqs/0004-workbench-serial-sessions/REQS.md`、`reqs/0004-workbench-serial-sessions/DONE.md`。

- [x] 写/更新打包测试，确保 `config/serial_ports.json` 作为首次安装默认配置复制到 exe 同级 `config/`，不覆盖现有现场配置。
- [~] 更新启动/维护说明、API 使用说明和真实硬件冒烟步骤。（打包/维护说明已更新：`tools/packaging/README.md`；API 契约以 REQS.md 第 25 章形式存在。独立 API 使用说明文档与真实硬件冒烟步骤文档尚未单独成文，待真机验证时一并补齐。）
- [x] 保留 `observe-workbench-logs` 技能为规划项：不创建、不安装、不加载，直到 AI API 冻结并获用户明确授权。
- [x] 运行 `./.venv/bin/python3 -m workbench.check_assets --strict`、相关 pytest 全集、`git diff --check`、UTF-8 校验。
- [ ] 只读硬件枚举确认 COM4/8/9/24 可见；真实启动、烧录、发送和模拟集中器交互均等待用户当前轮明确授权。（未执行——需真机环境与用户授权。）
- [x] 写 `DONE.md`，列出每个验收项、测试命令、结果、未执行的真机动作和原因。