# REQS.md — 需求基线（需求 ID：0001，标题：串口全程监控 + XMODEM 烧录一体化 serial-flash-session）

> 本文件遵循「需求只追加、不覆盖」ADR 模式（req-mgmt 决策 #1）。
> **顶部「当前生效基线」只存最新版本**；所有历史变更追加在下方「变更记录」，禁止静默覆盖。
> 权威设计文档：department-ai-skills/docs/serial-flash-session-design.md（已确认方案与交付嘱托）

---

## 当前生效基线（版本：v2，更新：2026-08-10）

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
