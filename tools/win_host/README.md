# Windows 宿主控制面（host control plane）

> 一个入口，管 Windows 侧三件事：**串口桥、解析网关 8700、海思诊断服务 8765**；
> 附带看板页面、自检、审计、逐口开关。
> 2026-11-17 起替代 v1 的“PowerShell 薄壳”（v1 每次请求 fork `powershell.exe`，单次 4–9 秒）。

## 文件

| 文件 | 作用 | 部署位置 |
|---|---|---|
| `wsl-control.py` | 控制面服务（纯标准库，Windows 上跑） | `%USERPROFILE%\Desktop\`（计划任务 `FC-Wsl-Control` 拉起） |
| `dashboard.html` | 看板页面（服务状态 / 串口表 / 人工操作 / 自检 / 审计） | 与 `wsl-control.py` 同目录 |
| `uart-map.ps1` | **人用的**交互菜单（已去掉 usbipd；不再被 HTTP 接口调用） | 同目录 |
| `wsl-control-register.ps1` / `.bat` | 注册计划任务 + 防火墙（首次/重装用） | 同目录 |
| `test_wsl_control.py` | 单元测试（WSL 可跑，不需要 Windows） | — |

## 部署（Windows）

1. 把 `wsl-control.py`、`dashboard.html`、`uart-map.ps1` 复制到桌面（与 register 脚本同目录）。
2. 计划任务已存在时：`schtasks /End /TN FC-Wsl-Control` 再 `/Run`。
   首次：管理员运行 `wsl-control-register.bat`。
3. 打开 <http://127.0.0.1:8766/> 就是看板。

> 注意：计划任务的命令是 `pythonw.exe wsl-control.py 8766`（位置参数），v2 兼容该写法；
> 也可以用 `--port 8766`。

## 访问与鉴权

- 回环（`127.0.0.1` / `::1`）免令牌；**从 WSL / 局域网访问必须带令牌**。
- 令牌文件：`%USERPROFILE%\.wsl-control\token`；WSL 侧读
  `/mnt/c/Users/<user>/.wsl-control/token`。
- 请求头：`X-Host-Token: <token>`（或 `Authorization: Bearer <token>`、`?token=`）。
- 看板：Windows 本机打开会自动注入令牌；从 WSL 打开时页面会弹一次输入框，存到 localStorage。

## 配置

`%USERPROFILE%\.wsl-control\settings.json`（首次运行自动生成）：

| 键 | 默认 | 说明 |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `8766` | 监听地址/端口 |
| `require_token` | `true` | 非回环是否要令牌 |
| `status_ttl_seconds` | `8` | 快照缓存时间（毫秒级返回） |
| `wsl.distro` / `wsl.repo` | `Ubuntu-22.04` / `/01-workfile-ai/01-zzt/ZZT_SELF` | 定位 WSL 侧桥脚本与配置 |
| `bridge.*` | 桥脚本、配置、状态文件、pid、日志 | 桥的后端路径 |
| `gateway.*` | 8700、`.build_plain`、venv python | 解析网关 |
| `diag.*` | `FC-Diag-Service`、8765 | 海思诊断服务 |

## 接口（v2）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 看板页面 |
| GET | `/health` | 存活探针（免令牌） |
| GET | `/api/v1/caps` | 能力清单（含令牌文件路径、各域动作） |
| GET | `/api/v1/status[?fresh=1][&wait=N&since=V]` | 聚合状态；`fresh` 阻塞刷新，`wait+since` 长轮询 |
| GET | `/api/v1/ports` | 逐口视图：COM、设备、归属、通道、波特率 |
| POST | `/api/v1/ports/{id}/enable\|disable` | 逐口接管/释放（改配置 + 重启 Windows 桥） |
| GET | `/api/v1/services` | 桥/网关/诊断的摘要 |
| POST | `/api/v1/services/{bridge\|gateway\|diag}/{start\|stop\|restart}` | 服务启停 |
| GET/POST | `/api/v1/doctor` | 自检：COM、桥、网关、诊断、工作台、冲突 |
| GET | `/api/v1/logs?source=bridge\|gateway-out\|gateway-err\|service&lines=N` | 日志 tail |
| GET | `/api/v1/audit?lines=N` | 操作审计（谁、何时、结果） |

兼容保留：`/api/status`、`/api/bridge/{status,start,stop,toggle,restart}`、
`/api/gateway/{start,stop}`、`/api/diag/{status,start,stop,restart}`、`/api/bridge/log`、`/api/gateway/log`。

**已移除**：`/api/usbipd/map`、`/api/usbipd/restore` → 返回 `410 GONE`
（串口 2026-10-22 起统一走 Python 桥，不再 attach 到 WSL）。

## 端口归属判定

| owner | 含义 |
|---|---|
| `serial-bridge` | 桥已连上该口（数据通道 `client_connected=true`） |
| `fc-diag` | 该 COM 正被诊断服务 8765 占用（`/api/state.port`） |
| `free` | 系统里有该 COM，但没人持有 |
| `absent` | 系统串口表里没有这个 COM（没插/驱动/被 usbipd attach 走了） |
| `disabled` | 配置里 `enabled=false`（已释放） |

`conflict=true`：同一 COM 既被桥连着、又是诊断服务的口 —— 这就是“两个域抢口”，看板会标红。

## 逐口开关的语义与代价

- `disable`：把 `config/serial_bridge.json` 里该口 `enabled` 改为 `false`（**只改这一处，保留原排版**，
  备份 `.bak-hostcontrol`），然后重启 **Windows 半边**桥；WSL 侧 PTY 软链保留。
- 代价：重启 Windows 桥会让其余口断开重连，约 5–15 秒。
- `enable`：反向操作，同样重启 Windows 桥。

## 自检（doctor）

检查项：令牌文件、桥配置可读/端口唯一/COM 重复、启用口的 COM 是否在系统里、
WSL supervisor、Windows 桥、数据通道、解析网关 8700（含 DLL）、诊断服务任务与 8765、
工作台 8790、COM 冲突。每项带 `level`（ok/warn/error/info）与 `fix` 建议。

## 冒烟 / 测试

```bash
# WSL 里跑单测（不需要 Windows）
python3 -m unittest tools.win_host.test_wsl_control

# 线上冒烟（Windows 本机）
curl -s http://127.0.0.1:8766/health
curl -s http://127.0.0.1:8766/api/v1/caps | python3 -m json.tool
```

## 已知边界

- 8790 工作台在 WSL 侧，本服务只探活 + 给链接，不负责启停。
- 8765 只绑回环：从 WSL 浏览器打不开它的页面；从 Windows 本机可以。
- 逐口开关只影响 Windows 桥；WSL 侧 supervisor 与 PTY 不重启。
- 不实现串口租约（lease）与自动抢占：跨域抢口目前靠显式 `disable`，见 README 末节“后续”。
