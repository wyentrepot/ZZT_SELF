## 2026-08-10 — 变更 15：烧录流程回到部门技能 ps1（直接 image 烧录 + 1K 提速）
- **做了什么**: 反复 config 切波特率失败后，按用户指引参照部门技能 `flash_xmodem_module.ps1`（SPLC Octopus 模块验证过能烧录）重构。其流程为**直接 image 烧录、不做 config 切波特率**：`Wait-BootloaderPrompt`（reboot → 按 'd' → `[root /]#` → 发 image → `[image /]#`，只认 `[image /]#`）→ `download` → `Y` → XMODEM → `Image download OK` → reboot。flash() 用回 ps1 移植的 `wait_bootloader_prompt`，XMODEM 用 1K(1024 字节)提速；删除自创 config 复杂度（_enter_bootloader/_enter_config_mode/_setbaud_and_confirm/_switch_to_high_baud）。并修 `_test_bootloader_text` 误判：移除 "enter bootloader mode"（banner 里 "Press 'd' key to enter..." 会误判）。
- **为什么**: 用户明确「照部门技能 ps1：直接 image 烧录 + 1K 提速」；ps1 是验证过能烧录的权威流程，config 切波特率（460800upgrade.py）流程不同且多次真机失败。
- **涉及文件**: hplc_web/xmodem_flash.py（flash() 用回 wait_bootloader_prompt、删 config 辅助函数、_test_bootloader_text 移除误判、XMODEM 1K）、hplc_web/tests/test_module_serial_service.py（LoopbackReceiver 匹配 ps1 流程、BootloaderDetectTest）、reqs/0001-serial-flash-session/REQS.md（变更 15，基线 v9）、部署 D:\zzt
- **验证**: test_module_serial_service 21 测试全绿（回环：reboot→按 d→[root /]#→image→[image /]#→download→XMODEM 1K→BURN SUCCESS）；全量 151 测试仅 2 个既有测试数据缺失失败
## 2026-08-10 — 变更 14 补充 2：根本修正——config 在 bootloader 下执行
- **做了什么**: 前两次真机复测 config 进入失败，最终定位**根本原因**：`config`/`setbaudrate` 命令**只在 bootloader 模式存在**，正常系统 shell `[node /]$` 没有（真机回显 `config: Command not found!`）。前一轮误让设备启动到正常系统再 config 是错的。**正确流程**：reboot → Unicorn Bootloader banner（"Press 'd' key"/"MHz"）→ 按 'd' 进 bootloader `[root /]#` → bootloader 下 `config` → `[config /]#` → setbaudrate 230400→460800 → exit → image → download → XMODEM 1K。实现：新增 `_enter_bootloader`（等 banner 按 'd' 进 [root /]#），`_enter_config_mode` 去掉 20 次 'd' 直接 config，删除错误的 `_wait_normal_boot`/`_send_d_key_repeat`。
- **为什么**: 严格对齐 460800upgrade.py——"MHz" 匹配 bootloader banner（Silicon Version: ...MHz），按 'd' 进 bootloader 后再 config。
- **涉及文件**: hplc_web/xmodem_flash.py（_enter_bootloader 新增、_enter_config_mode 简化、删 _wait_normal_boot/_send_d_key_repeat、flash() 流程改）、tests（LoopbackReceiver reboot 回显改 bootloader 序列）、reqs/0001-serial-flash-session/REQS.md（变更 14 补充 2）、部署 D:\zzt
- **验证**: test_module_serial_service 21 测试全绿（回环：reboot→按 d 进 bootloader→config→setbaudrate→image→XMODEM 1K）；全量 151 测试仅 2 个既有失败
## 2026-08-10 — 变更 14 补充：config 进入修复（真机实测）
- **做了什么**: 真机烧录仍慢且失败（464KB 在 115200 + 128 字节包传 ~3 分钟，设备中途 abort 序号重置到 1）。按权威参考脚本 `D:\tools\460800upgrade.py` 重构：①reboot 后等设备自动启动到正常系统 → 按多次 'd' → config 模式 → `setbaudrate 230400` → `setbaudrate 460800`（主机串口经 `_set_baud` 同步切，双保险确认 `[config /]#` 或 `1212121212454545`）→ exit 回 root → image → download → Y；②XMODEM 改 **1K 模式（STX 0x02 + 1024 字节/包）**在 460800 下发送。新增 `_wait_normal_boot` / `_enter_config_mode` / `_setbaud_and_confirm` / `_switch_to_high_baud`。
- **为什么**: 用户明确「先切波特率 230400→460800，一次传 1024 字节」，参考 460800upgrade.py；旧单一 115200 + 128 字节包导致慢 + 设备超时 abort。
- **涉及文件**: hplc_web/xmodem_flash.py（STX/1K、flash 重构波特率流程）、hplc_web/tests/test_module_serial_service.py（1K 包测试、selftest、回环新流程）、reqs/0001-serial-flash-session/REQS.md（变更 14，基线 v8）、TODO.md（阶段 8）、部署 D:\zzt
- **验证**: test_module_serial_service 21 测试全绿（1K 包结构、selftest 128/1K、回环完整烧录 reboot→config 切波特率→XMODEM 1K 多块→Image download OK→BURN SUCCESS）；全量 151 测试仅 2 个既有"测试文件"目录缺失失败（与本次无关）
## 2026-08-10 — 变更 13 补充：launcher 三态探测修复
- **做了什么**: 修复首版 launcher 探测逻辑缺陷——服务探测失败（无论没跑还是过期）都走 `restart_outdated_service`，服务没跑时查不到 PID 误报 `Port 8765 is in use`（用户启动时复现）。改为三态探测：一次 PowerShell 返回 exit 0（服务在跑且最新→直接开浏览器）/ exit 1（服务没跑→bootstrap 启动新服务）/ exit 2（在跑但过期→kill 重启）。
- **为什么**: 用户启动 launcher 报 `[SERVICE_OUTDATED_RESTARTING]` 后 `[ERROR] Port 8765 is in use`——旧逻辑把"服务没跑"也当作"过期重启"，查 PID 落空误报端口占用。
- **涉及文件**: hplc_launcher.bat（三态探测 `if errorlevel 2` / `if not errorlevel 1` / `goto :bootstrap`）、hplc_web/tests/test_launcher.py（补三态断言）、reqs/0001-serial-flash-session/REQS.md（变更 13 补充）
- **验证**: test_launcher 3 测试全绿；PowerShell 探测脚本实测 exit 1（服务没跑）正确走 bootstrap；已部署 D:\zzt 且与本地一致
## 2026-08-10 — 变更 13：真机复测三问题（部署同步 + 启动提速 + 文件选择只取路径）
- **做了什么**: 真机复测后修复 3 问题。①烧录仍失败根因：D:\zzt 部署的是旧 module_serial_service.py（变更 12 修复未同步，mtime 13:54 无 _FlashReader），已重新部署并验证明文；②启动慢：python 进程冷启动 ~6s（E-SafeNet 对 python.exe 挂钩），launcher 每次启动重复跑 `import clr` 依赖检查(~14s)+2 次 PowerShell 探测；优化为 `.venv\.deps_ready` 标记跳过重复依赖检查、依赖检查去掉最慢的 clr、服务探测+能力检查合并为一次 PowerShell；③文件选择慢：前端 FileReader 整包 base64 上传 464KB 固件，用户要求"只要路径"；改为点选择先试 `/api/fs/pick`（改用 tkinter 原生对话框实现，仅返回路径）、失败/超时兜底到内置目录浏览器（/api/fs/roots+list，选 .bin 只填路径），移除整包上传。
- **为什么**: ①变更 12 修复未部署到 D:\zzt（独立部署副本非镜像）；②用户反馈"启动贼慢"；③用户质疑"不是只要获取路径即可吗？怎么像上传文件给浏览器"。
- **涉及文件**: D:\zzt 部署同步（module_serial_service.py / hplc_launcher.bat / app.py / static/module-serial.html / static/module-serial.js / tests）、hplc_launcher.bat（deps_ready 标记 + 合并探测 + 去 clr）、hplc_web/app.py（fs_pick 切 tkinter 版，超时 60s）、static/module-serial.html/js（路径选择 + 内置浏览器兜底，移除上传）、tests（test_launcher / test_app 更新）、reqs/0001-serial-flash-session/REQS.md（变更 13，基线 v7）、TODO.md（阶段 7）
- **验证**: test_launcher 全绿、FsApiTests 的 pick 测试全绿（tkinter 版）；全量 130 测试仅 2 个既有"测试文件"目录缺失失败（test_dotnet_parser / test_app，与本次无关）；module-serial.js 括号配平检查通过
## 2026-08-10 — 变更 12：真机烧录并发读串口竞争修复
- **做了什么**: 修复真机烧录失败（iap_cco 固件 XMODEM packet 10/11 重传后 `Xmodem Download failed: -2!`）。根因：烧录线程 `xmodem_flash.flash()` 内部直接 `ser.read()` 等 ACK，与常驻 RX 线程 `ser.read(ser.in_waiting)` 并发读同一 handle，RX 线程抢走 ACK → 超时重传累积 → 设备校验失败。重构为「RX 线程唯一 reader」：烧录期间 RX 线程把设备应答喂入 `_flash_resp_q` 队列，`flash()` 经新增 `_FlashReader`（read 走队列、write/in_waiting/baudrate 委托真实 ser）完成收发——烧录本质也走串口模块，前端仍从日志/服务读。
- **为什么**: 用户明确架构要求「串口线程唯一收发包、写日志目录、前端从日志读、发送走串口模块、烧录本质也是调用串口线程的发送」；旧实现 flash 内部直读与单一模块模型冲突，导致并发抢读烧录失败。
- **涉及文件**: hplc_web/module_serial_service.py（_FlashReader + _flash_resp_q + _rx_loop 分发 + flash 接线）、hplc_web/tests/test_module_serial_service.py（FlashReaderUnitTest + FlashReaderRegressionTest）、reqs/0001-serial-flash-session/REQS.md（变更 12，基线 v6）、TODO.md（阶段 6）
- **验证**: test_module_serial_service 20 测试全绿（含新回归：RX 线程并发下完整驱动 XMODEM 烧录 BURN SUCCESS）；全量 113 测试仅 4 个既有 ImportError（fastapi/pytest 模块缺失，与本次无关）；AST 全部通过
## 2026-08-10 — 需求 0001 变更迭代补记
- **做了什么**: 需求 0001 收尾后又发生 7 轮变更（一键启动双标签、字符显示、4缺陷修复、日志框高度、单切换按钮、铺满视口、原生文件选择器），按 req-mgmt 流程 C 补记为变更 3~9（只追加不覆盖），基线升至 v3
- **为什么**: 之前修复阶段未按 req-mgmt 流程登记变更，导致 REQS.md 记录与实现脱节；用户质疑技能未被执行
- **涉及文件**: reqs/0001-serial-flash-session/REQS.md（变更 3~9）、REQS-INDEX.md（状态改回 🔄 进行中）
- **验证**: REQS.md 变更 3~9 齐全、版本 v3；INDEX 状态与实现一致
## 2026-08-10 — 阶段 2-4 实现完成
- **做了什么**: 串口全程监控 + XMODEM 烧录一体化全部核心实现
  - 阶段 2：hplc_web/xmodem_flash.py（XMODEM 传输核心，flash(ser,..) 与 handle 解耦，CRC 自检 0x31C3）+ hplc_web/module_serial_service.py（ModuleSerialService 常驻独占 handle + RX 线程实时落盘 + 增量 buffer + write/动态波特率/烧录同 handle）
  - 阶段 3：app.py 新增 /api/module-serial/* 路由 + /module-serial 前端独立页面（新标签 800ms 增量轮询、日志+进度同屏）
  - 阶段 4：flash_module.py 独立烧录脚本（项目不在时自开 COM）+ 部门仓 serial-flash-session 技能（编排层）
- **为什么**: 按设计文档 5 阶段交付；串口单一模块模型（前端纯显示、烧录=同 handle 文件传输、RX 不停、不关串口）
- **涉及文件**: hplc_web/xmodem_flash.py, module_serial_service.py, app.py, static/module-serial.html, static/module-serial.js, flash_module.py, tests/test_module_serial_service.py, tests/test_module_serial_api.py, department-ai-skills/skills/serial-flash-session/
- **验证**: 14+10 单元/API 测试通过；110 测试全绿；loopback 模拟接收方完整驱动烧录（3 包 ACK、波特率 9600→115200→9600、Image download OK、BURN SUCCESS）
# DONE.md — 完成日志（需求：0001 serial-flash-session）

> 只追加，最新在上。记录做了什么、为什么、改了哪些文件（req-mgmt 决策 #1）。

## 2026-08-10 — 真机烧录失败修复：并发读串口竞争（变更 12，基线 v6）
- **做了什么**: 真机烧录 iap_cco 固件实测失败（XMODEM packet 10/11 重传后 `Xmodem Download failed: -2!`）。根因：烧录线程 `xmodem_flash.flash()` 内部直接 `ser.read()` 等 ACK，与常驻 RX 线程 `ser.read(ser.in_waiting)` 并发读同一 handle，RX 线程抢走 ACK → 超时重传累积 → 设备序号/CRC 校验失败。修复：**RX 线程保持唯一 reader**，烧录期间把设备应答喂入 `_flash_resp_q` 队列，`flash()` 经新 `_FlashReader`（read 走队列、write/baudrate 委托真实 ser）收发。烧录结束 `finally` 清队列恢复实时落盘。
- **为什么**: 用户明确架构要求「串口线程唯一收发包、写日志目录、前端从日志读、发送走串口模块、烧录本质也是调用串口线程的发送」——原 flash 内部直读与该单一模块模型冲突，是并发读竞争烧录失败的直接原因。修复使烧录也走串口模块收发，从根上消除竞争。
- **涉及文件**: hplc_web/module_serial_service.py（_FlashReader + 烧录应答队列 + _rx_loop 分发）、hplc_web/tests/test_module_serial_service.py（FlashReaderUnitTest + FlashReaderRegressionTest 回环驱动）、reqs/0001-serial-flash-session/REQS.md（变更 12，基线 v6）、TODO.md（阶段 6）
- **验证**: test_module_serial_service 20 测试全绿（含新回归：_FlashReader 单元测试 + 回环完整烧录 BURN SUCCESS，2 包全 ACK、Image download OK）；其余 4 error 为既有 fastapi/pytest 依赖缺失，与本次改动无关

## 2026-08-10 — 初始化进度体系
- **做了什么**: 建立 req-mgmt 进度体系（REQS-INDEX.md + 需求 0001 基线/TODO/DONE）
- **为什么**: 需求基线、任务清单、完成日志需随分支保存，防需求变更混乱
- **涉及文件**: REQS-INDEX.md, reqs/0001-serial-flash-session/REQS.md, TODO.md, DONE.md
- **验证**: 文件创建完成，基线内容与权威设计文档决策一致
