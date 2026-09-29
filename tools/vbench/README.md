# vbench —— 虚拟串口台架（实机平替）

日常开发用的黑盒/白盒测试台架：用 `os.openpty` 伪终端对替代实机串口，
把被测件（`libs/sim_concentrator` 模拟集中器、`libs/sim_meter` 模拟电表）
接上模拟 CCO / 模拟 STA 的工作节奏，在纯软件环境复现实机联调链路。

> 定位（2026-09-29 与需求方对齐）：
> 1. 独立子项目，**不做** AI 工作台技能，不进 AI 能力面；
> 2. 仅用于日常开发/CI，**平替实机测试**；
> 3. **唯一数据交换通道是虚拟串口字节流**——被测件与模拟角色之间、模拟角色
>    相互之间（含 CCO↔STA 的 HPLC 跳）不共享内存队列、不直接函数调用；
>    每个角色只持有自己的串口端点，接线只发生在 bench 装配层。

## 目录结构

| 文件 | 职责 |
|---|---|
| `pair.py` | `VirtualPair`（一对伪终端）+ `link_pairs`（1:1 字节泵）+ `link_star`（星型总线泵，下行广播/上行汇聚） |
| `frames645.py` | DL/T 645-2007 构帧/解帧/切帧（自包含，零仓库依赖；CS 含两个 68 的口径） |
| `frames13762.py` | 1376.2 本地帧薄封装（复用 `sim_concentrator.frame_codec`，字节口径与被测库一致） |
| `fake_sta.py` | 模拟 STA：99×6/AA×6 广播 13H 绑表扫掠 → 单表校验 → DI 抄读 → 17H 改速率；载波转发线程 |
| `fake_cco.py` | 模拟 CCO：14H-F2 bpsCheck 时钟询问节奏、10H-F2 档案应答、11H-F1 确认、13H-F1 路由抄读中继 |
| `bench.py` | `VirtualBench` 台架装配（N 路底座口 + 本地口 + 载波星型总线）与场景动作 |
| `cli.py` | `python3 -m tools.vbench <scenario>` 场景入口（JSON 结论，退出码 0/1） |
| `tests/` | pytest：`test_meter_blackbox.py`（电表黑/白盒）、`test_simcon_virtual_cco.py`（集中器黑/白盒）、`test_e2e_bench.py`（端到端节奏） |

## 拓扑

```
模拟集中器(simcon SerialIO)      伪CCO(FakeCCO)        伪STA(FakeSTA)×N      sim_meter×N
        │ local_pair.slave          │ │                    │ │                  │
        └────── local_pair ─────────┘ └── carrier_cco ──┐  └─ carrier_sta[i] ─┐│
                                                      │                     ││
                        link_star 星型字节泵（下行广播/上行汇聚）┘─────────────────┘
                                                      │
                                           meter_pair[i].slave → sim_meter 通道
```

- 每路底座口 = 一个 `VirtualPair`：从端给 sim_meter 真实通道代码打开，主端给该路的 FakeSTA；
- HPLC 跳 = CCO 载波口与各 STA 载波口之间的星型字节泵（`link_star`）：
  CCO 下行所有 STA 都能听到（共享媒介语义），任一 STA 上行汇聚回 CCO；
- `local_pair`：从端给 simcon 的 `SerialIO`，主端给 FakeCCO。

## 用法

```bash
# 场景（开发期快速验证，退出码 0/1）
python3 -m tools.vbench bind      --meters 2          # 绑表扫掠
python3 -m tools.vbench e2e       --addrs 999999999999,013300000001
python3 -m tools.vbench blackbox  --meters 1          # 脏流鲁棒性

# 全部台架测试
python3 -m pytest tools/vbench/tests/ -q
```

### 测试中打开被测件（照抄即可）

```python
from tools.vbench.bench import VirtualBench
from tools.vbench.pair import VirtualPair
from sim_concentrator.serial_io import SerialIO          # 被测件 1：模拟集中器
from sim_meter.serial_channel import MeterSerialChannel  # 被测件 2：模拟电表

bench = VirtualBench.create(meters=1, meter_addrs=["999999999999"])
io = SerialIO(port=bench.local_pair.slave_path, baudrate=115200, parity="N",
              resource_registry=SerialResourceRegistry(), journal=None)
io.open()
# meter 通道：port=bench.meter_paths()[0]，parity 一律 "N"（见下）
bench.start(); bench.bus_warmup(io); bench.bind_all(); bench.start_relay()
...
bench.stop(); io.close()
```

## 环境注记（重要）

1. **WSL pts 不接受 PARENB**：2026-09-29 实测（6.18.33.2-microsoft-standard-WSL2），
   在 pts 上以 `parity="E"` 打开即 `EINVAL`。台架内所有开口一律 `parity="N"`；
   pts 是虚拟管道，线路奇偶不影响字节内容，与实机 8E1 口径无关。
2. **645 CS 口径**：从第一个帧起始符 68 起至 CS 前所有字节（含两个 68）模 256——
   与 DL/T 645-2007 及实机 STA 捕获帧（`6899..996813007916`）一致。
3. **1376.2 的 Fn 命名**：`"14H-F2"` 中的 F2 是 `Fn=2` 的显示法（F 前缀+十进制），
   不是 0xF2。`_norm_fn("F2")==2`，帧内 DT 线序为 `02 00`。构帧用整数 1/2。
4. **pty 方向性**：写主端→从端可读；写从端→主端可读。角色都在主端收发，
   桥接泵必须走两对的**从端** fd（`link_star`/`link_pairs` 已封装）。
5. **13H 应答的数据域不加 33H**（表地址原文）；其余读应答（91H 等）数据域 +33H。
6. **AsyncWait**：回帧经虚拟串口/泵线程有毫秒级在途时间，断言内部状态用
   `bench.wait_until(lambda: ...)` 轮询，不要读后立即断言。

## 测试发现的被测件缺陷（均已修复）

| 缺陷 | 位置 | 修复 |
|---|---|---|
| `tx_count` 只定义从不自增，状态 API 恒为 0 | `sim_meter/meter.py` | `handle_frame` 返回应答时自增 |
| 脏字节/假头（实机 STA 交替下发的 698 帧，L 字节 0xAA）让切帧器永久卡死，电表"失聪" | `sim_meter/serial_channel.py` | 通道层重同步：buf[0]≠0x68 跳到下一个 68；假头超过 `_STALL_RESYNC`(0.5s) 跳到 buf 内下一个 68 |
