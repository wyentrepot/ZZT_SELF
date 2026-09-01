# REQS-0017 — AI 排查能力强化（分钟采集漏点排查方法论固化）

> 状态：🚧 进行中
> 创建：2026-09-01
> 关联：REQS-0013（1376.2 帧页面化）、REQS-0009（通信流追踪）、ai-control-plane 技能（使用经验沉淀）

## 1. 背景与问题

2026-09-01 排查安徽分钟采集「漏点」（某周期显示 15 帧应 16）时，发现**排查主要靠人工直接查
sqlite 索引库 / CCO 日志 / simcon 库**，而非走既有 ai-control-plane 技能的控制面路径。过程暴露出
技能在**离线数据排查**维度的多处不足：

1. **无"离线日志 / 索引库直查"参考**：排查依赖直接读 `.build_plain/apps/listener/runtime/indexes/idx-*.sqlite3`、
   `data/listener_13762.sqlite`、`data/logs/模块/cco/*.log`、`data/logs/simcon/sc-*.jsonl`，技能只覆盖 HTTP 控制面。
2. **分钟采集分析的口径陷阱未文档化**：`task-minute-analysis` 按**上报时刻**分桶，`freeze_time`（冻结时刻）
   才是权威归属；两者在存在迟报/补采时结果不一致，易误判"假缺报"。
3. **双通道概念缺失**：系统存在 06F230（1376.2 采集上报，被 minute_reports 统计）与 11E4（另一主动上报通道）
   两条通道，行为不同，技能无此概念，排查时容易混淆。
4. **CCO 日志帧格式未收录**：排查靠手动 grep CCO 日志中的 `683300c3...06201c01`（06F230）、`11e4`、`11e3` 帧，
   无统一解析参考。
5. **无"链路断线时段"排查指引**：12:00~14:00 实为 CCO 主链路断线无数据（`11:16:19` 串口关闭、`14:09:36` 重开），
   技能未提示"先确认采集链路在线"。
6. **组合排查场景缺位**：SKILL.md 最小路径表"只读一个 reference"的约束，在需要 listener+simcon+observations
   多端交叉验证时过死。

## 2. 目标

1. 新增 **references/offline-analysis.md**：索引库 / 日志库 / simcon 库的路径、表结构、查询示例。
2. 在 **listener.md** 补充"分钟采集分析分桶口径"（上报时刻 vs 冻结时刻）与 `minute_reports` 表字段语义。
3. 新增 **references/cco-log.md**：CCO 日志帧特征（06F230 / 11E4 / 11E3）、正则、冻结字段解析、双通道说明。
4. 在 SKILL.md 最小路径表增加"离线数据排查 / 漏点定位"场景及组合参考链。
5. 补充"先确认采集链路在线"检查项（observations.md 或新参考）。

## 3. 设计

### 3.1 离线数据源速查（offline-analysis.md）

| 数据源 | 路径 | 存什么 | 排查用途 |
| --- | --- | --- | --- |
| 侦听台索引库 | `.build_plain/apps/listener/runtime/indexes/idx-*.sqlite3` | `frames` + `minute_reports` | 分钟分析数据源（权威） |
| 侦听台原始日志 | `.build_plain/data/logs/侦听台/listener_*_自动保存.txt` | 7E HPLC 帧原文 | 索引库原料 |
| 模拟集中器库 | `.build_plain/data/listener_13762.sqlite` | `frame_log` + `report_event` | 06F230 主动上报（记录面不全） |
| simcon 会话帧 | `.build_plain/data/logs/simcon/sc-*.jsonl` | 1376.2 帧 JSONL | 会话分段记录 |
| CCO 模块日志 | `.build_plain/data/logs/模块/cco/` | 固件运行日志 | 发送侧真相 |

### 3.2 分桶口径（listener.md 增补）

- `list_task_minute_periods` 按 `time_seconds % period_ms`（上报时刻）分桶。
- 判定真实缺报应以 `freeze_time`（冻结时刻）为准；页面显示"去重后 STA 数"与"缺报"需结合两者。

### 3.3 CCO 日志帧特征（cco-log.md）

- `683300c3...06201c01` → 06F230 采集上报（CCO → 集中器）
- `11e4 00000132xx ... 0001 <STA> 0102 00XX 1401092601XX00` → 11E4 主动上报（`00XX`=冻结分钟）
- `11e3 ... 0200 0022 <STA> 0100 XX 14010926` → 11E3 补采指令
- 冻结字段解析：11E4 `01020020` 的 `0020` = 冻结分钟；06F230 `0200 44/46/48...` 每周期 +2 = 周期序号。

### 3.4 排查方法论（沉淀进使用经验）

1. 先还原页面统计口径（分桶键）。
2. 用冻结时刻做权威核对。
3. CCO 日志（发送侧）+ 侦听台索引（接收侧）双端交叉验证。
4. 排除链路断线时段（先确认采集链路在线）。

## 4. 验收

- [x] `references/offline-analysis.md` 存在，含全部数据源路径与表结构。
- [x] `listener.md` 含分桶口径说明。
- [x] `references/cco-log.md` 存在，含三类帧特征与冻结字段解析。
- [x] SKILL.md 最小路径表含"离线数据排查 / 漏点定位"场景。
- [x] 使用经验目录含本次排查文档（已建：`安徽分钟采集漏点排查.md`）。

> P0 验收通过：2026-09-01 提交技能仓库 d1cf162；P1（页面/分析改进）未启动。

## 5. 变更记录

- 2026-09-01 需求建立，来源于安徽分钟采集漏点排查复盘（技能使用不足清单 6 项）。
- 2026-09-01 P0 技能文档补全完成（提交技能仓库 d1cf162），验收 5 项全过；P1 待排期。
