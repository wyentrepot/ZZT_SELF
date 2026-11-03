# REQS-0037 — 重庆 STA 分钟采集 698 11E3 修复 + 645 回归（真机台架）

> 状态：✅ 已完成（2026-11-03 真机补测收口：task 0/1/15 内层 698 全链路 PASS，冻结 2026-11-03 01:00:00；未烧录未重编译）
> 创建：2026-11-02
> 来源：用户要求核查 req05（QUOK-17）并做真机测试；后续追加重庆 CCO 重编译和 645 配置采集回归
> 关联：
> - STA 工程：`/home/H_STA/01/sta`，分支 `chong_qing_fabu`
> - STA 需求：`/home/H_STA/01/sta/reqs/0005-chongqing-sta-minute-collection-698-11e3/`
> - CCO 工程：`/home/H_CCO/001/cco`，重庆构建分支 `fix_13762_develop`
> - 工作台技能：`ai-control-plane` / `wb-simcon` / `wb-simeter` / `wb-listener` / `splc-flash-loop`

## 1. 背景

Teambition 缺陷 QUOK-17：重庆 STA 分钟采集 698（协议类型 3）在响应 CCO 的 11H-00E3（11E3）读取时，上行携带的 698 帧与表端原始回复不一致，协议工具报“APDU长度错误”。

根因（已确认）：

1. 控制域 C 写成 `0x13`（应为 `0xC3`）；
2. APDU 第三字节写成任务号（应为 PIID-ACD `0x01`，任务号不属于 698 APDU）；
3. TimeTag 硬编码 `CA 7D A4 05`（应随冻结时刻）；
4. APDU 长度漏算 9 字节固定尾（`0x46` vs 表端 `0x48`）。

## 2. 目标与范围

- 修复重庆 698 分钟采集 11E3 构帧：保留全部请求 OAD，缺项按配置长度写 `0xFF`；C/APDU 头/TimeTag/APDU 长度正确。
- 保证 645 分钟采集不受影响：一 DI 一帧、缺项 `0x32` 满长度。
- 用真机验证：STA Unity、CCO 重庆固件、11E3 不同任务号、645 配置/采集/11E3/06H-F230。
- 不在范围：CCO/主站解析逻辑、其他地区分钟采集策略、山西/湖南直接 698 缺项改造。

## 3. 交付与结果

### 3.1 STA 代码（v5）

- 读取层：11E3 路径 698 缺项按配置长度 `memset(0xFF)`；`valid_map` 仅用于全缺判断。
- 构帧层：C=`0xC3`、APDU `85 02 01 <task->di_cnt>`、TimeTag=`read_start_sec`、APDU 长度含 9 字节尾、全缺返回 0、失败 `di_cnt=0`/`data_len=0`。
- 单测：`mclt_698_frame_test` 9 个用例，含 `read_layer_pads_missing_oad_ff`、`builder_keeps_missing_oad`、全缺/单 OAD 缺失/直接 698 回归。
- 源码提交：`c43f682a 🐞 fix:重庆分钟采集698 11E3上行帧缺项与表端回复不一致(QUOK-17)`；`6650c21d` 为版本号/日期 CI 提交。
- 板卡：`CHONG_QING_MODE / sversion 002761 / isv 060010`。

### 3.2 真机 Unity

- 9/9 `mclt_698_frame_test` PASS。
- 整轮 `48 Tests / 1 Failures`；唯一失败为既有 `jzjc_test.flow_cco_timeout_retransmits`（`Expected 22 Was 2`），与 0005 无关。
- 生产固件已恢复。

### 3.3 CCO 重庆固件 + 11E3 台架

- CCO 从 `fix_13762_develop`（`CHONG_QING_MODE`）编译烧录：`sv002741 / isv 060026`；上一轮安徽 CCO 的 `0x00E2/CFGFAIL` 阻塞消失。
- task 1 全链路 PASS：
  - 外层 00E3 `task_id=1`；
  - 内层 698 `C=C3`、APDU `85 02 01 03`、缺项 `20000200 01 FF FF`、APDU 长度 `0x22`、总长 54、TimeTag=17:10:00；
  - 06H-F230 任务号 1、协议 698.45、54B，嵌套 698 一致。
- task 0/1/15 外层 00E3 `task_id` 分别 0/1/15 正确；task 0 内部映射 `TASK_O_ID=16` 已验证。
- task 0/15 在 17:10 冻结时刻无数据，内层 698 未生成；后续发现 task 15 到 17:20:05 才存数、task 0 raw 配置后 CFGOK，最终“有数据后内层 698”补测未完成。

### 3.4 645 回归（PASS）

- task 1：协议 2、周期 5 min、DI `02010100`(2B)/`02020100`(3B)/`0203FF00`(3B)；F231/F232 后 CFGOK。
- STA：`recv mclt 645 data seq 0/1/2 dlen 2/3/3`，`Freeze data for task 1 stored successfully`。
- 11E3：外层 `task_id=1/protocol=2/fz_time=18:05:00/di_cnt=3/bufflen=56`；内层 3 个 645 帧，一 DI 一帧，无 698 APDU。
- 缺项场景 task 2：第三 DI `02030100` 被模拟表否认，11E3 帧数据为 `32 32 32`（配置长度 3），即 645 缺项仍 `0x32` 满长度。
- 06H-F230：task 1/2 任务号、协议 2、时间、56B 内容与 11E3 一致。
- 说明：模拟表不支持 `02030100`，正常场景用 `0203FF00` 替代；缺项场景专门用 `02030100`。

## 4. 验收

- [x] 698 代码/单测 v5 完成。
- [x] 真机 Unity 9/9 mclt PASS。
- [x] CCO 重庆固件编译/烧录/身份验证。
- [x] 11E3 task 1 全链路 PASS（内层 698 + 06H-F230）。
- [x] task 0/1/15 外层 00E3 task_id 正确。
- [x] 645 配置/采集/11E3/06H-F230/缺项 0x32 全 PASS。
- [x] task 0/15 内层 698 按各自冻结时刻补测（2026-11-03 冻结 01:00:00，task 0/1/15 ALL PASS；06H-F230 三方一致；证据 `/d/sta/01-sta/20261103-003456/`）。
- [x] 645 快速回归（task2 正常 + task3 缺项 0x32）、删除任务（单删/迁移快照/0xFF 广播）、STA 软 reboot 重连与存数保持、早于存数点读请求（result=2 空响应）。
- [ ] 0002/0004 完整回归、单相/三相、断电重启（本次明确跳过）。
- [ ] 跨地区（Anhui/Jibei）共用函数决策与回归。

## 5. 风险与遗留

1. **【已关闭 2026-11-03】task 0/15 内层 698**：2026-11-03 台架补测 PASS——冻结 01:00:00 后 task 0/1/15 全链路断言 ALL PASS（06H-F230 三方一致），风险消除。
2. **跨地区共用**：`more_frame_698_ack_struct()` 被 Anhui/Jibei 共用；如本分支要出安徽/冀北包，需协议确认或加地区守卫。
3. **APDU 长度 128~255**：当前单字节长度，超 255 失败；大任务需 `0x81/0x82` 或限制长度。
4. **【已关闭 2026-11-03】TimeTag**：台架实证——早于最早存储点的 11E3 请求返回 result=2 空响应（di_cnt=0/bufflen=0），不产生内层 698/TimeTag；成功帧 TimeTag=该任务实际冻结时刻。
5. **原始 reqs 数据丢失**：STA 工程原 `reqs/` 在台架后续清理中被清空；已在 `/home/H_STA/01/sta/reqs/0005...` 重建 REQS/PLAN/DONE/TODO 与恢复摘要，原始串口日志/截图需从备份恢复。
6. **台架操作经验**：CCO 烧录必须停桥、EDBG 直连 COM8；桥 PTY 会在 79% 超时。分钟采集 5 min 周期，任务要一次配齐、等每个任务存数后再发 11E3。
7. **台架实证结论——11E3 下发通道（决策收口）**：11E3 由 **CCO 自主发起**（冻结时刻后约 9 min，逐任务读 STA；实测 01:09:05–06 读 fz=01:00:00）；simcon 侧 raw APS 11E3 写 CCO 本地口（COM4）**不可达**（01:02:03、01:04:49 两次实测均无 rxlen:24 接收、无响应）。"下发通道"决策按台架事实收口为：**依赖 CCO 自主发起 + 工作台判据校验路线**（等待/判读 CCO 自主读产生的帧，而非测试端注入）。W4 read 场景"本地口注入 11E3"的设计前提不成立，场景机制 PASS、自动读链路需按 CCO 自主读口径使用（对 W3 场景文档语义的影响已记录至 TODO）。
8. **遗留清单（工程跟进，非台架阻塞；详见 TODO.md）**：
   - **P1【已关闭 2026-11-03，用户拍板】simeter 物理波特率**：config **保持 `config/serial_bridge.json` 9600E 不改**（已核实当前即 9600/E）；口径=锁 9600，表端/STA 会扫波特率。台架事实留档：2026-11-03 窗口静态 9600E 下 00:45/00:50 两轮采集全败（STA `mclt_state3 retry uart send max`、无 Freeze 存数），00:52:48 桥动态切 2400 后 00:55 轮 645（task 2/3）即存数、01:00 轮 5 任务全存数，01:24:08 会话清理已复位 9600（证据 `/d/sta/01-sta/20261103-003456/raw-logs/bridge-log-baud-switch-today.txt`、sta-session/simeter journal）；窗口内未观测到自主扫波特率事件，锁 9600 依赖扫描行为成立与否由后续台架按拍板验证；
   - **P2** simeter 脑 698 仅支持 jzjc OAD：698 分钟采集需 `inject_698_reply.py` 注入应答，长期建议加可配置 OAD 数据集；
   - **P2** STA 任务持久化逐 slot 且部分 slot 未落盘（软重启后仅 task1 恢复，`mcltList_length: 1`），建议核实 STA 工程 sector_manager 提交策略；
   - **P3** 11E3 自主发起机制对 W3 场景文档语义的影响（read 步骤口径）已记录，需在 W3/W4 场景文档补注。

## 6. 证据

- 2026-11-03 补测台架：`/d/sta/01-sta/20261103-003456/`（BENCH-task015-RETEST.md、断言脚本/输出、F230 帧、CCO 日志摘录、raw-logs）；CCO 侧 `/d/cco/001-cco/20261103-003456/raw-logs/`；镜像 `/home/H_STA/01/sta/reqs/0005.../evidence/BENCH-task015-RETEST-20261103.md`
- STA 需求与恢复摘要：`/home/H_STA/01/sta/reqs/0005-chongqing-sta-minute-collection-698-11e3/`
- 11E3/06H-F230 恢复摘要：`.../evidence/BENCH-v5-taskid-RECOVERED.md`
- 645 回归证据：`.../evidence/bench-645-regression-20261102-180650/`
- CCO 构建/烧录：`/d/cco/001-cco/20261102-164343/`、`/d/cco/001-cco/20261102-165256/`
- STA 构建/烧录：`/d/sta/01-sta/20261102-162725/`
- 645 原始日志：`/d/sta/01-sta/20261102-180650/`、`/d/cco/001-cco/20261102-180650/`
