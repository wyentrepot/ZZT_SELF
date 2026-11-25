# REQS.md — 需求基线（需求 ID：0039，标题：48h 高频复现监控适配）

> 本文件遵循「需求只追加、不覆盖」ADR 模式。
> 立项日期：2026-11-07；关联：REQS-0007（STA 发送队列卡死四阶段验证 / C 阶段 48h）。
> 本需求是**工作台能力需求**，不是缺陷结论本身；缺陷结论见
> `/home/H_STA/01/reqs/0007-sta-queue-repro-longrun/evidence/FINAL_CONCLUSION_20261106.md`。

---

## 1. 背景

REQS-0007 的 C 阶段要求 48h 无人值守、高频抄表压力、缺陷复现时能抓到触发原因。当前用临时脚本
`/home/H_STA/01/reqs/0007-sta-queue-repro-longrun/tracks/c_longrun.py` 跑通了流程，但暴露了 6 个工作台缺口：

1. **simcon 高频抄表会突然崩塌**：`libs/sim_concentrator/batch.py::_read_one()` 用
   `seen = len(rx_history())` 当帧游标，而 `serial_io.py` 的 `_rx_history` 只保留最近 1000 帧；
   一旦到 1000，`hist[seen:]` 永远为空，所有应答都匹配不到 → 每轮 60s 超时 → 吞吐骤降。
   同时 `_rx_queue`（`pending_frames`）在 batch 期间无人消费，单调增长（实测 1170）。
2. **没有工作台原生长跑任务**：`/api/ai/v2/longruns` 不存在；只能靠本地脚本 + 会话内定时器，
   会话心跳链一断就没人知道；工作台「会话」页看不到这个任务。
3. **STA 固件日志门禁没打开**：`bsp/src/bsp_debug.c::bsp_debug_init()` 只设
   `debug_level = DBG_LEVEL_USER`（= `DBG_LEVEL_MDL`），`DBG_LEVEL_APP | DBG_LEVEL_MM` 被注释；
   `protocol/dll/inc/mac_log_conf.h` 的 `LOGGER_MAC_ENABLE=false` 让
   `media_access_task.c` / `csma_access.c` / `mac.c` 的 `LOG_*` 在编译期被裁掉。
   当前只有 `mac_intf.c`（`LOGGER_MAC_ENABLE1=true`）和诊断构建的 `mac_data_buff.c` 有日志。
   **后果：guard 能发现 `write` 被写坏，但抓不到“谁写的”**。
4. **没有“日志是否真的开着”的前置门禁**：无法从工作台侧证明固件带齐了 MAC/APP/MM 日志与
   ring buffer；用户无法确认监控是否真实生效。
5. **会话/证据不持久**：工作台重启会丢模块串口会话；证据散落在脚本目录，未自包含。
6. **物理映射缺校验**：COM↔mapping↔PTY↔桥接的对应关系没有启动前强校验，接错口也能“跑起来”。

## 2. 目标

一句话：**让 AI 工作台原生支持“48h 高频抄表 + 日志门禁 + 触发即抓现场”的复现场景**，
不再依赖临时脚本和人工盯守。

## 3. 需求点

### 3.1 长跑任务与心跳

| 编号 | 需求 | 验收 |
|---|---|---|
| LR-1 | 长跑任务定义/生命周期：`start/stop/status/list`，独立进程组、PID、退出码、状态机 `pending/running/reproduced/completed/failed/invalid/stopped`，重复启动幂等 | 一条命令启动任意脚本/内置高频任务；`status/list/stop` 正常；崩溃按策略重启 |
| LR-2 | 心跳接口：`GET /api/ai/v2/longruns/{id}/heartbeat` + CLI `longrun.py hb <id>`，单行 JSON ≤2KB、≤2s；默认 60s；字段固定 `state/elapsed/guard_hits/selfheal/staging/batch_ok/batch_fail/last_check/verdict` | 主代理只凭心跳即可判断 `running/reproduced/invalid/failed`；心跳不依赖聊天会话/UI |
| LR-3 | 1h 汇总 + 告警：每小时写 `hourly.jsonl`，guard 命中/进程死亡/日志停更/磁盘不足时告警并留证 | 48h 内每小时都有记录；异常 1 分钟内可见 |

### 3.2 高频抄表与背压

| 编号 | 需求 | 验收 |
|---|---|---|
| LR-4 | **修复 simcon 帧游标**：`batch._read_one()` 改用单调帧序号（`seq > last_seq`），禁止用 `len(rx_history())`；`rx_history` 截断策略与游标兼容 | 连续 1h ≥2 轮/分钟、成功率 ≥95%，不再出现“到 1000 帧后全超时” |
| LR-5 | **pending_frames 有界**：`_rx_queue` 设上限/丢弃策略；batch 消费或定期 drain；`pending_frames` 超过阈值告警并自动复位 | 48h 运行 `pending_frames` 不单调增长；复位后 1 轮内恢复 |
| LR-6 | **节流与退避**：单轮完成再发下一轮；失败退避 60/120s；连续失败 3 次或每 250 轮自动 `simcon close/open`；目标可持续 2~10 轮/分钟 | 台架饱和时自动降速并恢复，不把“台架无效”记成负结果 |

### 3.3 日志监控门禁与触发捕获

| 编号 | 需求 | 验收 |
|---|---|---|
| LR-7 | **固件 diag_caps 自描述**：诊断固件在启动 banner / AT / 1376.2 查询中返回启用的日志域（MAC/APP/MM/MDL）、ring buffer 容量、guard/canary 版本、固件 md5 | 工作台可只读查询；无 diag_caps 视为不满足 |
| LR-8 | **启动前日志门禁**：长跑 preflight 必须校验 diag_caps；缺 MAC/APP/MM 任一必需域或 ring buffer 未启用 → 拒绝启动并给出缺失项 | 用一个“日志关闭”的固件启动必须被拒绝；全开固件可启动 |
| LR-9 | **触发即抓现场**：guard/canary 命中时，固件 dump 最近 N 条 ring buffer（合法写历史、任务上下文、邻居越界哨兵），工作台同步抓取最近 2MB 日志、`heartbeat.jsonl`、`status.json`、`diag_caps`、固件 md5、COM 映射、simcon/CCO 状态，写 `manifest.json` | 命中后 60s 内证据自包含，能回答“谁在什么时刻写了 write”或给出有效排除证据 |
| LR-10 | **日志域要求**（固件侧）：诊断构建必须打开 `media_access_task.c` / `csma_access.c` / `mac.c` / `mac_data_buff.c` 的 MAC 日志，`bsp_debug_init` 打开 `DBG_LEVEL_APP | DBG_LEVEL_MM`，并为 `nwk_intf_fifo` / `subMap` / `tei_set_bitmap` 加边界哨兵 | 全量日志构建可编译、可烧录、可入网；ring buffer 不显著影响时隙 |

### 3.4 可见性 / 持久化 / 映射

| 编号 | 需求 | 验收 |
|---|---|---|
| LR-11 | 工作台「会话」页新增“长跑/48h 任务”卡片：状态、elapsed、guard、batch_ok/fail、最后心跳、证据入口 | 不打开文件也能看到任务是否在跑、是否命中 |
| LR-12 | 会话持久化：工作台重启后自动恢复 module_log/listener/simcon/simeter 会话；长跑任务标记 `interrupted` 并可续跑 | 重启不丢采集；续跑不丢 48h 证据 |
| LR-13 | 物理映射 preflight：COM↔mapping↔PTY↔桥接 TCP 双向校验，展示 Windows 设备名/Status/baud/parity；不匹配拒绝启动 | 接错口/错映射时启动失败并指出具体端口 |
| LR-14 | 交付物：48h `summary.md/json`、证据目录、复盘报告；负结果必须含 `batch_ok/send_ok/pending_frames/日志增长` | 能区分“无复现”与“台架无效” |

## 4. 验收标准

- [ ] LR-1~LR-14 实现；既有 v2 接口回归通过（`verify_api_inventory.py` 无新增破坏）。
- [ ] 48h 无人值守跑通：每小时心跳/汇总齐全，进程死亡能自动重启并留证。
- [ ] 高频抄表连续 1h ≥2 轮/分钟、成功率 ≥95%，`pending_frames` 不失控；simcon 游标 bug 修复有回归用例。
- [ ] 日志门禁生效：日志关闭的固件被拒绝启动；全开固件可启动并能在 guard 命中时抓到 ring buffer + 最近日志。
- [ ] 「会话」页可见长跑任务；工作台重启后可恢复会话/续跑。
- [ ] COM 映射校验通过；接错口时启动失败并给出具体端口。

## 5. 非目标

- 不在本需求内修复 STA `write` 被写坏的具体缺陷（属 REQS-0007 C2）。
- 不改变量产固件默认日志级别；诊断日志只进诊断构建。
- 不重构既有 module-log/listener/simcon/simeter 业务语义。
- 不 push 任何仓库。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 全量 APP/MM/MAC 日志淹没 115200 串口、影响时隙 | ring buffer + 定向日志；只在 guard 命中时 dump；日志限速 |
| 高频抄表再次打饱和 CCO/simcon | LR-4/5/6 背压 + 自动复位 + 成功率门禁 |
| 工作台重启丢会话 | LR-12 持久化/自动恢复 |
| 固件 diag_caps 无法从外部验证 | 启动 banner + 只读查询双通道；preflight 强校验 |
| 48h 内未复现 | 预先定义有效负结果指标；保留 ring buffer 健康计数 |

## 7. 关联

- 缺陷结论：`/home/H_STA/01/reqs/0007-sta-queue-repro-longrun/evidence/FINAL_CONCLUSION_20261106.md`
- simcon bug 证据：`.../evidence/SIMCON_BATCH_CURSOR_BUG.md`
- 当前临时长跑：`.../tracks/c_longrun.py`
- 台架交接：`/home/H_STA/01/reqs/0007-sta-queue-repro-longrun/HANDOFF_20261107.md`
- 现有 WM 能力基线：REQS-0007 WB-1~WB-10、REQS-0018、REQS-0036
