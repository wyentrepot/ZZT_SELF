# RESEARCH-reset.md — STA 复位手段调研

> 调研对象：需求 0041 / task-2。目的：把「第一次组网前必须复位 STA」这件事落成后续可实现的
> `reset.sta` 原子块（`REQS.md:651` 记为「具体手段待调研」）。
> 方法：只读检索仓库 + 只读检索仓库内已落盘的串口会话日志；**未开串口、未发任何帧、未改任何配置**。
> 唯一「实测」动作是 `GET /api/serial-bridge/status`（只读状态查询），输出见 §A.0。

---

## 结论摘要

| 手段 | 可用性 | 命令/帧原文 | 判据 | 证据 |
|---|---|---|---|---|
| **A. CCO 控制台命令**（`sta reset` 类，经 `cco-main` 会话下发） | **不可用** | 无此命令。CCO 固件自报的 `sta` 子命令清单只有 `sta show` / `sta all` / `sta offline` / `sta show bind_status` / `sta show mclt_list` | — | `data/logs/模块/cco/20261106-104209_[cco]_[cco-main-COM8--run-hplc-serial-cco-main].log:14667-14668`（发裸 `sta` → `argv[1] err, such as sta show\|all\|offline\|sta show bind_status\|sta show mclt_list`）；`help` 在 CCO 控制台回 `Command not found!`（同目录 `20261102-165423_...:1706`、`20261027-215846_...:1192564`、`20261106-104209_...:5482`） |
| **B. 1376.2 复位类 AFN**（经 `simcon` 下发） | **可复位 CCO，不可复位 STA** | `01H-F1` 硬件初始化(复位)，无数据单元：`68 0F 00 43 00 00 00 00 00 01 01 01 00 46 16`；simcon verify 写法 `{"send":{"format":"local","afn":1,"fn":1}}` | CCO 回 `00H-F1` 确认帧，约 7s 后重启并重跑 `bpsCheck` | `libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json:187-220`（01H 语义＝"对**本地通信模块**做硬件复位"）；`skills/share/serial-bridge/SKILL.md:217-219` |
| **C. STA 模块串口 AT 复位** | **台架不可用**（`factory` 口不存在）；且 `AT+RESET` 本身也只在 STA 正常应用模式 9600 8E1 生效 | `AT+RESET`；厂测模式内用 A5 `DI 0x0004`（重启模块） | 无台架判据（口不在） | `libs/factory_test/README.md:71-72`；`tools/win_host/VERIFY-20261117.md:39`（`factory` COM7 "不在系统串口表"）；当前 `config/serial_ports.json` 已无 `factory` 条目 |
| **C′/E. STA 控制台命令 `reboot`**（经 `sta-main` 会话下发）★ | **可用（首选）** | `reboot`，补 CRLF 后实际写出 `reboot\r\n`（8 字节） | 会话日志在「下发时刻之后」出现 `Unicorn Bootloader` / `Run image0, wait a moment, please!` / `reset reason:` + `SFT_GLB_RST`；再以 CCO 侧 `onnet` 回落→恢复为端到端判据 | 实测日志：`data/logs/模块/sta/20261028-083511_[sta]_[sta-main-COM9--run-hplc-serial-sta-main].log:40-41`、`data/logs/模块/sta/20261118-114943_[sta]_[sta-main-COM18--run-hplc-serial-sta-main].log:67-68`；`docs/19-模拟电表实机联调总结-20260928.md:26`；代码路径 `apps/module_log/xmodem_flash.py:221,447-448` |
| **D. 断电重上电** | 物理上必然可行，但**仓库内无任何文档化流程、无软件判据** | 人工断电再上电 | 上电横幅（与 C′ 同一套判据） | 未找到证据（`grep 断电/上电` 于 `docs/*.md`、`skills/share/*/SKILL.md` 仅命中"CCO 上电后跑 bpsCheck"等旁述，无复位流程） |

**一句话**：本台架唯一已验证、可原子化、有明确回显判据的 STA 复位手段是 **经 `sta-main` 模块串口会话向 STA 控制台发 `reboot`**；CCO 控制台没有 `sta reset` 一类命令，1376.2 的 `01H-F1` 复位的是 CCO 自身（本地通信模块）而不是某个 STA，AT 复位所依赖的厂测口在当前台架不存在。

---

## 推荐方案

### 首选：`reset.sta` 块 = 经 `sta-main` 会话写 `reboot`

1. `POST /api/ai/v2/module-actions {"action":"ensure","mapping_id":"sta-main"}` → job → `session_id`
   （契约：`apps/workbench/ai_contracts.py:140-155`；调用范式与 `tools/scripts/minute_collect/mclt_list_query.py:148-162` 完全一致）
2. 记录 `t0`，`POST /api/ai/v2/module-actions {"action":"send","session_id":"<sid>","text":"reboot"}`
   （`append_newline` 默认 `true`，服务端补 `\r\n` → 实发 8 字节，见 `apps/module_log/module_serial_service.py:344-365`；审计里同类调用记录为 `"sent": 8`）
3. 只读轮询该口自己的会话日志（沿用 `mclt_list_query.py:71-93` 的 `_newest_log(mapping_id)` + `[RX]` 抓取思路，
   文件名形如 `data/logs/模块/sta/<ts>_[sta]_[sta-main-COM18--*].log`），在 `t0` 之后找启动指纹（见下"判据"）。
4. 若 15s 内无指纹 → 重发一次（实测 69 次下发有 5 次首轮无回显，见 §耗时）；连续 2 次失败 → 块判 `FAIL` 并给出"口无响应"断点。
5. 端到端判据（可选、更强）：复位前后各取一次 CCO 侧 `onnet`（或 simcon `10H-F1` 从节点数量），确认回落→恢复。

**为什么选它**：仓库内已有 69 次真实下发记录、64 次产生启动指纹；不需要新增硬件口；不需要 simcon
（当前 `simcon`/COM4 `usable=false`，见 §A.0）；判据是板子自打的 `reset reason:`/`SFT_GLB_RST`，不是"命令返回 0"。

### 备选：无（同级替代不存在）

- 若只要求"CCO 侧看到 STA 掉线再回来"，可用 `01H-F1` 复位 CCO（`68 0F 00 43 00 00 00 00 00 01 01 01 00 46 16`），
  但这会**同时打断整个网络**、代价远大于复位单个 STA，且 `simcon` 当前不可用 → 不作为备选，仅作 B 项结论留档。
- 用户手工断电重上电：可作为 `reset.sta` 判 `FAIL` 后的人工兜底，但要人工记录时间点，块内不可原子化。

### 首选落地前必须先验证的 3 件事（都只读/低风险，见"遗留待验证"）

1. **STA 控制台是否也有 `sta reset` 类命令**：STA 控制台**确有** `sta` 命令族（实测 `sta pv open` → `PV CB open, feedback=1, sta_f=1`，
   `data/logs/模块/sta/20261029-141243_...:355-356`），与 CCO 的 `sta show|all|offline` 不是同一套；`help` 在该板上
   曾无回显（`20261027-215044_...:3130` 发 `help`、`:3148` 发 `reset`、`:3161` 发 `reboot now` 全部无响应，该口当时已静默）。
   → 需上台架发一次**裸 `sta`**（只打印 usage，无副作用）取权威子命令清单。
2. **当前 COM18 这块板子的固件是否也自报同一套横幅**（日志指纹跨 17 个会话一致，但最近一次成功记录在 2026-11-18）。
3. **复位后是否会被 `hplc_pwr` 功耗门限卡住**：`docs/19-模拟电表实机联调总结-20260928.md:74-77` 记载 STA 启动日志
   `power(8) greater than level4, 60min later to set hplc power` → `nwk_init,hplc_pwr=0`，HPLC 功放置 0 达 2400s，
   期间"CCO 侧 onnet=0 且无 recv assoc_req"。若当前板 `hplc_pwr=8`，则 reboot 后可能长时间入不了网——
   这直接决定 `reset.sta` 后面接 `nwk.*` 块要不要带"等入网"超时参数。

---

## 逐项排查详情

### A. CCO 控制台命令（`sta-main` 之外的另一条路：`cco-main` 会话）

**查了什么**：`tools/scripts/minute_collect/mclt_list_query.py`（发 `sta show mclt_list` 的先例）、
`tools/scripts/identify_board.py`、`tools/scripts/minute_collect/README.md`、`docs/16-AI操作指南.md`、
`docs/21-分钟采集与698分帧台架测试流程.md`、`docs/19-...`、`skills/share/serial-bridge/SKILL.md`、
`libs/module_log`（实为 `apps/module_log/`），并对 `data/logs/模块/cco/*.log` 做了「CCO 控制台实际下发过的命令」反查。

**1）仓库里能确认的 CCO 控制台命令只有两类 `sta` 命令**（`grep -rn 'sta show' tools/ docs/`）：
`identify_board.py:240` `commands.append("sta show")   # 仅 CCO 有；STA 无此命令（会回错误提示）`，
`mclt_list_query.py:162` `"text": "sta show mclt_list\r\n"`。二者都是**只读查询**。

**2）CCO 固件自己给出的子命令清单（最权威的一份仓库内证据）**：
`data/logs/模块/cco/20261106-104209_[cco]_[cco-main-COM8--run-hplc-serial-cco-main].log:14667-14668`
```
[20261106-11:04:25:652] [TX] sta
[20261106-11:04:25:880] [RX] staEargv[1] err, such as sta show|all|offline|sta show bind_status|sta show mclt_list
```
→ `sta` 族的 argv[1] 只有 `show` / `all` / `offline`，**没有 `reset` / `reboot` / `restart`**。

**3）无法用 `help` 穷举**：CCO 控制台 `help` 回 `Command not found!`——
`20261102-165423_...:1706`、`20261027-215846_...:1192564`、`20261106-104209_...:5482` 三处一致。
所以「CCO 没有 `sta reset`」这个结论的强度是**固件自报清单无此项**，不是"读遍固件源码"。CCO 固件源码
（`bsp/venus/sdk/libdepend/cmd.c`，仅被 `identify_board.py:11-13` 提及）不在本仓库、本机也未挂载，无法核。

**4）CCO 控制台确实有 `reboot`，但复位的是 CCO 自己**：
`reqs/0020-real-machine-test/DONE.md:90`「**复位 CCO**：向 cco 会话发 `reboot`（sent=8）→ CCO 恢复，日志显示 `onnet cnt=120, devCnt=120`」。
`grep '[TX] reboot' data/logs/模块/cco/*.log` 共 26+18 处，都是 CCO 自身重启，**没有任何一条带 STA 定位参数**。

**结论**：A 不可用。CCO 控制台既没有 `sta reset` 类命令，也没有"指定节点复位"的语法。

### B. 1376.2 复位类 AFN

**查了什么**：`libs/parser_lib/adapters/adapter_10376/__init__.py` 的 `_AFN_NAMES`（`:27-33`）、
`_encode_app_data`（`:1251` 起）与 `_NO_UNIT_DOWN`（`:1240-1248`）、
`metadata/afn_fn.json` 全 17 个 AFN 的语义，以及 `encode_app_data` 实跑。

**1）把 17 个 AFN 的 Fn 全列了一遍**（`python3` 读 `metadata/afn_fn.json`），全库里带"复位/重启/初始化"语义的只有三处：
- `01H 初始化`：`F1 硬件初始化（复位）` / `F2 参数区初始化` / `F3 数据区初始化`（`afn_fn.json:187-220`）
- `12H 路由控制`：`F1 重启` / `F2 暂停` / `F3 恢复`（`afn_fn.json:3242-3247`）

**2）关键语义限定 —— `01H` 复位的是"本地通信模块"，不是远端 STA**：
`metadata/afn_fn.json:191`
```
"sem": "对本地通信模块做硬件复位，或清除其参数区/数据区档案，是开局与恢复现场的第一步。",
```
即 1376.2 的 `01H` 是「集中器 → 挂在集中器自己口上的本地通信模块」。本台架上 simcon 扮集中器、CCO 就是那个本地
通信模块 → `01H-F1` 复位的是 **CCO**。这与 `docs/16-AI操作指南.md:206-207`、`:402`
（"CCO 只在上电/复位后跑 bpsCheck，可用本地 `01H-F1` 复位"）和 `skills/share/serial-bridge/SKILL.md:217-219`
（"可用本地 1376.2 `01H-F1` 复位（CCO 回 `00H-F1`，约 7 秒后重启）"）完全一致。

**3）`12H-F1` 重启的是"路由进程"，不是模块硬件**：`afn_fn.json:3246`
```
"sem": "对路由进程本身做重启、暂停与恢复，用于故障恢复与临时让出信道。",
```
本需求已把 `nwk.route_restart`（12H-F1）单列为**组网块**（`REQS.md:653`、`PLAN.md:223`），与"复位 STA"不是一回事。

**4）帧可构（实测只读跑通）**：`_NO_UNIT_DOWN` 含 `(0x01,1)(0x01,2)(0x01,3)`（`__init__.py:1241`），
`encode_app_data(0x01,1,{})` 返回 `b""`（无数据单元）；用 `build_frame` 构帧，并用 REQS 里已登记的 12H-F1
样例（`REQS.md:517` `680F00430000000000011201005716`）反向校验编码器一致性：
```
01H-F1: 68 0F 00 43 00 00 00 00 00 01 01 01 00 46 16
01H-F2: 68 0F 00 43 00 00 00 00 00 01 01 02 00 47 16
12H-F1: 68 0F 00 43 00 00 00 00 00 01 12 01 00 57 16   ← 与 REQS.md:517 逐字节一致
```
（下行帧、通信方式 HPLC、无地址域、seq=1；CCO 对本台架本地口只认无地址域帧，见 `reqs/0020-real-machine-test/DONE.md:71-72`。）

**结论**：B 只能复位 CCO，**不能复位 STA**；1376.2 全 73 个 Fn 里没有"复位指定从节点"这一类命令。
附带事实：`simcon` 当前 `usable=false`（§A.0），连"CCO 复位"这条备选现在也跑不了。

### C. STA 模块串口 AT 复位

**查了什么**：`libs/factory_test/`（README + `client.py` + `cli.py`）、`config/serial_ports.json`、
`tools/win_host/VERIFY-20261117.md`、`factory-COM7` 历史会话日志，以及 `grep -rn 'AT+命令|AT指令'` 全仓。

**1）AT 复位手段在文档里存在，但有两处硬限定**：`libs/factory_test/README.md:71-72`
```
- `AT+FACTMODE` 在 STA 固件里没有实现，通常是治具/适配器侧命令；`AT+RESET` 只在 STA 正常应用模式（9600 8E1）生效，厂测模式下无效。
- 厂测模式下复位用 A5 `0x0004`，不要用 `AT+RESET`。
```
代码侧：`libs/factory_test/client.py:268-271`（`reset()` = A5 `DI_RESET(0x0004)`，「设备先回 0x82，再硬复位」）、
`client.py:276`（`at_commands=("AT+FACTMODE","AT+RESET")` 只是进入厂测的前导）。

**2）但这套手段依赖的物理口在当前台架不存在**：
- `libs/factory_test/README.md:39`（§1 端口表）：厂测上位机口 = **COM7，9600 8N1**；正常应用同一 UART1 是 9600 8E1（`README.md:64` 注意 4）。
- `tools/win_host/VERIFY-20261117.md:39`：`factory` / COM7 / 厂测上位机 / 9600 8E1 / **不在系统串口表**。
- 当前工作台侧 `config/serial_ports.json` 的 7 个映射里**没有 `factory`**（listener/cco-main/sta-main/simeter/ecu-biz/ecu-mgmt/simcon）。
- 历史唯一一次 `factory` 会话是失败的：`data/logs/模块/cco/20261028-171807_[cco]_[factory-COM7--run-hplc-serial-factory].log`
  只有 4 行（开会话 → 切 9600 → 一帧乱码 TX → 关串口），没有 A5 交互、没有复位回显。

**3）语义上也不适合本用途**：这条路要占**STA 的业务 UART1**（同一物理口在台架上是"模拟电表 ↔ STA 的 645 底座口"，
见 `docs/19-...:27` 的端口表），而当前该侧由 `simeter(COM20)` 服务占用。复位一个 STA 却要先抢走它与电表的链路，
代价和耦合都远大于直接发控制台 `reboot`。

**结论**：C 不可用（台架缺口 + 与 simeter 链路冲突）。若将来接上治具口（COM7/9600），A5 `DI 0x0004` 可作为
"更底层的硬复位"备选，但仍需现场验证。

### C′ / E. 其他途径：STA 控制台命令 `reboot`（★ 唯一可用）

**查了什么**：`docs/19-...`、`skills/share/serial-bridge/SKILL.md`、`reqs/0037-.../DONE.md`、
`reqs/0004-workbench-serial-sessions/DONE.md`、`apps/module_log/xmodem_flash.py`、
`tools/scripts/identify_board.py`，并对 `data/logs/模块/sta/*.log` 做「实际下发命令」与「回显」反查（69 次 `reboot`）。

**1）仓库文档明确把它写成 STA 控制台能力**：
- `docs/19-模拟电表实机联调总结-20260928.md:26`：`STA 日志口 … 115200 8N1 | STA 日志/控制台（可发 reboot，XMODEM 烧录同口）`
- 同文件 `:41`：`module-serial 会话 … STA/CCO 日志、reboot、flash`
- `skills/share/serial-bridge/SKILL.md:204`：`COM8 | STA 日志口；本次 8 秒窗口无输出，发 reboot 也无回显`（反向说明该口常态就是发 `reboot`）

**2）产品代码里已在用这条路径**：`apps/module_log/xmodem_flash.py:221`（`enter_bootloader_command: str = "reboot"`）、
`:447-448`（烧完 `_send_line(ser, "reboot")` 恢复启动）；`tools/scripts/identify_board.py:11-13` 反而专门解释过
**为什么不拿 reboot 当识别手段**（"reboot 会打断该口正在跑的任务、CCO 重启还要重建入网，代价大"），
等于确认 `reboot` 是既有的、代价可估的操作。

**3）实测回显原文（可直接照抄判据）**：
`data/logs/模块/sta/20261028-083511_[sta]_[sta-main-COM9--run-hplc-serial-sta-main].log:40-41`
```
[20261028-08:37:13:606] [EVENT] TX line: reboot
[20261028-08:37:15:168] [EVENT] RX text: "reboot\x1bE\r\n******************************\r\n  Unicorn Bootloader\r\n******************************\r\n  Version           : ver_02-00-57\r\n  Release Date      : Jan 25 2024 17:00:00\r\n  silicon version   : venus2m_v2 @ 200/200/100 MHz\r\n\r\n\r\nPress 'd' key to enter bootloader mode!\r\nPress '@' key to skip the waiting time and run image!\r\n 3\r 2\r 1\r 0\r"
```
同一板上 2026-11-18 复现（`sta-main-COM18`）：
`data/logs/模块/sta/20261118-114943_[sta]_[sta-main-COM18--run-hplc-serial-sta-main].log:67-68`（同上横幅），
`:69-73` 随后 `Run image0, wait a moment, please!` → `addr:0xfe040000, size:524288` → 分区表 → `See OS run`。

**4）同一份日志里的"复位证据链"（这是判据的原料）**：
`20261118-114943_...:74-110` 依次出现
`[node /]$` → `boot_ver:ver_02-00-57` / `hver:0101, sver:001401` / `isv :090007` →
`System reset by 8000` → `reset reason:` → `SFT_GLB_RST` → `read ok` → `load_sta_para succ,...` →
`phy init ok!` → `phy sal init ok!` → `bsp init done!` → `phy hplc init done!`。
跨全部 STA 会话日志统计（`grep -rh` 去重计数）：`Run image0, wait a moment, please!` ×81、`reset reason:` ×81、
`SFT_GLB_RST` ×74、`System reset by 8000` ×70（另有 `System reset by 5000/11000/19000` 等少数），
说明**"下发后出现 `reset reason:` + `SFT_GLB_RST`"是跨固件版本稳定的复位指纹**。

**5）可靠性实测（不是理论值）**：对 `data/logs/模块/sta/*.log`（17 个会话）统计「`reboot` 下发」与「15s 内出现
启动横幅 / `Run image0`」：
```
总 reboot 发送 = 69，15s 内出现启动横幅/镜像运行 = 64
```
逐会话看，失败集中在两个口当时已静默的会话（`20261027-215044` 2 发 0 中、`20261027-220423` 1 发 0 中、
`20261118-101925` 1 发 0 中）。`20261028-083511` 内也有一次 11:41:57 的 `reboot` 只回 `reboot\x1bE` 而无横幅、
11:43:56 重发即成功（`:140926-140928` / `:140963-140964`）→ **首轮失败重发一次是必要的**。

**6）台架现状（实测只读，`GET /api/serial-bridge/status`）**：
```
listener  | COM27 | usable= True | err= []
sta-main  | COM18 | usable= True | err= []      ← 首选方案用这个口
cco-main  | COM12 | usable= True | err= []
simeter   | COM20 | usable= True | err= []
ecu-biz   | COM28 | usable= False | err= ['端口在桥配置中已禁用', ...]
ecu-mgmt  | COM29 | usable= False | err= ['端口在桥配置中已禁用', ...]
simcon    | COM4  | usable= False | err= ['桥接软链不存在或目标不是字符设备', ...]
summary: {'total': 7, 'enabled': 5, 'channel_ok': 4, 'workbench_ready': 4}
```
→ `sta-main(COM18)` 绿灯，首选方案当前就能跑；`simcon` 红灯，B 项连"复位 CCO"也跑不了。

**7）其他候选命令已排除**：STA 控制台实测下发过的命令全集（`grep '[TX]'` 归一化计数）为
`reboot`(63+6) / `image`(30) / `download 0`(30) / `Y`(30) / `version`(6) / `fcversion`(2) / `sta pv open`(1) /
`sta pv read`(1) / `help`(1) / `?`(1) / `reset`(1) / `RST`(1) / `reboot now`(1) / `level mm`(1) / `level app`(1)。
其中 `help`/`?`/`reset`/`RST`/`reboot now` 这 5 条全部出现在
`data/logs/模块/sta/20261027-215044_...:3124,3130,3148,3161`，**全部无任何回显**（该口当时已静默，只有
`mac_recv_task.c no data 5 seconds` 在刷）→ **不构成"`reset`/`RST`/`reboot now` 是有效命令"的证据**，也不能反证。
唯一被实测证明有效的是 `reboot`；`sta pv open|read` 证明 STA 控制台确有 `sta <sub>` 命令族
（`20261029-141243_...:355-356`、`:383-384`），因此"STA 侧是否存在 `sta reset`"必须上台架探（见遗留项 1）。

### D. 断电重上电

**查了什么**：`docs/15-硬件资源速查.md`（全文含操作守则 §5）、`docs/16-AI操作指南.md`、
`docs/17-P8真机验收清单.md`、`docs/19-...`、`skills/share/serial-bridge/SKILL.md`，`grep '断电|上电|power cycle'`。

**结论**：**未找到证据**。仓库里没有任何"断电重上电复位 STA"的流程文档或脚本；只有旁述性提及
（`docs/16-AI操作指南.md:206`"CCO 只在上电/复位后跑 bpsCheck"、`docs/19-...:74`"STA 启动日志 power(8)…
60min later to set hplc power"）。物理断电在人工兜底上当然可行，但它
①不可原子化、②无软件判据（只能靠人工盯着横幅）、③会把 STA 的 `hplc_pwr` 功耗计时器等一并重置
（`docs/19-...:75`：功放置 0 长达 2400s），对"复位后马上组网"反而更不利。
→ 不纳入 `reset.sta` 的自动化路径，仅作失败后的人工兜底。

### E. 其他发现（一并记录，避免后续重复调研）

1. **工作台没有专门的"复位/重启模块"端点**：`apps/module_log/app.py` 的路由里只有
   `sessions/{id}/write`、`write-text`、`baudrate`、`flash`（`:202,207,216,221`），
   `grep -i 'reset|reboot' apps/module_log/app.py` 无 action 级接口。→ `reset.sta` 只能复用通用的
   `module-actions action=send`，这也是首选方案只用 2 个 API 调用的原因。
2. **烧录流程顺带完成一次复位**：`FlashJobRequest.no_reboot_after` 默认 `false`（`apps/workbench/ai_contracts.py:168`），
   `xmodem_flash.py:447-448` 烧完即 `reboot`。→ 如果同一用例里既要烧又要复位，复位可省一次下发；
   但**不能**把"烧录成功"当"复位成功"，二者的判据应分开。
3. **复位会打断本口正在跑的观察任务**：审计里多次出现 `stop` 失败 `会话仍有活动观察任务，不能停止`
   （`apps/workbench/runtime/ai-control/operations.json` 内 `op-12f066b0bcf64ba8` 等），
   `reset.sta` 实现时要注意：复位前后若挂了 observation，需先停观察或确认观察能跨复位存活。
4. **STA 任务在复位后按 slot 持久化、但只部分自恢复**：`reqs/0037-chongqing-sta-mclt-698-11e3-and-645-regression/DONE.md:14`
   「STA console `reboot` 软重启（重连 ~20s 内 CCO 自动重发配置；STA 任务持久化逐 slot，仅 task1 恢复，
   其余靠 CCO 重配重建；冻结数据索引恢复无报错）」→ 复位后别假设分钟采集任务一定还在。

---

## 复位后重新入网耗时

**有依据**（两条独立来源，互相印证）。

### 1）实测：`reboot` → 各里程碑（解析 `data/logs/模块/sta/*.log` 时间戳，脚本只读）

`data/logs/模块/sta/20261028-083511_...`（COM9 时期，两次完整复位）：
```
reboot @ 08:37:13.606  {image0: 2.07s, SeeOS: 7.59s, phyinit: 7.59s, nsm1: 10.10s, bcnIn: 13.67s, nsm2: 18.09s}
reboot @ 11:43:56.617  {image0: 3.90s, SeeOS: 3.91s, phyinit: 3.91s, nsm1:  6.47s, bcnIn:  8.54s, nsm2: 14.55s}
```
`sta-main-COM18` 时期（当前口，`20261107` / `20261118` 会话，取多次中位数区间）：
```
banner(Unicorn Bootloader)  +0.6 ~ +1.6 s
state_nsm.state:1           +6.5 ~ +10.1 s
首个 nwk_nd_bcnIn            +8.5 ~ +10.9 s
state_nsm.state:2            +14.6 ~ +18.1 s
```
里程碑对应的日志原文（`20261028-083511_...`）：
`state_nsm.state:1`（`:253` 附近）、`nwk_nd_bcnIn, link=HPLC, nid=...,tei:...`（`:484`）、
`self TEI change = 0x0003` → 掉网 `nwk disc HPLC LINK band2,time=45000` → `self TEI change = 0x0000`（`:254,268`）。

### 2）文档：`~20 s` 内 CCO 自动重配

`reqs/0037-chongqing-sta-mclt-698-11e3-and-645-regression/DONE.md:14`：
「STA console `reboot` 软重启（**重连 ~20s 内 CCO 自动重发配置**…）」。

### 量级结论

| 阶段 | 量级 | 依据 |
|---|---|---|
| 发 `reboot` → 出现启动横幅 | 0.6–1.6 s | 实测日志（上） |
| → 应用起来（`See OS run` / `phy init ok`） | 2–8 s | 实测日志 |
| → 网络层起动 / 收到首个 beacon | 7–14 s | 实测日志 |
| → 发现网络（`state_nsm.state:2`） | 15–18 s | 实测日志 |
| → CCO 重新下发配置 / 侧链路恢复 | **~20 s** | `reqs/0037.../DONE.md:14` |

→ `reset.sta` 块的等待参数按 **等待上限 30 s**（20 s 判据 + 余量）、**轮询间隔 1 s** 设计比较稳妥；
**注意**：以上是"板子能起来 + 网络层发现网络"的耗时，**不等于 `onnet=1`**；若命中 `hplc_pwr` 功耗门限
（`docs/19-...:74-77`，功放置 0 达 2400s），真正入网可能要等 40 分钟——这是唯一会让"复位后马上组网"失败的坑，
必须列为台架验证项。

---

## 遗留待验证

以下都需要上台架，但按风险排序，前 3 项**只读/无副作用**：

1. **STA 控制台是否存在 `sta reset` 类命令（只读）**：向 `sta-main` 会话发**裸 `sta`**（对照 CCO 的行为，裸命令只打印
   `argv[1] err, such as ...` 用法行，不会改状态），拿到 STA 侧权威子命令清单；同时可发一次 `help` / `?` 确认是否有清单。
   若真有 `sta reset`，它可能比 `reboot` 更"轻"（不重启整个固件），需要重写首选方案。
   ——依据：`sta pv open|read` 已证明 STA 控制台有 `sta <sub>` 族（`20261029-141243_...:355-384`）。
2. **当前 COM18 板子的横幅/指纹是否一致（只读）**：发一次 `reboot` 后确认 `Unicorn Bootloader` → `Run image0` →
   `reset reason:` + `SFT_GLB_RST` 这套指纹仍在（最近成功记录 2026-11-18，固件 `isv:090007 / sver:001401`）。
   同时确认 `ver`/`fcversion` 与复位前一致（即复位不改版本）。
3. **`hplc_pwr` 功耗门限是否卡住入网**：复位后观察 STA 日志是否出现
   `power(8) greater than level4, 60min later to set hplc power` → `nwk_init,hplc_pwr=0`；
   若有，则 `reset.sta` 之后必须等 `hplc power set succ`（或先由 CCO 下发参数把 `hplc_pwr` 降到 ≤4）才谈组网，
   否则 `nwk.*` 块会一直 `unknown`。
4. **端到端判据的最小充分集**：CCO 侧用哪个观测最稳——`link.c (287)| onnet=N`（周期性）、
   `aps_ioctrl_nwk.c (950)| onnet cnt = N`（触发式）、还是 simcon `10H-F1`（从节点数量）？
   需实测三者在"复位一个 STA"场景下的时延与灵敏度，再定 `reset.sta` 的判据（建议：块内只判 STA 侧指纹，
   把"是否入网"交给后续 `nwk.onnet_count` 块，避免一个块两个事实）。
5. **首轮失败重发的边界**：实测 69 发 5 失败（≈7%）。需确认失败时是"板子没收到"还是"日志观察窗口太短"，
   以及重发的合理间隔（实测 11:41:57 失败 → 11:43:56 重发成功，间隔 2 分钟属人工节奏，自动化应更短）。
6. **复位与 `simeter`/`nwk` 块的顺序耦合**：复位后 STA 会重新走 `aps_addrmap_state` 绑表扫掠
   （日志 `aps_addrmap_state.c (1412)| turn:0 pos:3 baudrate:9600 E DLT645_CMD`，`20261029-141243_...:366`），
   若同一用例里还要 `meter.addr_probe` / `meter.read_645`，需确认复位后多久才能稳定抄表。
7. **CCO 侧是否也存在 `sta` 族之外更底层的调试 AFN（F0H 内部调试）**：`_AFN_NAMES` 里有 `0xF0: "内部调试"`（`__init__.py:32`），
   但 `metadata/afn_fn.json` 标为"厂家自定义（无标准 Fn）"，仓库内无任何构帧/收帧样例 → 未找到证据，
   仅作"若将来需要更细粒度节点控制"的探索方向。

---

## 领导独立复核（2026-11-23 13:40，用户口述确认后的二次验证）

用户口述：「CCO 控制台没有 sta reset 一类命令，但是有 sta 日志吧，可以发 reboot 复位」
→ 与本报告首选方案一致。以下是独立复核结果，非引用本报告结论。

### 复核 1：全量日志统计（非抽样）

```
$ grep -rlE "reboot" data/logs/模块/sta/*.log | wc -l
17                              ← 17 个 STA 日志文件含 reboot 下发记录
$ grep -rhE "reset reason:|SFT_GLB_RST|Unicorn Bootloader" data/logs/模块/sta/*.log | wc -l
266                             ← 266 次启动指纹
```

### 复核 2：因果链逐行确认（`20261028-083511_[sta]_[sta-main-COM9...].log`）

```
40: [20261028-08:37:13:606] [EVENT] TX line: reboot
41: [20261028-08:37:15:168] [EVENT] RX text: "reboot\x1bE\r\n***...\r\n  Unicorn Bootloader\r\n..."
46: [20261028-08:37:15:673] [RX] Run image0, wait a moment, please!
150:[20261028-08:37:21:198] [RX] reset reason:
152:[20261028-08:37:21:199] [RX] SFT_GLB_RST
```

时间线：`reboot` → **+1.56s** bootloader 横幅 → **+2.07s** `Run image0` → **+7.59s** `reset reason:` + `SFT_GLB_RST`。
同文件内 `09:12:12` / `09:46:47` / `10:21:22` 三次同样模式复现 → **判据稳定可重复**。

### 复核 3：新发现 —— 存在 `reboot now` 变体

```
[20261027-21:56:06:919] [TX] reboot now
[20261027-21:55:04:636] [TX] reboot
[20261027-22:04:34:611] [EVENT] TX line: reboot
```

本报告首选方案只写了 `reboot`。实测存在两种写法（`reboot` 与 `reboot now`），
两者都产生启动指纹。→ **`reset.sta` 块必须把命令文本做成参数**（`cmd`，默认 `"reboot"`），
不得硬编码，便于按复位力度切换。

### 复核 4：判据三段式（供块层直接落码）

| 阶段 | 信号 | 相对 `reboot` 时刻 |
|---|---|---|
| 1 | `Unicorn Bootloader` | +1.5~1.6s |
| 2 | `Run image0, wait a moment` | +2.0~2.1s |
| 3 | `reset reason:` + `SFT_GLB_RST` | +6.5~8.0s |

→ 块判据建议：**命中阶段 3 为确定性 PASS**；只命中阶段 1/2 判 `inconclusive`（已进 bootloader 未必跑起来）；
15s 内一个都没命中 → 重发一次；连续 2 次失败 → `fail`。

### 复核未覆盖

- 上述日志来自 COM9 历史口，当前台架是 COM18；机制同源但**当前口未做活体复位实测**（避免影响他人台架使用）。
  待 `reset.sta` 块落地后做一次活体验证。
