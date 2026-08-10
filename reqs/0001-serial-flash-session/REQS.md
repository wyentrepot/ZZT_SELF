# REQS.md — 需求基线（需求 ID：0001，标题：串口全程监控 + XMODEM 烧录一体化 serial-flash-session）

> 本文件遵循「需求只追加、不覆盖」ADR 模式（req-mgmt 决策 #1）。
> **顶部「当前生效基线」只存最新版本**；所有历史变更追加在下方「变更记录」，禁止静默覆盖。
> 权威设计文档：department-ai-skills/docs/serial-flash-session-design.md（已确认方案与交付嘱托）

---

## 当前生效基线（版本：v9，更新：2026-08-10）

### 目标
在 SPLC 模块烧录场景下，让一个「模块日志/烧录串口」被 ZZT_SELF 项目全程接管：原始字节流实时落盘、实时可看、可追溯；同一串口上可同时执行 XMODEM 烧录（文件传输），**不打断实时监控**；项目不在运行时也能独立烧录。

### 需求点（来自设计文档 D1-D10 + 2026-08-10 设计细化）
- **串口单一模块模型**：所有串口操作（open/read/write/改波特率/XMODEM 烧录）收敛到 `ModuleSerialService` 一个模块，它是唯一持有 COM handle 的地方；前端纯显示 + 发指令，烧录技能只发指令，两者都通过 REST API 调用该模块，任何一方不直接持有串口。
- **串口常驻独占**：服务 start 时 open COM 一次，stop 才 close；烧录、改波特率、手动写都在**同一个 handle** 上操作，绝无重开、无打断。
- **烧录 = 文件传输**：XMODEM 烧录往已持有的 COM handle 发数据流（CRC-16/XMODEM、SOH 包、ACK/NAK/CAN/EOT、bootloader 导航、download <slot>、Image download OK 判定）；波特率可运行中动态修改（ser.baudrate 底层 SetCommState，不关句柄不清缓冲）。
- **RX 监控线程全程不停**：烧录与监控同 handle 并发（pyserial read/write 线程安全）；烧录线程写 TX，RX 线程照常读回显并实时落盘。
- **原始字节实时落盘**：LOG/MODCOM{port}_{ts}_模块日志.txt，跨天轮转；RX 行/TX 行/事件行（如波特率变更）共用同一文件，时间线连续。
- **与侦听台完全独立**：D1/D2/D9——模块日志口与侦听台串口互不干扰，两套独立 COM、独立处理；zzt 只新增不改现有。
- **XMODEM 核心与 handle 解耦**：xmodem_flash.py 的 flash(ser, bin_path, slot, baud_plan, log_write) 只认 pyserial 对象；项目运行时由服务用其 handle 执行，项目不在运行时由独立脚本 flash_module.py 自己 open COM 复用同一核心。
- **烧录双入口**：D8——页面按钮 + 技能脚本；两者运行时都走同一执行路径（调 /api/module-serial/flash）。
- **新技能 serial-flash-session 为编排层**：D5——薄封装复用 zzt REST API；继承原技能安全规则（未确认 COM 不烧、dry-run/自检不算硬件证据、烧录成功须有 bootloader 确认文本）。
- **原技能处置**：D10——原 xmodem-module-flash 先禁用、作备份保留。

### 验收标准
- [x] ModuleSerialService 实现：start/stop/status/write/flash/baudrate，常驻独占 handle，RX 线程实时落盘 + 增量 buffer
- [x] XMODEM 移植通过 loopback 自测（对照 ps1 CRC 自检 0x31C3）
- [x] /api/module-serial/* 路由 + /module-serial 前端独立页面（新开标签，800ms 增量轮询，日志+进度同屏）
- [x] flash_module.py 独立脚本：项目不在运行时自己 open COM 复用同一核心完成烧录
- [x] serial-flash-session 技能 SKILL.md 编排层落盘于 department-ai-skills/skills/
- [x] 原 xmodem-module-flash 已禁用（reasonix doctor 验证不再加载），部门目录备份保留
- [x] zzt 现有 pytest 全绿无回归

### 所属分支
master

---

## 变更记录（只追加，禁止覆盖）

### 变更 1 ｜ 2026-08-10 ｜ 需求确认
- **改成什么**: 按权威设计文档（department-ai-skills/docs/serial-flash-session-design.md）确立初始基线，含 D1-D10 决策与 5 阶段交付
- **为什么**: 文档为已确认的方案与交付嘱托
- **影响**: 全部 5 个阶段
- **被取代**: 无（初始版本）

### 变更 2 ｜ 2026-08-10 ｜ 设计细化
- **改成什么**: 明确「串口单一模块模型」——串口由 ModuleSerialService 一个模块管到底，前端纯显示、烧录技能只发指令，全部走 REST API；烧录 = 同一 handle 上的文件传输 + 动态波特率，RX 监控线程全程不停，不关串口不重开；XMODEM 核心与 handle 解耦（flash_module.py 项目不在时自开 COM 复用）
- **为什么**: 用户明确要求「串口一直独占、烧录只是传输文件、不影响监控、不需要关闭重开串口」「前端纯显示、下发也调串口模块」
- **影响**: 阶段 2（module_serial_service.py）、阶段 3（API/前端）、阶段 4（flash_module.py + 新技能）
- **被取代**: 变更 1 中「烧录脚本短暂接管 COM（pause/resume）」的早期设想

### 变更 3 ｜ 2026-08-10 ｜ 一键启动自动打开模块日志页
- **改成什么**: run.py 启动后同时打开侦听台主页 + /module-serial 页两个标签；hplc_launcher.bat 能力检查加 module_serial_api_revision
- **为什么**: 用户反馈一键启动后看不到模块日志页（旧副本 D:\zzt 无新功能）
- **影响**: hplc_web/run.py, hplc_launcher.bat；同步部署到 D:\zzt
- **被取代**: 无（新增强化）

### 变更 4 ｜ 2026-08-10 ｜ RX/TX 改字符显示
- **改成什么**: 模块日志页不再显示 hex，改按换行切字符行、每行加 YYYYMMDD-HH:MM:SS:mmm 时间戳
- **为什么**: 用户要求「不要用 hex 显示，要字符打印，有换行符时每行前加时间标签」
- **影响**: module_serial_service.py（_ingest_char_stream 按行切分 + 新时间戳格式）, module-serial.js
- **被取代**: 变更 2 中「RX 原始字节 hex 落盘」

### 变更 5 ｜ 2026-08-10 ｜ 模块日志页 4 缺陷修复
- **改成什么**: ①文件选择用内置浏览器 ②波特率方案改单一波特率(对齐烧录技能 115200) ③image slot 改下拉 0/1 ④界面自适应
- **为什么**: 用户指出 4 个缺陷（文件管理器没弹出、波特率方案不符技能、image slot 语义、界面太小）
- **影响**: module-serial.html/js, xmodem_flash.py（单一波特率）
- **被取代**: 变更 4 中文件选择方式

### 变更 6 ｜ 2026-08-10 ｜ 日志框高度修复
- **改成什么**: 日志框改 42vh 自适应高度 + 页面整体滚动（修深不见底）
- **为什么**: 用户反馈日志框深不见底（calc(100vh-360px) 在内容超高时失效）
- **影响**: module-serial.html
- **被取代**: 变更 5 中 calc 高度

### 变更 7 ｜ 2026-08-10 ｜ 启动/停止合并单按钮
- **改成什么**: 启动、停止两按钮合并为一个切换按钮（启动→停止→启动），按状态自动切换文本/样式
- **为什么**: 用户对两个按钮不满意，要求单一切换按钮
- **影响**: module-serial.html/js
- **被取代**: 无（交互优化）

### 变更 8 ｜ 2026-08-10 ｜ 铺满视口不留白
- **改成什么**: ms-shell 恢复 100vh，日志面板 flex:1 撑满剩余空间（修底部留白）
- **为什么**: 用户反馈浏览器留白太多没铺满（上轮 height:auto 导致）
- **影响**: module-serial.html
- **被取代**: 变更 6 中 42vh 固定高度

### 变更 9 ｜ 2026-08-10 ｜ 文件选择改回 Windows 原生对话框
- **改成什么**: 移除内置文件浏览器，改调 /api/fs/pick 弹 Windows 原生文件管理器（仿照 zzt）
- **为什么**: 用户找不到 WSL 路径，要求直接打开 Windows 文件管理器选真实盘符路径
- **影响**: module-serial.html/js（删内置浏览器）, 依赖 app.py /api/fs/pick
- **被取代**: 变更 5 中内置文件浏览器方案

### 变更 10 ｜ 2026-08-10 ｜ 文件选择改用浏览器原生 file 上传
- **改成什么**: 「选择文件…」改触发浏览器 <input type=file>（弹系统文件选择框，能选 Windows/WSL 路径文件），选中后 base64 上传后端 /api/module-serial/upload 保存到 LOG/uploads/ 返回路径；新增 xmodem_flash.resolve_bin_path 支持 WSL 路径（/mnt/d、/home 等）转换为 Windows 可读路径
- **为什么**: 服务进程被 E-SafeNet 提升为 SYSTEM（Store Python），/api/fs/pick 的 PowerShell 原生对话框弹窗进不了用户桌面（两个页面都失效）；用户要求改用浏览器 file 上传且支持 WSL 路径
- **影响**: hplc_web/app.py（upload 路由）、xmodem_flash.py（resolve_bin_path）、static/module-serial.html/js（file 上传）、tests
- **被取代**: 变更 5、变更 9 的 /api/fs/pick 原生对话框方案

### 变更 15 ｜ 2026-08-10 ｜ 烧录流程回到部门技能 ps1（直接 image 烧录 + 1K 提速）
- **改成什么**: 反复 config 切波特率失败后，用户指出权威参考是部门技能 `flash_xmodem_module.ps1`（在 SPLC Octopus 模块验证过能烧录）。其流程是**直接 image 烧录、不做 config 切波特率**：`Wait-BootloaderPrompt`（reboot → 按 'd' → `[root /]#` → 发 `image` → `[image /]#`，Test-BootloaderText 只认 `[image /]#`）→ `download <slot>` → `Y` → XMODEM → `Image download OK` → reboot。重构 flash() 回到 ps1 流程（用回 ps1 移植的 `wait_bootloader_prompt`），XMODEM 用 **1K(1024字节包)提速**；删除之前自创的 config 切波特率复杂度（_enter_bootloader/_enter_config_mode/_setbaud_and_confirm/_switch_to_high_baud）。同时修复 `_test_bootloader_text` 误判：移除 "enter bootloader mode"（它出现在 bootloader banner "Press 'd' key to enter bootloader mode!" 里，导致真机误判为已就绪而提前发命令）。
- **为什么**: 用户明确「照部门技能 ps1：直接 image 烧录 + 1K 提速」；ps1 是验证过能烧录的权威流程，我之前加的 config 切波特率（来自 460800upgrade.py）流程不同且多次真机失败。
- **影响**: hplc_web/xmodem_flash.py（flash() 用回 wait_bootloader_prompt、删 config 辅助函数、_test_bootloader_text 移除 "enter bootloader mode" 误判、XMODEM 1K）、tests（LoopbackReceiver 匹配 ps1 流程、BootloaderDetectTest 更新）、部署 D:\zzt
- **被取代**: 变更 14 及补充的 config 切波特率流程（460800upgrade.py 方式）；恢复变更 12 前的直接 image 烧录路径

### 变更 14 ｜ 2026-08-10 ｜ 烧录提速 + 修复：XMODEM-1K + 波特率切 460800（按 460800upgrade.py）
- **改成什么**: 真机烧录仍慢且失败（464KB 在 115200 单一波特率 + 128 字节包传 3 分钟，设备中途 abort 序号重置）。按权威参考脚本 `D:\tools\460800upgrade.py` 重构烧录流程：①reboot 后等设备自动启动到正常系统（不按 'd' 进 bootloader）→ 按多次 'd' → config 模式 → `setbaudrate 230400` → `setbaudrate 460800`（主机串口同步切波特率，双保险确认：`[config /]#` 或 `1212121212454545`）→ exit 回 root → image → download → Y；②XMODEM 用 **1K 模式（STX 0x02 + 1024 字节/包，DEFAULT_BLOCK_SIZE=1024）**在 460800 下发送，大幅提速。
- **为什么**: 用户明确指示「应该先切波特率到 230400→460800，然后一次传 1024 字节」，并给出参考脚本 460800upgrade.py；旧实现单一 115200 + 128 字节包导致慢 + 设备超时 abort。
- **影响**: hplc_web/xmodem_flash.py（STX/1K 包、flash() 重构波特率切换流程、新增 _wait_normal_boot/_enter_config_mode/_setbaud_and_confirm/_switch_to_high_baud）、tests（1K 包结构、selftest 128/1K、回环完整烧录新流程）、部署 D:\zzt
- **被取代**: 变更 5 的「单一波特率 115200」方案；变更 2 的直接进 bootloader 烧录路径（改为先 config 切波特率再 image）
- **补充（真机实测，根本修正）**: 前两次真机复测 config 进入失败，最终定位**根本原因**：`config`/`setbaudrate` 命令**只在 bootloader 模式下存在**，正常系统 shell（`[node /]$`）没有（真机回显 `config: Command not found!`）。之前误让设备启动到正常系统再 config 是错的。**正确流程**：reboot → 显示 Unicorn Bootloader banner（"Press 'd' key"/"MHz"）→ **按 'd' 进入 bootloader（[root /]#）** → 在 bootloader 下 `config` → `[config /]#` → setbaudrate 230400→460800 → exit → image → download → XMODEM 1K。参考 460800upgrade.py 的 "MHz" 即匹配 bootloader banner（Silicon Version: ...MHz）。实现：新增 `_enter_bootloader`（等 banner 按 'd' 进 [root /]#），`_enter_config_mode` 去掉按 20 次 'd' 直接 config；删掉 `_wait_normal_boot`/`_send_d_key_repeat`（错误路径）。

### 变更 13 ｜ 2026-08-10 ｜ 真机复测三问题：部署同步 + 启动提速 + 文件选择只取路径
- **改成什么**: 真机复测发现 3 问题并修复：①烧录仍失败——D:\zzt 部署的是旧 module_serial_service.py（变更 12 修复未同步，mtime 13:54 无 _FlashReader），已重新部署；②启动慢——python 进程冷启动 ~6s（E-SafeNet 对 python.exe 挂钩），launcher 每次启动重复跑 `import clr` 依赖检查(~14s) + 2 次 PowerShell 探测；优化为 `.venv\.deps_ready` 标记跳过重复依赖检查、依赖检查去掉最慢的 clr、服务探测+能力检查合并为一次 PowerShell；③文件选择慢——前端用 FileReader 整包 base64 上传 464KB 固件到后端再写盘，用户要求"只要路径"；改为点选择先试 `/api/fs/pick`（改用 tkinter 原生对话框实现，仅返回路径不上传），失败/超时兜底到内置目录浏览器（/api/fs/roots+list，选 .bin 只填路径），移除整包上传。
- **为什么**: ①变更 12 修复未部署到 D:\zzt（独立部署副本，非镜像）；②用户反馈"启动贼慢"（点启动到服务可用整体慢）；③用户质疑"不是只要获取路径即可吗？怎么像上传文件给浏览器"。
- **影响**: 部署同步（D:\zzt 的 module_serial_service.py / hplc_launcher.bat / app.py / module-serial.html / module-serial.js）、hplc_launcher.bat（deps_ready 标记 + 合并探测 + 去 clr）、hplc_web/app.py（fs_pick 切 tkinter 版）、static/module-serial.html/js（路径选择 + 内置浏览器兜底，移除上传）、tests（test_launcher / test_app 更新）
- **被取代**: 变更 10 的「浏览器整包 base64 上传」方案（保留 upload 接口作备选）；变更 9 的 PowerShell 原生对话框（实测挂起，改用 tkinter）
- **补充（同日）**: 首版 launcher 探测逻辑有缺陷——服务探测失败（无论没跑还是过期）都走 `restart_outdated_service`，服务没跑时查不到 PID 误报 `Port 8765 is in use`。改为**三态探测**：一次 PowerShell 返回 exit 0（服务在跑且最新→直接开）/ exit 1（服务没跑→bootstrap 启动）/ exit 2（在跑但过期→kill 重启）。已部署 D:\zzt，test_launcher 3 测试全绿。

### 变更 12 ｜ 2026-08-10 ｜ 烧录并发读串口竞争修复（烧录走串口模块收发）
- **改成什么**: 真机烧录实测失败（iap_cco 固件，XMODEM packet 10/11 重传后 `Xmodem Download failed: -2!`）。根因：烧录线程 `xmodem_flash.flash()` 内部直接 `ser.read()` 等 ACK，与常驻 RX 线程 `ser.read(ser.in_waiting)` 并发读同一 handle，RX 线程抢走 ACK → 超时重传累积 → 设备校验失败。重构：**RX 线程保持唯一 reader**，烧录期间 RX 线程把读到的字节喂入烧录应答队列，`flash()` 通过一个 ReaderProxy（read 走队列、write 委托真实 ser）完成收发——烧录本质也是调用串口模块的发送/接收，前端仍从日志/服务读。
- **为什么**: 用户明确架构要求「串口线程唯一收发包、写日志目录、前端从日志读、发送走串口模块、烧录本质也是调用串口线程的发送」；当前 flash 内部直读与单一模块模型冲突，导致并发读竞争烧录失败。
- **影响**: hplc_web/xmodem_flash.py（无改动或仅语义说明）、hplc_web/module_serial_service.py（新增烧录应答队列 + ReaderProxy + RX 分发）、tests
- **被取代**: 变更 2 中「烧录 = 独立线程直接读 handle 等应答」的字节读取实现（发送仍走同一 handle，串口不关不重开、RX 线程常驻）

### 变更 11 ｜ 2026-08-10 ｜ 烧录闭环：兼容 Unicorn Bootloader 就绪检测
- **改成什么**: _test_bootloader_text 在 [image /]# 之外，兼容真实设备 Unicorn Bootloader（venus8m）的 "You can input command help or ?" / "enter bootloader mode" 就绪信号
- **为什么**: 真机烧录实测（iap_cco 固件）：设备 Unicorn Bootloader 按 d 进入的是交互式命令提示符（You can input command），无 [image /]#，导致 Bootloader prompt was not detected 烧录失败
- **影响**: hplc_web/xmodem_flash.py（_test_bootloader_text）、tests
- **被取代**: 无（修复闭环）



