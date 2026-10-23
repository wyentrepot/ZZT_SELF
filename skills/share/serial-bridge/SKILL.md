---
name: serial-bridge
description: '**本仓库所有串口操作的默认方案**（读日志、发 1376.2 帧、烧录、工作台会话）：把 Windows COM 用纯 Python serial-to-TCP 桥成 WSL 的 /run/hplc/serial/<id>，不再用 usbipd、不在 WSL 枚举 /dev/ttyACM*|ttyUSB*。覆盖 WSL PTY supervisor 启停、工作台映射切换、以及用 /api/serial-bridge/status|verify 验通道。Channel-only: one owner per port, fixed physical parity (PTY 侧恒 N), app-driven 9600/115200 baud switching, no lease/preemption.'
metadata:
  author: reasonix
  version: "1.1.0"
  applies-to: /01-workfile-ai/01-zzt/ZZT_SELF
policy:
  allow_implicit_invocation: false
---

# Windows → WSL 串口桥（serial-bridge）

用纯 Python 把 Windows 的 `COMx` 桥成 WSL 下的稳定 PTY 路径
`/run/hplc/serial/<id>`，不需要 usbipd、不需要管理员。Windows 侧
`serial_bridge.py windows` 用 pyserial 打开 COM，连到 WSL 的
`127.0.0.1:<tcp_port>`；WSL 侧 `serial_bridge.py wsl` 建 PTY、监听 TCP、
维护软链和状态文件。工作台通过 `/api/serial-bridge/*` 做只读状态和
“能不能正常打开”的验证。

## 默认约定（AI 必读，2026-10-22 起）

**串口一律走本桥，不再执行 usbipd、不再在 WSL 里枚举/查找 `/dev/ttyACM*`、`/dev/ttyUSB*`。**
旧 handoff 里"串口映射 / ttyACM 卡死 / 找不到口"一类描述都属于 usbipd 旧方案，遇到时直接改用桥，
不要 detach/attach、不要换设备号试错。

- 应用侧只用软链：`/run/hplc/serial/<id>`。
- **用之前先验活**：`python3 skills/share/serial-bridge/scripts/verify_bridge.py`
  （要求 `ok=true` 且目标口 `workbench_ready=true`）；失败先按 `ports[].errors` 报层，不要先开工作台会话。
- 端口认领表（按现场接线改 `config/serial_bridge.json`）：

| id | 用途 | Windows COM | 工作台 mapping_id | PTY 打开参数 |
|---|---|---|---|---|
| `simcon` | 模拟集中器 ↔ CCO 本地总线（1376.2） | COM4 | `simcon` | 9600 `parity=N`（真实 8E1 在 Windows 侧） |
| `cco-main` | CCO 日志 / shell | COM9 | `cco-main` | 115200 8N1 |
| `sta-main` | STA 日志 / shell | COM8 | `sta-main` | 115200 8N1 |
| `listener` | 侦听台 / STA 底座口 | COM24 | `listener` | 115200 `parity=N` |

- **校验位只在 Windows 侧生效**：WSL / 工作台 / pyserial 一律 `parity="N"`；用 E 打开会直接
  `Invalid argument`（见故障排查）。
- **波特率**：应用在 PTY 上改 `ser.baudrate` 即可，Windows 侧自动跟随（看 `desired_baud`）。
  CCO 对外口会自适应 9600/115200，**不要**拿桥配置里的默认 115200 硬覆盖业务 baud。
- 一个物理口同时只能一个持有者：Windows 侧工具要用 → `serial_bridge.py wsl stop`，用完再 `wsl start`。

## 边界（先读，避免误用）

- **通道版，不是完整租约**：一个物理口同一时刻只能有一个持有者。要让
  Windows 工具用串口，执行 `wsl stop` 释放全部端口；用完再 `wsl start`。
  不做“用户一关工具 AI 自动抢占”的 broker。
- **PTY 不接受校验位**：WSL 侧应用（工作台、pyserial、烧录脚本）一律
  `parity="N"` 打开；真实的 E/O 校验位固定在 Windows 侧
  `config/serial_bridge.json` 的 `parity` 字段里。
- **波特率可动态切换**：WSL 应用在 PTY 上改 `ser.baudrate` 时，supervisor
  通过独立控制口通知 Windows 侧重开 COM（默认 `control_port = tcp_port+1000`），
  支持 COM4 上 CCO 的 9600↔115200 自适应。切换瞬间有几十毫秒中断，协议侧
  需要能重试/重发。
- **校验位固定**：PTY 不接受 PARENB，WSL 侧应用一律 `parity="N"`；真实
  E/O 校验位固定在 Windows 侧 `serial_bridge.json` 的 `parity` 字段。
- **无 DTR/RTS/流控**：需要硬件复位或 RTS/CTS 的场景不要用本桥。
- Windows 侧脚本要**人工复制**到 Windows（例如
  `C:\Users\Public\serial-bridge\`）。不要从 WSL 往 `/mnt/c` 写脚本，
  避免 DLP/加密把文件内容破坏。

## 拓扑

```
Windows COM4/8/9/24
        │ pyserial（固定 baud/parity）
        ▼
serial_bridge.py windows  ──TCP data 127.0.0.1:8814/8818/8819/8824──►
                          ──TCP ctrl 127.0.0.1:9814/9818/9819/9824──►
                                                          serial_bridge.py wsl
                                                                    │
                                              /run/hplc/serial/<id> ─┴─► /dev/pts/N
                                                                    │
                                                              workbench
                                                        /api/serial-bridge/*
```

## 文件

| 文件 | 用途 |
|---|---|
| `config/serial_bridge.json` | 双端共享配置：COM、TCP 端口、物理串口参数、WSL 软链目录 |
| `tools/serial_bridge/serial_bridge.py` | 单文件双端工具（Windows 子命令 / WSL 子命令） |
| `apps/workbench/serial_bridge_api.py` | `/api/serial-bridge/status`、`/verify` |
| `skills/share/serial-bridge/scripts/verify_bridge.py` | 调工作台接口的轻量客户端 |

默认端口映射（按现场接线修改 `windows_com`）：

| id | Windows COM | 数据 TCP | 控制 TCP | 物理串口参数 |
|---|---|---|---|---|
| `simcon` | COM4 | 8814 | 9814 | 115200 8E1（应用可切 9600/115200） |
| `listener` | COM24 | 8824 | 9824 | 115200 8E1 |
| `sta-main` | COM8 | 8818 | 9818 | 115200 8N1 |
| `cco-main` | COM9 | 8819 | 9819 | 115200 8N1 |

## 一次性部署

### 1. Windows 侧

0. **如果 COM 现在被 usbipd attach 到 WSL**：先恢复回 Windows，否则
   Windows 桥打不开串口。运行 `tools/scripts/wsl环境部署.bat` 选
   `[2] 恢复串口到 Windows`，或对每个设备执行
   `usbipd detach --busid <busid>`。
1. 把下面两个文件人工复制到同一个 Windows 目录，例如
   `C:\Users\Public\serial-bridge\`：
   - `tools/serial_bridge/serial_bridge.py`
   - `config/serial_bridge.json`
2. 安装 pyserial（Windows Python）：
   ```bat
   python.exe -m pip install pyserial
   ```
3. 确认 COM 号：
   ```bat
   python.exe C:\Users\Public\serial-bridge\serial_bridge.py windows --config C:\Users\Public\serial-bridge\serial_bridge.json --list-ports
   ```
4. 诊断（Windows 桥**未运行**时最准）：
   ```bat
   python.exe ...\serial_bridge.py windows --config ...\serial_bridge.json --check
   ```
   读结果：
   - `tcp_reachable=true`, `com_openable=false`：WSL 在监听，COM 被占用；
     可能是 Windows 桥正在运行，也可能是 usbipd/别的工具还占着。
   - `tcp_reachable=true`, `com_openable=true`：WSL 在监听，但 Windows 桥
     还没启动。
   - `tcp_reachable=false`, `com_openable=true`：WSL 侧没起，先看第 2 步。
   - 两项都 false：查 USB/接线/COM 号。
5. 启动（前台调试）：
   ```bat
   python.exe C:\Users\Public\serial-bridge\serial_bridge.py windows --config C:\Users\Public\serial-bridge\serial_bridge.json
   ```
   后台常驻用 `pythonw.exe` + 日志：
   ```bat
   pythonw.exe ...\serial_bridge.py --log C:\Users\Public\serial-bridge\bridge.log windows --config ...\serial_bridge.json
   ```
   也可以放进“任务计划程序”，登录时启动。

> 启动顺序：先 detach usbipd，再起 WSL 侧（第 2 步），最后起 Windows 侧。
> 反过来也能用，Windows 会每秒重连，直到 WSL 监听出现；但 usbipd 未 detach
> 时 Windows 桥永远打不开 COM。

### 2. WSL 侧

```bash
cd /01-workfile-ai/01-zzt/ZZT_SELF
python3 tools/serial_bridge/serial_bridge.py wsl start --config config/serial_bridge.json
python3 tools/serial_bridge/serial_bridge.py wsl status --config config/serial_bridge.json
```

`start` 会脱离终端常驻，日志默认写到 `data/runtime/serial_bridge.log`；
`run` 是前台模式，便于调试。成功时状态文件里每个端口有：
`link=/run/hplc/serial/<id>`、`pts=/dev/pts/N`、`listening=true`。

停止 / 释放窗口给 Windows 工具：

```bash
python3 tools/serial_bridge/serial_bridge.py wsl stop --config config/serial_bridge.json
```

`stop` 后 Windows 桥会自动断开并关闭 COM 句柄（约 1 秒），Windows 工具
即可打开串口；再次 `wsl start` 后桥会重新占用。

## 让工作台走桥

> 2026-10-22 实机验证后，本仓库的 `config/serial_ports.json` 已切到桥接路径：
> `simcon` 9600，其余 115200；原 usbipd 配置备份在
> `config/serial_ports.json.usbipd-backup-20261022.json`。

工作台读 `config/serial_ports.json`。切换某个口到桥接时，改三处：

```json
{
  "id": "listener",
  "linux_device": "/run/hplc/serial/listener",
  "windows_com": "COM24",
  "usb_busid": "",
  "parity": "N"
}
```

- `linux_device` 指向桥接软链；
- `parity` 必须是 `N`（真实校验位在 `serial_bridge.json`）；
- 清空 `usb_busid`（或先 detach usbipd），否则 usbipd attach 时
  `resolve_serial_config` 会优先打开真实 `/dev/ttyUSB*` 而不是桥；
- 改完重启工作台（串口目录在服务启动时加载）。

`simcon` 的 1376.2 物理口通常配 E 校验，示例：

```json
{"id": "simcon", "linux_device": "/run/hplc/serial/simcon", "windows_com": "COM4",
 "usb_busid": "", "parity": "N"}
```

`config/serial_bridge.json` 里 `simcon.parity` 保持 `E`，由 Windows 侧
负责真实校验；WSL 侧只看到透明字节。

## 本次实机结论（2026-10-22）

| 端口 | 结论 |
|---|---|
| COM4 | 模拟集中器 ↔ CCO 本地总线。下 1376.2 `01H-F1` 复位 CCO 后，CCO 自适应序列为 `bps:1`(9600 E) → `bps:3`(115200 N) → `bps:2`(115200 E)；simcon 在 **9600** 和 **115200 E** 都能完成 14-F2 应答并让 CCO 打印 `bpsCheck affair over`。 |
| COM9 | **CCO 日志口**：日志含 `beacon send` / `onnet=` / `bpsCheck`；工作台 module-serial 会话实测抓到 `beacon send`。 |
| COM8 | STA 日志口；本次 8 秒窗口无输出，发 `reboot` 也无回显，STA 可能未上电/静默。 |
| COM24 | 侦听台/STA 底座口；Windows 桥能正常打开，未做协议数据验证。 |

**2026-10-22 15:12 复核（CCO 修复版在板、工作台已切桥）**：四口全绿
`{summary:{total:4,enabled:4,channel_ok:4,workbench_ready:4}}`；`simcon` 当前
`desired_baud=9600`、`client_connected=true`、`control_connected=true`；`config/serial_ports.json`
四个 `linux_device` 均已指向 `/run/hplc/serial/*` 且 `parity=N`、`usb_busid` 已清空。

证据目录：`data/logs/serial_bridge_evidence_20261022/`（Windows 桥日志、
CCO bpsCheck 日志、simcon 14-F2 收发 JSONL）。

### 重新触发 CCO bpsCheck

CCO 只在上电/复位后跑 bpsCheck；`local_rx_ok=1` 后不会重跑。可用本地
1376.2 `01H-F1` 复位（CCO 回 `00H-F1`，约 7 秒后重启）：

```bash
curl -s -X POST http://127.0.0.1:8790/api/simcon/verify \
  -H 'Content-Type: application/json' -d '{
  "port":"/run/hplc/serial/simcon","baudrate":115200,"parity":"N",
  "enable_responder":false,
  "steps":[{"send":{"format":"local","afn":1,"fn":1},
            "expect":{"format":"local"},"expect_timeout":5}]
}'
```

然后用带 14-F2 应答的 verify 任务等待（注意匹配字段是十进制
`"fn":2`，不是 242）：

```json
{"port":"/run/hplc/serial/simcon","baudrate":9600,"parity":"N",
 "enable_responder":true,
 "steps":[{"recv_only":true,
           "expect":{"format":"local","afn":20,"fn":2},
           "expect_timeout":90,
           "responders":[{"match":{"format":"local","afn":20,"fn":2},
                          "reply":{"format":"local","afn":20,"fn":2,
                                   "userdata_builder":"echo"}}]}]}
```

CCO 当前落在哪个 baud，就让 simcon 以同一个 baud 打开；`verify` 会按
`serial_ports.json` 的映射 baud 同步 Windows 侧，不要用桥配置里的默认
115200 去覆盖业务 baud。

## 验证（工作台）

工作台需在 8790 运行。三个等价入口：

```bash
# 1) 只读状态
curl -s http://127.0.0.1:8790/api/serial-bridge/status | python3 -m json.tool

# 2) 逐口验证（默认会尝试用 pyserial 打开软链）
curl -s -X POST http://127.0.0.1:8790/api/serial-bridge/verify \
  -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool

# 3) 技能自带脚本（ok 时退出码 0）
python3 skills/share/serial-bridge/scripts/verify_bridge.py
python3 skills/share/serial-bridge/scripts/verify_bridge.py --status
python3 skills/share/serial-bridge/scripts/verify_bridge.py --ports listener cco-main
```

判读字段：

| 字段 | 含义 |
|---|---|
| `supervisor.running` | WSL 桥进程在跑（状态文件 pid 存活） |
| `ports[].bridge.client_connected` | Windows 侧数据通道已连上 |
| `ports[].bridge.control_connected` | Windows 侧控制通道已连上（动态波特率依赖它） |
| `ports[].bridge.desired_baud` | WSL 应用当前要求的波特率（切换后 Windows 会跟随） |
| `ports[].link_exists` / `char_device` | `/run/hplc/serial/<id>` 存在且指向字符设备 |
| `ports[].checks.open_ok` | pyserial 能以 `parity=N` 打开；`false` 时看 `open_error` |
| `ports[].channel_ok` | 桥进程 + 软链 + Windows 连接三者都成立 |
| `ports[].workbench_ready` | `channel_ok` 且 `serial_ports.json` 映射指向软链、parity=N |
| `ok` | 本次查询范围内全部 `channel_ok`（verify 时要求 `workbench_ready`） |

一次成功的最小响应特征：

```json
{
  "ok": true,
  "summary": {"enabled": 4, "channel_ok": 4, "workbench_ready": 4},
  "ports": [{"id": "listener", "channel_ok": true, "workbench_ready": true,
             "checks": {"open_ok": true}}]
}
```

## AI 使用顺序

1. `GET /api/serial-bridge/status`（或 `verify_bridge.py --status`）。
2. 若 `ok=false`：不要打开工作台串口会话；按 `ports[].errors` /
   `warnings` 报告具体是哪一层没通。
3. `POST /api/serial-bridge/verify`，确认 `ok=true` 且目标口
   `workbench_ready=true`。
4. 再走既有工作台接口：按 `mapping_id` 启动 listener / module-serial /
   simcon / simeter 会话。
5. 需要切波特率时，直接在工作台/脚本里改 `ser.baudrate`（如 simcon 以
   9600 打开、再以 115200 重开）；`desired_baud` 会自动跟随，Windows 侧
   重开 COM。
6. 结束会话后释放串口；Windows 用户要用口时执行 `wsl stop`。

## 故障排查

| 现象 | 处理 |
|---|---|
| **WSL 里找不到 `/dev/ttyACM*` / `/dev/ttyUSB*`** | **正常**：已不使用 usbipd。改用 `/run/hplc/serial/<id>`，先跑 `verify_bridge.py` |
| AI/脚本仍按 `/dev/ttyUSB0` 之类打开 | 改成桥软链路径；`config/serial_ports.json` 的 `linux_device` 必须是 `/run/hplc/serial/<id>` 且 `usb_busid` 为空 |
| `config_error: 桥配置不存在` | 确认 `--config` 路径；WSL 默认用仓库 `config/serial_bridge.json` |
| `supervisor.running=false` | `python3 tools/serial_bridge/serial_bridge.py wsl start --config ...` |
| `client_connected=false` | Windows 侧 `serial_bridge.py windows` 没起，或 host/端口不一致，或防火墙拦截 |
| `control_connected=false` | 控制口（`tcp_port+1000`）被占用/未监听；动态波特率不会生效 |
| `desired_baud` 不跟随应用 | 应用改的不是 PTY 的 termios（例如直接改 Windows 侧配置）；先确认 `ser.baudrate` 已设置 |
| Windows `--check` 显示 `tcp_reachable=false` 但 WSL 桥在跑 | Windows→WSL 的 localhost 转发不可用：确认 `.wslconfig` 未关闭 localhostForwarding，或把 `windows.host` 改成 `wsl.exe hostname -I` 返回的 WSL IP |
| Windows 弹出“找不到 '\\' 文件” | 从 WSL 的 UNC 当前目录执行 `cmd start` 导致；把脚本/配置人工复制到 Windows 本地目录后，`cd /d` 到该目录再 `start pythonw.exe`；或直接从 WSL 用 `python.exe`（会话级） |
| `link_exists=false` | WSL 桥没起；或 `link_dir` 不可写（换 `wsl.link_dir` 到有权限目录） |
| `checks.open_ok=false`, `Invalid argument` | 工作台/脚本用了 `parity="E"`；改成 `N` |
| Windows `Access denied` | COM 被 Windows 桥或别的工具占用；先 `wsl stop` 或关掉占用工具 |
| 桥重启后应用还握着旧 pts | 重启占用方会话；`wsl start` 会重建软链 |
| `python.exe` 报缺 pyserial | `python.exe -m pip install pyserial` |
| 脚本从 WSL 复制到 Windows 后乱码/加密 | 用资源管理器人工复制；不要从 WSL 写 `/mnt/c` |
| 状态 `stale=true` | 状态文件超过 10 秒未更新；`wsl status` 看进程，必要时 `wsl stop` 后重启 |

## 一键开关（推荐，已并入 WSL 环境部署菜单）

`tools/scripts/wsl环境部署.bat`（或桌面上的 `01-wsl-.bat`）现在包含：

- **[6] Python 串口桥开关**：启动 = 启动 WSL supervisor + Windows `pythonw` 桥；再按一次 =
  停止 Windows 桥，立即释放 COM 给 Windows 工具。WSL supervisor 保持运行，下次 [6] 秒接管。
- **[7] 查看 Python 串口桥状态**：Windows 侧 PID、WSL 侧 PID、每口 data/control/baud/PTY。
- [1]/[2] 仍是 usbipd 旧方案；[4]/[5] 解析网关。

非交互调用：

```powershell
powershell -File uart-map.ps1 -Action bridge-toggle
powershell -File uart-map.ps1 -Action bridge-start
powershell -File uart-map.ps1 -Action bridge-stop
powershell -File uart-map.ps1 -Action bridge-status
```

脚本默认 WSL 仓库 `/01-workfile-ai/01-zzt/ZZT_SELF`；仓库搬家时改 `uart-map.ps1` 顶部的
`$script:WslRepo`。桥本身不需要管理员；菜单因包含 usbipd 选项会请求 UAC。

> **不用 usbipd 时不需要关 Hyper-V 防火墙**：桥走 Windows→WSL 的 TCP localhost。
> [6]/[7] 不调用 usbipd；[3] 的 usbipd 视图有 3 秒超时，不会因防火墙卡死。
> 前提是 COM 在 Windows 侧可用（设备 `Shared`/未 `Attached`）。

## Windows 常驻（手工部署）

把 `serial_bridge.py` 和 `serial_bridge.json` **人工复制**到
`C:\Users\Public\serial-bridge\`，然后在 Windows 本地目录启动：

```bat
cd /d C:\Users\Public\serial-bridge
start "" pythonw.exe serial_bridge.py --log bridge.log windows --config serial_bridge.json
```

不要在 WSL 的 UNC 当前目录里执行 `cmd start`（会报“找不到 '\\' 文件”）；
从 WSL 直接运行 `python.exe ... windows` 只适合会话级调试，WSL 会话结束
后 Windows 桥可能随之退出。

## 烧录（与本桥的关系）

- **工作台模块烧录**：走 `/api/ai/v2/flash-jobs`（v1 `/flash-operations`），
  固件必须是 `iap_*.bin`，**不要用 `flash_*.bin`**；烧录不可取消，失败不自动重试。
  桥只负责透明字节；XMODEM 的动态波特率会经控制通道生效，但切换瞬间有几十毫秒
  中断，建议先用单档 baud（如 `--baud-plan 115200`）验证。
- **EDBG / `splc-flash-loop` 闭环**：EDBG 用 Windows 侧 COM 直接烧录；如果目标口
  正被本桥占用，先 `python3 tools/serial_bridge/serial_bridge.py wsl stop --config
  config/serial_bridge.json` 释放，烧完再 `wsl start`。不要让桥和 EDBG 同时抢
  同一个 COM。
- 烧录前必须确认：目标（CCO/STA）、精确 COM、固件类型/路径、本次授权；
  构建失败不得烧旧产物；串口“可枚举”不等于“属于目标板”。

## 停止与回退 usbipd

```bash
# 方式一（推荐）：菜单 [6] 停止 Windows 桥（只释放 COM，WSL supervisor 保留）
# 方式二：停整座桥（同时释放所有 WSL listener）
python3 tools/serial_bridge/serial_bridge.py wsl stop --config config/serial_bridge.json

# 回退 usbipd
# 1) 恢复 config/serial_ports.json 的 linux_device / parity / usb_busid
# 2) 重启工作台
# 3) 双击 tools/scripts/wsl环境部署.bat → [1] 映射串口到 WSL
```

## 相关文件

| 文件 | 说明 |
|---|---|
| `config/serial_bridge.json` | 桥配置（物理参数、TCP 端口、软链目录） |
| `tools/serial_bridge/serial_bridge.py` | Windows/WSL 双端子命令 |
| `tools/serial_bridge/test_serial_bridge.py` | 配置与 PTY 泵测试 |
| `apps/workbench/serial_bridge_api.py` | 状态/验证接口 |
| `docs/19-模拟电表实机联调总结-20260928.md` | 早期 socat+TCP 桥拓扑与踩坑记录 |
