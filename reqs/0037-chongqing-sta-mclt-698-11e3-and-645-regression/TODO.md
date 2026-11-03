# REQS-0037 — TODO

> 状态：✅ 主要交付全部完成（2026-11-03 task 0/1/15 内层 698 补测 PASS，收口）；遗留项转为工程跟进。

## 已完成

- [x] 698 11E3 v5 修复：读取层 FF 满长度、构帧层固定长度拷贝、C/APDU/TimeTag/长度修正。
- [x] 单测 9 个用例 + 真机 Unity 9/9 PASS。
- [x] CCO 重庆固件 `fix_13762_develop` 编译/烧录：`CHONG_QING_MODE / sv002741`。
- [x] 11E3 task 1 全链路 PASS（内层 698 + 06H-F230）。
- [x] task 0/1/15 外层 00E3 task_id 正确。
- [x] 645 配置/采集/11E3/06H-F230/缺项 0x32 全 PASS。
- [x] STA 工程 req05 的 REQS/PLAN/DONE/TODO 重建 + 恢复摘要。
- [x] task 0/15 内层 698 补测（2026-11-03，ALL PASS，见 DONE.md 与 /d/sta/01-sta/20261103-003456/）。
- [x] 645 快速回归（task2 正常 + task3 缺项 0x32）；删除任务（单删/迁移快照/0xFF 广播）；STA 软 reboot 重连与存数保持观察；
      早于存数点读请求行为（result=2 空响应）。

## 待完成（工程跟进，非台架阻塞）

- [x] 【已关闭 2026-11-03，用户拍板】simeter 波特率：`serial_bridge.json` **保持 9600E 不改**（已核实当前即 9600/E），口径=锁 9600、表端/STA 会扫波特率；台架事实留档：00:45/00:50 静态 9600 两轮采集全败，00:52:48 动态切 2400 后恢复（00:55 轮 645 存数、01:00 轮 5 任务全存数），01:24 清理已复位 9600（证据 `/d/sta/01-sta/20261103-003456/raw-logs/bridge-log-baud-switch-today.txt`）。
- [ ] simeter 脑 698 可配置 OAD 数据集（当前仅 jzjc OAD 可应答，698 分钟采集需注入应答）。
- [ ] STA sector_manager 任务持久化提交策略核实（软重启后仅部分 slot 恢复，mcltList_length=1）。
- [ ] 645/0002/0004 完整回归、单相/三相、断电重启（本次明确跳过）。
- [ ] 跨地区 Anhui/Jibei 共用函数决策与回归。
- [ ] APDU 长度 128~255（0x81/0x82 扩展）方案确认。
- [ ] W3/W4 场景文档补注 11E3 自主发起语义（read 步骤"本地口注入 11E3"前提不成立，按 CCO 自主读口径使用；见 REQS.md §5.7）。
- [ ] 恢复 STA 工程原 reqs/evidence 的原始串口日志、截图、审计报告（从备份/Teambition）。

## 下次台架顺序

1. 核对 STA/CCO 均为 `CHONG_QING_MODE`，CCO 为 `fix_13762_develop` 构建。
2. 一次配齐 698 task 0/1/15 与 645 task，确认 `CFGOK`。
3. 等每个任务 `Freeze data for task X stored successfully`，记录冻结时刻。
4. 11E3 由 CCO 冻结后约 9 min 自主发起（勿向 COM4 注入，实测不可达）；抓外层 task_id + 内层帧，按冻结时刻校验。
5. 清理任务、恢复档案/模拟表地址、关闭会话；证据落 `/d/` 持久目录。
