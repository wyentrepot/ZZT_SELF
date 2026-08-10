# TODO.md — 任务清单（需求：0001 serial-flash-session）

> 大需求按阶段推进。完成一项勾 - [x]；全部完成移到 DONE.md。

## 阶段 0：初始化进度体系
- [x] 建立 reqs/ 结构（REQS-INDEX.md + 0001-serial-flash-session/REQS.md/TODO.md/DONE.md）
- [x] 灌入权威设计文档基线 + 2026-08-10 设计细化（变更记录只追加）

## 阶段 1：备份并禁用原 xmodem-module-flash
- [x] 确认部门目录 skills/xmodem-module-flash/（含 flash_xmodem_module.ps1）完整保留作备份
- [x] 全局已安装副本移出加载路径 / 改名 .disabled
- [x] reasonix doctor 验证不再加载

## 阶段 2：D:\zzt 新增模块日志串口服务（Python 核心）
- [x] hplc_web/xmodem_flash.py：XMODEM 传输核心，与 handle 解耦 flash(ser, bin_path, slot, baud_plan, log_write)
- [x] hplc_web/module_serial_service.py：ModuleSerialService——常驻独占 handle + RX 线程实时落盘 + 增量 buffer + write/动态波特率 + 烧录线程同 handle
- [x] 与 SerialCaptureService 完全分离，只新增不改现有

## 阶段 3：扩展 zzt API + 前端独立页面
- [x] app.py 新增路由 /api/module-serial/ports|status|start|stop|flash|write|baudrate|logs(?after=)
- [x] 烧录参数：.bin 路径（复用文件选择器）、slot、波特率方案、-NoRebootAfter 等价项
- [x] /module-serial 前端独立页面（新开标签）：800ms 增量轮询、日志+进度同屏、TX/RX 标记+事件行、页面按钮烧录

## 阶段 4：独立烧录脚本 + 新技能 serial-flash-session
- [x] flash_module.py 独立脚本：项目不在运行时自己 open COM → 复用 xmodem_flash.flash() → close
- [x] department-ai-skills/skills/serial-flash-session/：SKILL.md 编排层
- [x] 继承原技能安全规则（未确认 COM 不烧、dry-run 不算硬件证据、须 bootloader 确认文本）

## 阶段 5：无硬件自测 + 回归 + 文档/进度同步
- [x] loopback 模拟串口验证 XMODEM 移植（对照 ps1 CRC 自检 0x31C3）
- [x] zzt 现有 pytest 全绿无回归
- [x] 使用说明 + 权威设计文档/产物齐全确认 + req-mgmt 收尾（DONE.md 归档、REQS-INDEX.md 置 ✅）
