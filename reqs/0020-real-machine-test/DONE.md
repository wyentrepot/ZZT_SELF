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
