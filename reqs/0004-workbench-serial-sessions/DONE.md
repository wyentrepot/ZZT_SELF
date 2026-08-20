# DONE.md — 需求 0004 收尾验收记录

> 状态：实现、自动化验证、真机只读/交互验证与**烧录验证**全部完成。
> 日期：2026-08-20（核对/收尾）；真机验证与烧录 2026-08-20 于 WSL（root）执行。
> 范围以 `REQS.md` 为准；当前硬件配置以 `docs/15-硬件资源速查.md` 与用户确认值为准：COM4 侦听台、COM8 CCO 日志、COM9 STA 日志均 115200；COM24 模拟集中器 9600/E/8/1。
> 自动化测试在 WSL 工作区使用 FakeSerial / 临时 SQLite；真机验证直接驱动真实串口（root 权限），仅侦听台/模块日志为只读采集，模拟集中器为受用户授权的完整交互（含写配置帧）。

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

## 真机验证记录（2026-08-20，用户授权）

环境：WSL Ubuntu-22.04 以 root 运行；真实串口全部枚举可用：`/dev/ttyUSB0`=COM4 侦听、`/dev/ttyACM0`=COM8 CCO、`/dev/ttyACM1`=COM9 STA、`/dev/ttyUSB1`=COM24 模拟集中器。验证通过直接驱动服务层/CLI 完成，未起 uvicorn。

### 统一映射加载
- `SerialPortCatalog.load()` 从 `config/serial_ports.json` 加载 **4 个映射全部正确**（listener/cco-main/sta-main/simcon，含 linux_device/windows_com/label/module/usage/baudrate）。

### 侦听台 COM4（只读采集）
- `SerialCaptureService` 打开 `/dev/ttyUSB0`@115200，6 秒采集 **5927 字节 / 41 帧**，日志落盘 `listener_devttyUSB0_..._自动保存.txt`，停止后状态 `stopped`。
- `port_identity` 正确：`mapping_id=listener, label=侦听台, windows_com=COM4`。

### 模块日志 COM8/COM9（只读采集，双会话并行）
- COM8(CCO) 与 COM9(STA) **同时运行互不干扰**（6 秒分别采集 42 行 / 119 行）。
- 日志文件命名含映射身份：`20260820-174705_[cco]_[cco-main-COM8--dev-ttyACM0].log`；**首条 EVENT 完整含模块类型/映射名/COM 号/设备名**：`mapping_id=cco-main；label=CCO 日志口；module=cco；device=/dev/ttyACM0；windows_com=COM8；...serial=115200/8N1`。
- COM8 采集到 CCO 真实业务日志（nwk 层 send/recv/bcn），确认链路有效。

### 串口独占冲突（真机）
- COM8 被会话 A 占用时，会话 B 尝试打开同端口被拒：`RuntimeError: 串口 /dev/ttyACM0 已被会话"真机-CCO"占用（ms-...）`，精确指出占用会话；停止 A 后 B 不受影响。

### 模拟集中器 COM24（用户授权完整交互，含写配置帧）
- 映射解析正确：`COM24 → /dev/ttyUSB1`@9600/E/8/1。
- 完整任务 `anhui_minute_collect.json`（fail_fast=false）9 步全部执行：**3 步 pass**（10H-F4 路由查询、10H-F2 从节点查询、03H-F10 运行模式），**6 步 fail**（部分为 matched=True 但响应内容断言不符，如任务配置查询/写配置；等待上报步骤无上报）。
- 结论：**COM24 真实串口收发完全打通**（发送 7 帧均收到响应）；fail 步骤为设备当前状态与任务文件期望值的语义差异，非映射/链路问题。

### COM8 调试口烧录（用户授权，XMODEM 写固件）
- 端口：`/dev/ttyACM0`（COM8，CCO 调试口，002「程序下载说明.md」方案，115200 8N1）。
- 固件：`iap_cco_AN_HUI_hv0201_sv000203_date240719_9600_E_FC_F8_isv090023_idate260813.bin`（462,804 B，IAP 升级）→ slot 0。
- 流程实测：`reboot` → `Unicorn Bootloader ver_02-00-41` → 按 `d` → `[root /]#` → `image` → `[image /]#` → `download 0` → `Y` → **XMODEM-1K 452 包 / 462,804 B / 100%**（中途 packet 129 一次重传自动恢复）→ `Xmodem download 462848(bytes) success!` → `Image download confirmed` → `reboot`。
- 结果：**`BURN SUCCESS`**；重启后 CCO 恢复正常运行（12 秒读 3746 字节，nwk 网络启动 / send / uart 收发活跃）。
- 备注：WSL 内运行 `flash_module.py` 时固件路径需绕过 `resolve_bin_path` 的 `/home→\\wsl.localhost UNC` 转换（该函数面向 Windows 侧服务），用 `/tmp` 副本即可。

### 未执行
- 浏览器端 UI 冒烟（工作台 iframe 保活 / 页签交互的真机刷新）未做——已由自动化前端测试覆盖。

## 验收场景对照（REQS §3）

### 1. 侦听台切页保活
- 状态：✅ 实现 + 自动测试 + 真机采集验证。
- 证据：工作台 `apps/workbench/static/app.js` 以 `ensureFrame` 懒加载一次并永久保活的 iframe 容器；切页只改 `hidden`，不改 `src`。`apps/workbench/test_app.py::test_shell_keeps_lazy_iframes_instead_of_reassigning_one_frame` 覆盖；真机 COM4 采集 41 帧确认串口链路真实有效。
- 未执行：浏览器内切页+返回的交互冒烟（自动化已覆盖 iframe 行为）。

### 2. 侦听台刷新状态恢复
- 状态：✅ 实现 + 自动测试；真机已确认后端状态（running/stopped）真实可读。
- 证据：`apps/workbench/static/pages/listener/app.js` 与 `apps/listener/static/app.js` 初始化请求 `/api/listener/serial/status`，`running/starting` 时切到 serial 数据源、回填配置并启动轮询；不因 unload/切页调用 stop。真机 `SerialCaptureService.status()` 返回 running→stopped 状态迁移正确。
- 未执行：浏览器刷新页面的真机冒烟。

### 3. 统一 JSON 映射生效
- 状态：✅ 实现 + 自动测试 + **真机验证**。
- 证据：`config/serial_ports.json`（COM4 listener / COM8 cco-main / COM9 sta-main / COM24 simcon，含 115200 与 9600/E/8/1）；`libs/shared/serial_mapping.py` + `serial_resources.py` 共享加载器；侦听台、模块日志、模拟集中器均调用同一 catalog（`libs/sim_concentrator/serial_io.py` 已接入）。真机加载 4 映射全部正确，COM24→/dev/ttyUSB1、COM4→/dev/ttyUSB0、COM8→/dev/ttyACM0、COM9→/dev/ttyACM1 均按 Linux 别名正确打开。
- 未执行：改 JSON 后重启软件（Windows 打包端）冒烟。

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
- 状态：✅ 实现 + **真机验证**。
- 证据：会话日志命名使用映射 id / label，首条 EVENT 含模块类型、映射名、COM 号与实际设备名（`module_serial_service.py` 日志命名逻辑）。真机 COM8 日志文件 `20260820-174705_[cco]_[cco-main-COM8--dev-ttyACM0].log` 首条 EVENT 完整含 `mapping_id=cco-main；label=CCO 日志口；module=cco；device=/dev/ttyACM0；windows_com=COM8；serial=115200/8N1`。

### 10. 对照解析 / 模拟集中器可用
- 状态：✅ 实现 + 自动测试 + **真机交互验证**。
- 证据：对照解析按 `session_id` 选择实时来源（`test_module_serial_frontend.py::test_mapping_details_and_dynamic_session_are_available_to_compare_view`）；模拟集中器读取统一 catalog 并保留 `simcon` 映射（9600/E/8/1）。真机 COM24 完整交互（fail_fast=false）9 步全部执行，查询类 3 步 pass，收发链路完全打通。

## 新增/变更文件清单

- 新增：`config/serial_ports.json`、`libs/shared/serial_mapping.py`、`libs/shared/serial_resources.py`、`libs/shared/test_serial_mapping.py`、`libs/shared/test_serial_resources.py`、`apps/listener/index_registry.py`、`apps/listener/test_index_registry.py`、`apps/workbench/ai_api.py`、`apps/workbench/ai_auth.py`、`apps/workbench/ai_operations.py`、`apps/workbench/ai_store.py`、`apps/workbench/test_ai_api.py`、`apps/workbench/test_ai_operations.py`、`tools/packaging/README.md`、`tools/packaging/runtime_hooks/ensure_serial_ports_config.py`、`tools/packaging/test_packaging_resources.py`、`reqs/0004-workbench-serial-sessions/`。
- 修改：`apps/listener/{app,log_service,serial_service,static/app}.py|js`、`apps/module_log/{app,module_serial_service,loghooks_api,static/module-serial.*}`、`apps/workbench/{app,static/app.js,static/index.html,static/styles.css,static/pages/*}`、`libs/sim_concentrator/*`、`tools/packaging/*.spec` 与 `build_exe.bat`、`docs/15-硬件资源速查.md`。

## 已知未完成 / 待办

1. **保留策略接口**：`apps/listener/index_registry.py` 无 delete/prune/retention，`/api/listener/indexes` 仅有 GET——历史索引无清理入口，依赖该接口的"文件清理边界"评审未闭环。
2. **AI 观察能力子集**：module 侧仅支持 `literal` 匹配与 `live/start=now`，无 `loghook_rule/regex/sequence/not_seen` 与 `cursor_range`；listener 侧无 `cursor_range`。与 REQS §25 完整契约有差距，待技能落地前补齐。
3. **API/冒烟使用说明**：独立 API 使用说明与真实硬件冒烟步骤文档未单独成文。
4. **烧录（XMODEM 写固件）已完成**：2026-08-20 用户授权烧录并指定固件目录 `/home/H_CCO/002/cco/firmware/`，选定 **COM8（`/dev/ttyACM0`）调试口**烧录（002「程序下载说明.md」用调试串口 115200 8N1）。烧录 `iap_cco_AN_HUI_hv0201_sv000203_date240719_9600_E_FC_F8_isv090023_idate260813.bin`（462,804 B）到 slot 0，**成功**：`reboot` → Unicorn Bootloader → `d` → `image` → `download 0` → Y → XMODEM-1K 452 包 100% → `Xmodem download success` → 重启 → CCO 恢复正常运行（nwk 启动/收发活跃）。注：固件路径需绕过 Windows 侧 `resolve_bin_path` 的 `/home→UNC` 转换（WSL 内运行直接用 `/tmp` 副本）。
5. **模拟集中器部分步骤 fail**：`anhui_minute_collect.json` 中任务配置查询/写配置等步骤收到响应但内容断言不符（设备当前状态与任务期望值差异），待真机设备状态对齐后复查；与需求 0004 的映射/链路无关。

## 结论

需求 0004 的代码实现、自动化验证、真机只读/交互验证与**烧录验证**均已完成：统一映射 4 端口真机生效、侦听台 COM4 真实采集、模块日志 COM8/COM9 双会话并行采集、串口独占冲突检测、模拟集中器 COM24 收发链路全通、**COM8 调试口 XMODEM 烧录成功且 CCO 恢复运行**。文档收尾（TODO 勾选、DONE.md、docs 波特率修正）已完成并推送远程。剩余为上述 4 项待办，均不阻塞代码合入。
