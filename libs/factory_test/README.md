# factory_test — 模拟厂测上位机（A5 协议）

用于在工作台里模拟产线厂测上位机：解析/构建 STA 厂测 A5 帧，进入厂测模式，读写 RF/HPLC 参数，执行 RF/HPLC 点名。

## 1. 端口

| 端口 | 角色 | 参数 |
|---|---|---|
| COM7 | 厂测上位机 / 治具通信口 | **9600 8N1** |
| COM8 | 标准模块 STA 日志 | 115200 8N1 |
| COM9 | 被测 STA 日志 | 115200 8N1 |

工作台通过串口桥把 COM7 暴露为 WSL PTY `/run/hplc/serial/factory`。已在 `config/serial_ports.json` 和 `config/serial_bridge.json` 增加 `factory` 映射；需要重新启动串口桥（Windows 侧配置也要同步更新）后 PTY 才会出现。

> 当前 `serial_ports.json` 里 COM8/COM9 沿用已有 `sta-main`/`cco-main` 条目（标签仍是“STA/CCO 模块”）。本次不改共享映射，避免影响 CCO 流程；厂测场景按上表的物理角色使用。

## 2. A5 帧格式

```
A5 | ADDR | CTRL | LEN | DI_lo | DI_hi | DATA (LEN-2) | CRC32 (4, little-endian)
```

- `ADDR`：固件不校验，工装惯例 `0xFE`。
- `CTRL`：bit7=1 响应；低 4 位 0=读、1=写。常用 `0x10` 读、`0x11` 写、`0x90` 读响应、`0x82` 写成功、`0x83` 否认。
- `LEN`：只统计 `DI + DATA`，不含 CRC；整帧长度 = `4 + LEN + 4`。
- `DI`：16 位命令 ID，**小端**。`00 01` = `0x0100`（信道），`01 00` = `0x0001`（地址）。
- 写响应 `0x82/0x83` 的 `LEN=0`，**没有 DI/DATA 字段**，只有 4 字节 CRC。

### CRC

CRC-32/MPEG-2：

| 参数 | 值 |
|---|---|
| 多项式 | `0x04C11DB7` |
| 初值 | `0x00000000` |
| 输入/输出反转 | 否 |
| 最终异或 | `0x00000000` |
| 覆盖范围 | 帧头到 DATA 末尾（CRC 前全部字节） |
| 附加字节序 | 小端 |

不能用 `zlib.crc32` 代替。

## 3. 常用命令

| DI | 名称 | 读响应 DATA | 写数据 |
|---|---|---|---|
| `0x0000` | 版本号 | 7（HV le16 + SV le24 + 2 字节残留） | — |
| `0x0001` | 地址/MAC | 8（MAC6 + 00 00） | 6 |
| `0x0004` | 重启模块 | 0x82 ACK | 无 |
| `0x0005` | 进入测试模式 | 0x82 ACK | 无 |
| `0x000E` | 内部版本号 | 8（HV le16 + FC le24 + 日/月/年 BCD） | — |
| `0x0100` | RF 测试信道 | 1 | 1（信道） |
| `0x0103` | RF 功率 | 1 | 1（有符号） |
| `0x0109` | RF 点名 | 0x82/0x83 | 1（载荷长度） |
| `0x010A` | RF 点名结果 | 5（RX RSSI, 频偏, TX RSSI, RX SNR, TX SNR） | — |
| `0x010B` | RF 选项字 | 1 | 1（option） |
| `0x010C` | RF 发包 | 0x82 ACK | `N + N 字节` |
| `0x010D/0x010E` | 长发模式启动/停止 | 0x82 ACK | 无 |
| `0x0200` | HPLC 相位 | 1 | 1（0/1/2） |
| `0x0204` | HPLC 点名结果 | 4（RX RSSI, RX SNR, TX RSSI, TX SNR） | — |
| `0x0205` | HPLC 点名 | 0x82/0x83 | 1（载荷长度） |
| `0x0206` | HPLC band | 1 | 1 |
| `0x020B` | HPLC 功率 | 1 | 1（有符号） |

完整表见 STA 固件 `misc/src/factory_test.c` 的 `fact_cmd_read_handler` / `fact_cmd_write_handler`。

## 4. 进入厂测

- STA 上电后 `fact_test_process()` 把 UART1 设为 **9600 8N1**，只开放 **500ms** 窗口；窗口内收到合法 A5 帧即进入厂测（写 `0x0005`）。
- `AT+FACTMODE` 在 STA 固件里没有实现，通常是治具/适配器侧命令；`AT+RESET` 只在 STA 正常应用模式（9600 8E1）生效，厂测模式下无效。
- 厂测模式下复位用 A5 `0x0004`，不要用 `AT+RESET`。
- 本工具的 `enter` 默认先发 `AT+FACTMODE`、`AT+RESET`，等待 `--boot-delay` 后按 100ms 间隔重试 A5 `0x0005`，直到设备 ACK；不需要 AT 前导时加 `--no-at`。

## 5. CLI

```bash
# 帧解析/构帧（不需要串口）
python3 tools/scripts/factory_test.py parse --hex "A5 FE 90 03 00 01 29 1A DA 00 3D"
python3 tools/scripts/factory_test.py build --ctrl 0x10 --di 0x0100

# 串口操作（默认 /run/hplc/serial/factory）
python3 tools/scripts/factory_test.py enter
python3 tools/scripts/factory_test.py read  --di 0x0100
python3 tools/scripts/factory_test.py write --di 0x0100 --data 32
python3 tools/scripts/factory_test.py rf-call --enter
python3 tools/scripts/factory_test.py hplc-call --enter --band 2
python3 tools/scripts/factory_test.py monitor
python3 tools/scripts/factory_test.py test --enter --rf-call

# 其他端口/波特率
python3 tools/scripts/factory_test.py read --port /dev/ttyUSB0 --baud 9600 --di 0x0100
```

`parse`、`build` 只做纯软件编解码；`read`/`write`/`enter`/`reset`/`rf-call`/`hplc-call`/`test`/`monitor` 会打开串口。输出为 JSON。

## 6. 注意事项

1. **一个物理口同一时刻只能一个持有者**：使用 COM7 前先停掉占用该口的模块日志/其他会话，否则串口桥会报占用。
2. **写响应不要等 0x91**：当前固件固定 `0x82/0x83`；`0x91` 不是本固件的写响应。
3. **DI 小端**：`00 01` 是 `0x0100`（信道），`01 00` 是 `0x0001`（地址）。
4. **波特率/校验位**：厂测 A5 是 9600 8N1；正常应用同一 UART1 是 9600 8E1。串口桥运行中只能切波特率，不能切校验位；需要两套配置或由治具切换。
5. **固件残留/健壮性**：`0x0000` 返回 7 字节但只写 5 字节；`0x0105`/`0x0108` 返回未初始化/请求残留；部分写命令未校验长度；`flen` 为 `uint8_t`，LEN 过大回绕。上位机侧按固定长度解析并做范围检查。
6. **点名结果是“上一次”的**：`0x0109`/`0x0205` 失败时不会清空 RSSI/SNR，读 `0x010A`/`0x0204` 可能拿到旧值；以写命令的 `0x82/0x83` 判断本次是否成功。
7. **标准模块改 option 的已知缺陷**：`0x010B` 写后固件不会自动重启 RX（见 reqs/0001），只改 option 时标准模块可能收不到点名；建议先发 option、再发信道，或等待固件修复。
8. **发射风险**：`0x0101`、`0x010C`、`0x010D`、`0x0201` 会实际发射；长发模式是连续波，使用前确认合规和屏蔽。

## 7. 代码结构

```
libs/factory_test/
  codec.py      # CRC、构帧、解析、流式切帧
  commands.py   # DI 常量、载荷解码、构帧快捷函数
  client.py     # 串口客户端（enter/reset/read/write/点名）
  cli.py        # argparse CLI
  test_codec.py # 现场样帧与 CRC 单测
tools/scripts/factory_test.py  # 免配置入口
```
