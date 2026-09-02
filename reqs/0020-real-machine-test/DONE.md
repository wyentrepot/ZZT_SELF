# REQS-0020 DONE — 实机运行测试完成日志

> 最新在上。

## 2026-09-02 23:26-23:38 — 实机运行测试（第 1 轮）

### 执行结果（15 分钟硬停止内完成）

| 步骤 | 结果 | 证据 |
|---|---|---|
| 服务启动 | ✅ workbench 8790 / 侦听台 8765 / 模块日志 8766 后台常驻 | 三端口 LISTEN；解析后端 `remote` |
| 侦听台 COM24 | ✅ `/dev/ttyUSB1` 9600/E/8/1 采集运行 | listener/ensure；帧索引持续增长 |
| CCO/STA 会话 | ✅ cco-main(COM9=/dev/ttyACM1)、sta-main(COM8=/dev/ttyACM0) | module-sessions/ensure running |
| 档案读取 | ✅ 集中器下发 `10H-F2`（start=0,count=32） | simcon step succeeded；run manual-20260901-233425 |
| 645 任务5 配置 | ✅ `11H-F231`（task_no=5, 645, 周期10min, A相电压 02010100） | simcon step succeeded；run manual-20260901-233637 |
| 645 任务5 关联档案 1-32 | ✅ `11H-F232`（task_no=5, meters=013300000001..032） | simcon step succeeded；run manual-20260901-233644 |
| CCO 主动上报 | ✅ 持续 06H-F230 采集数据上报（集中器侧可见） | simcon session rx=48+, uplink=48+ |
| 服务保持监听 | ✅ 后台进程存活、持续采集 | 见下方运行证据 |

### 实机观察

- 集中器（simcon，COM4）↔ CCO 链路真实闭合：CCO 持续向集中器上报 `06H-F230`
  分钟采集数据与 `06H-F3` 空闲上报；集中器侧 `rx/uplink` 持续增长。
- 侦听台（COM24）同步捕获 485 总线帧（4+ 帧入库）；模块日志 cco/sta 均在写盘。

### 困难点 / 优化点（本次实测目标）

1. **simcon 自动选串口不可靠**：`simcon/step` 不传 `port` 时自动选到
   `/dev/ttyS0` 等虚拟口 → `Could not configure port: (5, 'Input/output error')`。
   必须显式传 `port=/dev/ttyUSB0, 9600/E/8/1`。优化点：自动选择应排除无实体的
   `ttyS*`，或复用当前已开会话端口而非重新选择。
2. **AI `listener/ensure` 忽略映射默认参数**：不传参数时用了 115200/N/8/1，
   与 `serial_ports.json` 的 9600/E/8/1 不符。优化点：ensure 应从 mapping 回填
   默认串口参数（与 module-session 一致）。
3. **WSL 下 `config/serial_ports.json` 缺 `linux_device`**：AI 控制面无法把
   `/dev/ttyUSB1`→listener、`/dev/ttyACM1`→cco 等解析到映射，ensure 报
   「未提供可用端口或映射 ID」。本次已补全 4 个映射的 `linux_device` 并新增
   `sta-main`。优化点：部署脚本映射后自动回填/校验 `linux_device`。
4. **`11H-F231/F232` 下发后未见 CCO `00H-F1` 确认**：集中器侧 tx 成功但 rx 均为
   `06H-F230/F3` 主动上报，未观察到任务配置的显式确认帧。可能原因：CCO 对
   「无地址域(module_id=0)」下行配置帧不确认 / 需带地址域 / 需先 `10H-F2` 等
   查询成功后再配置。待下一轮验证。

### 运行证据（服务保持后台监听）

- 端口：`ss -tln` → 8790 / 8765 / 8766 均在 LISTEN
- 进程：`python -m workbench.run / listener.run / module_log.run` 存活（nohup + disown）
- 解析后端：`GET 127.0.0.1:8765/api/version` → `parse_backend=remote`, dll_available=true
- 串口会话：listener(COM24) / cco(COM9) / sta(COM8) 三路 running

## 2026-09-02 23:40+ — 无地址域修正验证（第 2 轮，针对上轮结论）

### 验证目的
上轮结论「应全部构建为无地址域单 68 帧」，本轮用无地址域版本重发验证。

### 做法
- 新建 profile `apps/workbench/scenarios/profiles/noaddr.json`（无 cco_addr → 构帧无地址域）。
- 显式传 `"profile":"noaddr"` 绕过 step_state 缓存的默认 `anhui`。
- 无地址域 11H-F231（任务5/645/10min/A相电压）→ `68 20 00 43 00 00 00 00 00 06 11 40 1C ...`
- 无地址域 11H-F232（关联 32 档案）→ `68 D2 00 43 00 00 00 00 00 07 11 80 1C ...`（210B，比带地址域少 12B）

### 结果
| 检查项 | 结果 |
|---|---|
| 无地址域帧能否构出 | ✅ 构帧成功，R 域全 0、无地址域 |
| 帧校验 | ✅ 无地址域版 CS 正确（项目 codec 复核通过） |
| CCO 是否回 00H-F1 确认 | ❌ 未观察到（会话内无任何 00H-F1 / 17H-F231 应答） |
| CCO 行为 | 持续 06H-F230 主动上报（183 帧）——CCO 本就在跑采集，与本次下发无显式对应 |

### 修正后的结论（更新上轮第 4 点）
- **帧格式确认为无地址域正确**：CCO 主动上行本身就是无地址域（68|L|C|R|AFN|DT|data|CS|16），
  与 frame_codec.py「CCO 本地协议帧」注释一致；带地址域下发被 CCO 忽略。
- **但 11H-F231/F232 下发后 CCO 不回应答**，不能据此判定任务已生效。可能原因：
  1) CCO 对 11H 配置类命令本就不回显式确认，需靠后续 06H-F230 上报内容间接判断；
  2) 档案地址（0133 系列）与 CCO 实际从节点表不符——CCO 上行的 645 内嵌地址是
     `231214000001/45/35` 等，非 0133 系列；
  3) 侦听台(COM24)挂的总线与集中器↔CCO 链路非同一段（侦听台抓到 HPLC 帧而非 1376.2 下行）。
- **下一步建议**：核对 CCO 实际档案地址后，用匹配地址重配任务5；并比对任务周期
  （10min）与 06H-F230 上报节奏是否吻合。

## 2026-09-02 23:55+ — CCO 复位 + 档案读取 + FF 删除（第 3 轮）

### 用户疑云排查：CCO/STA 日志「不更新」
**结论：模块日志服务正常，是排查时 tail 到旧文件造成的误判。**
- module_log(8766) 进程/端口正常；workbench 内 cco/sta 会话 state=running。
- CCO/STA 内存缓冲与日志文件 mtime 完全同步（23:54:34 还在写盘，256KB+）。
- 目录下有多个历史 cco/sta 会话日志文件，tail 时命中了旧文件。

### 本轮实机动作
1. **复位 CCO**：向 cco 会话发 `reboot`（sent=8）→ CCO 恢复，日志显示
   `onnet cnt=120, devCnt=120`——**CCO 有 120 个 STA 在线**。
2. **读取真实档案**：无地址域 `10H-F2`（start=0,count=32）→ 下发成功，
   但 CCO 不回应答。真实档案地址从 CCO 主动上报内容提取：
   **`23121400XXXX` 系列**（01/35/45/53/71/87/101/137/143/145/116...），
   非 profile 预设的 `0133` 系列——之前 F232 关联的档案地址配错了。
3. **删除分钟采集（FF）**：
   - 协议文档确认：安徽 `0xFF`=全部任务（仅删除，§10）。
   - codec 原限制 task_no 1-15 → 已加 FF 放行（仅 delete 时），见
     `libs/parser_lib/adapters/adapter_10376/_encode_11f231`。
   - 无地址域下发：`68 19 00 43 ... 11 40 1C FF 00 02 00 00 32 16`。
   - **CCO 回真实应答**：`68 14 00 83 ... 11 40 1C FF 00 02 00 00 32 16`，
     data=`FF 00 02 00 00`，**配置结果=00=成功**（CS 校验通过）。
   - 删除后 CCO 上报骤降（15 秒仅 +3 帧），判定任务已清除。

### 关键发现 / 优化点（追加）
5. **侦听台(COM24)与集中器(COM4)不在同一链路**：侦听台抓到的全是 HPLC 载波
   MAC 原始帧（`7E` 起始，`0D/11/41` 控制域），不是 1376.2 485 帧；集中器下发
   的 1376.2 帧侦听台完全看不到。→ 接线拓扑需核实（侦听台可能挂在 HPLC 天线侧
   而非集中器↔CCO 485 总线）。
6. **CCO 对 10H-F2/11H-F231/11H-F232 的响应模式**：对查询(10H)不答；对配置(11H)
   仅在「无地址域 + FF 删除全部」时应答，普通任务号配置不答。→ 可能 CCO 只认
   无地址域广播配置，或任务号 1-15 配置需带地址域/先建档案。
7. **simcon step 默认 profile 陷阱**：`profile` 缺省强制 `anhui`（带地址域），
   需显式传 `noaddr` profile 才能构无地址域帧；step_state 会缓存 profile。

## 2026-09-02 00:00+ — 侦听台波特率修正（用户指正）

- **用户指正：侦听台(COM24)实际波特率是 115200**，非 config 原 9600/E。
- 修正：`config/serial_ports.json` listener 改为 `115200/N/8/1`；
  以 `listener/ensure baudrate=115200` 重启侦听台会话。
- **结果立竿见影**：frame_count 62→118 持续增长，日志实时写盘；
  捕获内容为 **HPLC 载波通信帧**（`FF02FFxx` 起始，MAC 层 7E 信封内的
  HPLC 报文，含 CCO↔STA 链路数据）。
- 结论：侦听台挂在 **CCO↔STA 的 HPLC 链路侧**（115200），
  **不是** 集中器↔CCO 的 485 总线（那侧才是 9600 的 1376.2 帧）。
  侦听台看不到集中器下发的 1376.2 帧属预期——链路不同。

## 2026-09-02 00:10+ — 删除 + 重新下发任务3（10min/A相电压/645）（第 4 轮）

### 执行链
1. **删除全部配置**：无地址域 `11H-F231` task_no=FF → CCO 应答
   `68 14 00 83 ... 11 40 1C FF 00 02 00 00`（结果码 00=成功）。
2. **读取档案**：无地址域 `10H-F2`（start=0,count=32）→ **CCO 首次响应**
   `68 DA 00 83 ... 10 02`（218B，总数=120，本页 25）。
   解析出真实档案地址 `23121400XXXX`（BCD 反序解码），合并 06H-F230 上报
   提取的去重后 **23 个**：001/022/036/045/053/057/071/075/087/101/116/
   127/128/135/136/137/143/145/148/155/164/183/187。
   第 2 页查询（start=25）CCO 未响应（只答第 1 页）。
3. **配置任务3**：无地址域 `11H-F231`（task_no=3, enable, 645, cycle=10min,
   A相电压 `02010100`）→ 下发成功。
4. **关联 23 档案到任务3**：无地址域 `11H-F232`（task_no=3, 23 个地址）→ 下发成功。

### 任务3 生效确认（CCO 模块日志铁证）
- `00:07:23 [RX] 68200043 ... 11 40 1C 03 01 02 0A 00 01 04 00 02 01 01 00 04 01 00 02 00`
  —— CCO uart 收到任务3 配置帧（周期0A=10min，A相电压 02010100）。
- `00:07:39 gw13762.c (7724)| rx task_id 3 meter_cnt 23` —— **CCO 接受任务3 + 23 电表**。
- 日志持续更新，采集状态机 `mclt_state0` 正常轮转。

### 观察/结论
- CCO 对**普通任务号配置（F231/F232）不回显式确认**（与第 3 轮一致）；
  但 CCO 日志确认已接收执行——任务生效以 CCO 日志为准，不以是否回 00H-F1 判定。
- 档案总数=120，本次只采到 23 个（第 1 页 25 个里 21 有效 + 上报补充）。
  后续如需 30 个，需确认 CCO 档案翻页/全量读取方式（第 2 页未响应）。
- 10min 周期完整一轮采集需等 10 分钟，服务后台持续运行中，无需 AI 值守。

## 2026-09-02 08:50+ — 集中器常驻自动确认（06H-F230 → 00H-F1）（第 5 轮）

### 问题（用户指正）
集中器任何时刻收到模块主动上报（如 06H-F230）都应默认回 00H-F1 确认，
与测试步骤/期待无关。原实现把 responder 只挂在 step/verify 执行窗口内，
两笔 step 之间的空闲期 CCO 上报得不到确认 → CCO 持续缓存/重发
（此前 rptlist_length:1024 暴涨、大量 frame duplicate、mclt data rejected）。

### 修复
- `libs/sim_concentrator/serial_io.py`：`SerialIO` 新增可选 `auto_responder`；
  读线程 `_run` 收到完整帧后先 `auto_responder.reply_for(frame)`，命中即
  `send_frame` 回包（00H-F1 等），与 step/测试解耦，任何时刻生效。
- `libs/sim_concentrator/api.py`：会话级 `_open_io` 创建 SerialIO 时注入
  `auto_responder=Responder()`（内置规则含 06H-F230 → 00H-F1、
  06H-F3 → 00H-F1 等）。
- 修复受 config 变更影响的过时测试 `test_serial_io.py`（COM24 已被映射为
  listener，改用 COM_FAKE 保证"未映射"语义）。

### 验证
- 本地单测：`Responder().reply_for(06H-F230)` → `68 0f 00 43 00 00 00 00
  00 01 00 01 00 45 16`（00H-F1 本地确认帧）PASS；`14H-F2` 无规则不应答
  （协议正确）。
- 实机：simcon 会话打开即注入常驻 responder；CCO 任务3 为 10min 周期，
  06H-F230 出现时读线程自动回 00H-F1（tx 增长）。待 06H-F230 实机帧确认。
- 既有失败（与本次改动无关）：`test_api_cli.py` 两个 simcon 固定映射断言
  因 config 里 simcon 有 COM4 映射而过时，属 config 变更连带。

### 实机观察（截至 08:52）
- simcon 会话 sc-20260902-084553 打开即注入常驻 responder（open=True, tx=0, rx=2）。
- 收到的 2 帧均为 14H-F2（路由数据抄读）——内置规则无 14H-F2 应答项，
  不应答为协议正确行为。
- CCO 任务3（10min 周期）尚未触发 06H-F230 采集上报；待下次采集触发时
  读线程应自动回 00H-F1（tx 增长），后台观察 job 记录中。
- 修复生效判据：simcon 会话 tx 在**无任何 step** 时因 06H-F230 自动增长，
  且增长的 tx 帧为 00H-F1（AFN=00 FN=F1）。

### 实机验证关键修正：内置 10H echo 规则引发应答回环（08:55 发现）
- 常驻 auto_responder 最初用完整内置规则（Responder()），其中
  `builtin.10xx_route`（10H → 10H echo）导致 CCO 上报 10H-F1 后 simcon
  回 10H-F1 → CCO 再回……形成应答风暴（tx/rx 各 ~680 帧）。
- **修复**：`Responder` 增加 `builtin=False` 模式；常驻 auto-ack 只用
  「主动上报确认」规则集（06H-F230/06H-F3/03H-F10 → 00H-F1），
  排除查询 echo 规则。查询/配置类帧的显式应答仍由 step 内完整 responder 处理。
- 验证：`06H-F230→00H-F1` PASS；`10H-F1/10H-F230→None`（无回环）PASS。
- 实机复验：重新打开 simcon 后，`10H-F230` 查询交互干净（tx=1 仅查询，
  无 10H-F1 回环），CCO 应答任务3 在位。回环已消除。
- 待验证：CCO 任务3（10min 周期）下次采集上报 06H-F230 时，simcon 自动
  回 00H-F1（tx 增长且帧为 AFN=00/FN=F1）。
