# sim_meter — 模拟电表（DL/T 645 从站）

补齐工程"有模拟集中器（`libs/sim_concentrator`）、有侦听台（`apps/listener`）、
没有模拟电表"的缺口：在 RS-485 串口上以从站身份应答主站（集中器 / CCO /
抄表工具）的 645 帧。**默认 DL/T 645-2007（07 版），可切 1997（97 版）**。

帧构造 / 切帧复用 `parser_lib.adapters.adapter_645`（build_frame /
build_frame_escaped / try_extract），构帧与工程解析口径一致。

## 能力一览

| 能力 | 645-2007（默认） | 645-1997 | 说明 |
| --- | --- | --- | --- |
| 应答地址 | `13H` 广播读（FE×6）→ `93H`；`11H` 读 DI `04000401H`（CCO 固件读表地址口径）；`15H` 写地址 | `01H` 读 DI `C032H`（固件线上 `65 F3`）；`06H` 写地址 | 地址变更后旧地址立即失效 |
| 查询时间 | `11H` 读 `04000101H` 日期及周次（YYMMDDWW）/ `04000102H` 时间（hhmmss）；`08H` 广播校时（99×6，不应答）；`14H` 写时间 | `01H` 同 DI 口径；`05H` 广播校时 | 表内时钟走动，校时后按偏移计时 |
| 抄表 | `11H` 读 `00010000H` 正向有功总电能、费率 1~4、电压 / 电流 / 瞬时功率、重庆分钟采集别名（`00000001H` 电能、`04000100H` 功率） | `01H` 读 DI `9010H` 有功总（固件线上 `43 C3`） | 数据集可经接口改值 |
| 波特率 | `17H` 更改通信速率（速率特征字单 bit：b0=600 … b5=9600, b6=19200, b7=38400），应答后串口热切换；兼容知识库口径 `09H` | `07H` | 接口层亦可直接切换 |
| 重庆即装即采 | 设备类型（4036 取值：0x10 表 / 0x11 物联表 / 0x12 导轨表 / 0x13 直流表 / 0x26 / 0x27 / 0x28 量测开关 / 0x29 分布式电源）+ 设备描述符可配置，生成 STA 上报（事件类型 51）与 1376.2 AFN=06H F7 两种上送报文 | — | 类型切换时描述符联动预设 |

异常应答 `DxH`（ERR：01 其他错误 / 02 无请求数据）；CS 错、非本表地址不应答；
`12H` 读后续不支持（`D2H`+ERR02）。知识库 645 卡把"更改通信速率"记在 `09H`
（国标为 `17H`、`17H` 记为电表清零，与国标有偏差），本模块按国标实现并把
`09H` 作为兼容别名。

## 快速开始

```bash
# 无串口演示（内置请求帧 → 应答帧，覆盖核心能力）
PYTHONPATH=libs python -m sim_meter demo

# RS-485 从站常驻（默认 2400/E/8/1，645-07；--api 同时起 REST）
PYTHONPATH=libs python -m sim_meter serve --port COM5 \
    --addr 000000000001 --baud 2400 --jzjc-type 0x10 --api 8782

# 仅 REST API（配 POST /handle_hex 可无硬件调测）
PYTHONPATH=libs python -m sim_meter api --port 8782

# 列出本机串口
PYTHONPATH=libs python -m sim_meter ports
```

## REST API（默认前缀 /api/simeter）

| 接口 | 说明 |
| --- | --- |
| `GET  /status` | 电表脑 + 串口 + 会话状态（地址 / 版本 / 波特率） |
| `GET  /ports` | 本机串口列表 |
| `POST /open` `{port, baudrate?, parity?…}` | 打开串口（支持 COMx、/dev/ttyUSB\*、loop://） |
| `POST /close` | 关闭串口 |
| `POST /baud` `{baudrate}` | **波特率切换**（打开时热切换：先回帧再重开） |
| `GET/POST /config` | 地址、**645 版本（07/97，默认 07）**、串口参数 |
| `GET/POST /data` | 抄表数据集查询 / 改值（如把 00010000 电能改数） |
| `POST /clock` `{time}` | 设置表内时钟 |
| `GET/POST /jzjc` | **重庆即装即采配置**：device_type（"0x10"…）、descriptor、protocol_type（00H/02H/03H）、下挂设备 |
| `POST /jzjc/report` `{style: sta\|afn06f7, send?}` | 生成（可选发送）即装即采上送报文 hex |
| `POST /handle_hex` `{hex}` | 无串口调测：喂主站请求帧 → 返回应答帧 + 说明 |
| `POST /send_hex` `{hex}` | 串口打开时手动注入原始帧 |
| `GET  /frames` | 会话帧日志（dir=rx/tx/event、after_seq、limit） |

会话帧日志逐条落盘 `data/logs/simeter/sm-*.jsonl`。

### 即装即采报文结构（生成口径，出处：知识库 协议层/100、102 卡）

- `sta`（Q/GDW 11612.43 STA→CCO 事件主动上报数据子域）：
  `事件类型51(1B) + 设备地址(6B BCD) + 协议类型(1B) + 描述符(1+N) + 下挂数量M + 记录×M`
- `afn06f7`（Q/GDW 1376.2 AFN=06H F7 上行数据单元）：
  `从节点数n(1B) + 地址6B + 协议1B + 序号2B + 描述符(1+N) + M(1B) + m(1B) + 记录×M`
- 记录：`地址长度N1 + 地址N1B + 描述符长度N2 + 描述符N2B + 协议类型1B`
- 设备类型 4036 为半字节拼接口径（大类×0x10+小类）；描述符按前 3~4 字符
  识别（DDZY/DTZY/DTSU/DJS/LKT/DFDL/EVBC/EVCC）。

## 测试

```bash
.venv/bin/python -m pytest libs/sim_meter/ -q     # 53 passed（含 loop:// 回环串口用例）
```

## 模块结构

| 文件 | 职责 |
| --- | --- |
| `meter.py` | 核心脑：645 应答引擎 + 数据集 + 即装即采状态（不碰串口，`handle_frame` 永不抛异常） |
| `serial_channel.py` | 从站串口通道：读线程切帧应答、波特率热切换（读线程跨重开存活）、loop:// 支持 |
| `journal.py` | 会话帧日志（内存镜像 + JSONL 落盘） |
| `api.py` | FastAPI 子应用 `create_simeter_app`（可独立跑或挂载侦听台） |
| `cli.py` / `__main__.py` | `serve` / `api` / `ports` / `demo` 子命令 |
