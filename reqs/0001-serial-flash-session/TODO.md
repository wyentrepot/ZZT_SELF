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

## 阶段 6：真机烧录并发读竞争修复（变更 12）
- [x] 定位真机烧录失败根因：烧录线程 ser.read() 与常驻 RX 线程并发抢读同一 handle，RX 抢走 ACK → 超时重传 → Xmodem Download failed -2
- [x] 重构：RX 线程保持唯一 reader，烧录期间把设备应答喂入 _flash_resp_q，flash 经 _FlashReader（read 走队列、write 委托真实 ser）收发——烧录也走串口模块
- [x] 回归测试：FlashReaderUnitTest（队列消费/write 委托）+ FlashReaderRegressionTest（RX 线程并发下完整驱动 XMODEM 烧录 BURN SUCCESS）
- [x] 验证：test_module_serial_service 20 测试全绿；AST 全部通过

## 阶段 6：真机烧录失败修复（并发读串口竞争）
- [x] 定位根因：RX 监控线程与烧录线程并发 ser.read 抢 ACK → Xmodem Download failed: -2
- [x] 修复：RX 线程保持唯一 reader，烧录期间应答经 _FlashReader 队列喂给烧录线程
- [x] 回归测试：_FlashReader 单元测试 + 回环完整烧录驱动测试（20 测试全绿）

## 阶段 7：真机复测三问题（变更 13）
- [x] 问题 3 烧录仍失败：D:\zzt 部署的是旧 module_serial_service.py（变更 12 修复未同步）→ 重新部署 + 验证明文
- [x] 问题 1 启动慢：python 进程冷启动 ~6s + launcher 重复依赖检查(~14s) → .deps_ready 标记跳过 + 去 clr + 合并探测为一次 PowerShell
- [x] 问题 2 文件选择慢：整包 base64 上传 → 改 /api/fs/pick（tkinter 原生框只取路径）+ 内置浏览器兜底，移除上传
- [x] 验证：test_launcher / FsApiTests pick 测试全绿；全量仅 2 个既有"测试文件"目录缺失失败（与本次无关）

## 阶段 8：烧录提速 + 修复（变更 14，按 460800upgrade.py）
- [x] XMODEM-1K：STX 0x02 + 1024 字节/包，build_xmodem_packet/send_xmodem 支持 block_size
- [x] flash() 重构：reboot → 等正常系统 → config 切波特率 230400→460800（双保险确认）→ image → XMODEM 1K
- [x] 测试：1K 包结构 + selftest 128/1K + 回环完整烧录新流程全绿（21 测试）
- [x] 部署 D:\zzt + 全量 151 测试仅 2 个既有失败（测试数据缺失，与本次无关）
