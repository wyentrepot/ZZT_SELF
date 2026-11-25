# WSL 控制接口对接文档（v2）

Windows 侧常驻控制服务 `wsl-control.py`（计划任务 `FC-Wsl-Control` 拉起），
把 **串口桥 / 解析网关 8700 / 海思诊断服务 8765** 的状态与开关收敛成**一个 HTTP 入口**，
并自带看板页面。前端（WSL 侧）只对接这一个地址即可。

> v2.1（2026-11-19）：串口映射增改（添加/编辑/一键释放/一键接管）+ 独立串口扫描接口，见下。
>
> v2（2026-11-17）：数据面改为纯 Python 直读/直测，不再每次 fork `uart-map.ps1`
> （v1 单次状态 4–9 秒，v2 首次约 2 秒、缓存后 10 毫秒级）；新增 `/api/v1/*`、
> 看板、逐口开关、自检、审计、令牌；**移除 usbipd 接口**。

## 架构

```
WSL 前端 / 技能 ──HTTP(+X-Host-Token)──▶ wsl-control (Windows, 0.0.0.0:8766)
                                            ├─ 串口桥（Windows 半边 serial_bridge.py）
                                            ├─ 解析网关 8700（本地子进程）
                                            └─ 海思诊断服务 8765（计划任务 FC-Diag-Service）
看板：http://127.0.0.1:8766/
```

## 基地址（WSL 内取宿主机 IP）

```bash
HOST=$(ip route show default | awk '{print $3}')   # NAT 模式
# mirrored 模式可用 localhost
TOKEN=$(tr -d '\r\n' < /mnt/c/Users/$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r')/.wsl-control/token)
```

- `localhost:8766` 在 NAT 模式下**不通**，必须用宿主 IP。
- 从 WSL / 局域网访问**必须带令牌**（回环免令牌）：
  `-H "X-Host-Token: $TOKEN"`。

## 接口一览

### 读

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针（免令牌）：`{ok, version, data:{status,config_ok}}` |
| GET | `/api/v1/caps` | 能力清单：版本、令牌文件、各域动作、端点列表 |
| GET | `/api/v1/status` | 聚合快照：`meta/host/bridge/gateway/diag/ports` |
| GET | `/api/v1/ports` | 逐口：`id,com,device,owner,enabled,com_present,bridge.*,conflict` |
| GET | `/api/v1/coms?fresh=1` | 系统串口扫描：`com,device,mapped_by,free,diag_used`；`fresh=1` 强制重扫（默认走 ≤8s 快照缓存，`cached:true` 表示来自缓存） |
| GET | `/api/v1/services` | 桥/网关/诊断摘要 |
| GET | `/api/v1/logs?source=bridge\|gateway-out\|gateway-err\|service&lines=100` | 日志 tail |
| GET | `/api/v1/audit?lines=50` | 操作审计 |

`/api/v1/status` 参数：

- `fresh=1`：阻塞刷新（约 2 秒）。
- `wait=N&since=V`：长轮询，最多 25 秒；快照版本变化或超时返回。

### 写（POST，均记审计）

| 路径 | 作用 |
|---|---|
| `/api/v1/services/bridge/{start\|stop\|restart}` | 串口桥（stop = 释放 COM 给 Windows 工具） |
| `/api/v1/services/gateway/{start\|stop\|restart}` | 解析网关 8700 |
| `/api/v1/services/diag/{start\|stop\|restart}` | 海思诊断服务 8765 |
| `/api/v1/ports/{id}/enable\|disable` | 逐口接管/释放（body `{"restart":true}`，默认重启 Windows 桥） |
| `/api/v1/ports` | **添加映射**：`{"id","windows_com", "label?","baudrate?","parity?","bytesize?","stopbits?","usage?","tcp_port?","control_port?","enabled?"}`；tcp/control 缺省自动挑空闲号（8818+/9818+）；id 重复 → `PORT_EXISTS`，COM 已被映射 → `COM_IN_USE` |
| `PATCH /api/v1/ports/{id}` | **编辑映射**：label/usage/windows_com/baudrate/bytesize/parity/stopbits/tcp_port/control_port（body `{"restart":true}`）；仅 label/usage 变化不重启桥；未知字段 → `VALIDATION`；仅改 label/usage 返回 `restart_skipped` |
| `/api/v1/ports/batch` | **一键释放/接管**：`{"action":"enable\|disable","ids":["..."]?,"restart":true}`；一次写回所有 enabled，只重启一次桥；`ids` 缺省 = 全部端口 |
| `/api/v1/doctor` | 自检（GET/POST 均可） |

### 已移除

| 路径 | 现状 |
|---|---|
| `/api/usbipd/map`、`/api/usbipd/restore` | **410 GONE**。串口统一走 Python 桥，不再 attach 到 WSL |

### 响应形状

成功：

```json
{"ok": true, "service": "wsl-control", "version": "2.1.0", "ts": "...", "data": { }}
```

失败（HTTP 码同时表达类别）：

```json
{"ok": false, "error": {"code": "PORT_NOT_FOUND", "message": "桥配置里没有端口：x", "detail": {}}}
```

| HTTP | code 示例 | 含义 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 非回环且未带令牌 |
| 400 | `VALIDATION` / `CONFIG_PARSE` | 参数不合法 / 桥配置 JSON 损坏 |
| 404 | `NOT_FOUND` / `PORT_NOT_FOUND` / `LOG_MISSING` | 路径/端口/日志不存在 |
| 405 | `METHOD_NOT_ALLOWED` | 写动作用了 GET |
| 409 | `BRIDGE_STOP_FAILED` / `GATEWAY_NOT_READY` / `DIAG_NOT_READY` / `PORT_EXISTS` / `COM_IN_USE` / `TCP_PORT_IN_USE` | 动作失败（未就绪/杀不掉/id、COM、TCP 端口冲突） |
| 410 | `GONE` | usbipd 接口已移除 |
| 500 | `INTERNAL` | 服务内部异常 |

## 状态字段（节选）

```jsonc
{
  "meta": {"version":"2.1.0","pid":1234,"age_seconds":0.1,"stale":false,"version_no":7},
  "host": {"coms":[{"com":"COM12","device":"Silicon Labs CP210x..."}],
           "workbench":{"port":8790,"reachable":true}, "python":"...pythonw.exe"},
  "bridge": {
    "windows": {"running":true,"pid":14052,"detected_by":"pid_file|netstat"},
    "config":  {"path":"\\\\wsl.localhost\\...\\serial_bridge.json","ports":[...],"error":null},
    "wsl":     {"running":true,"pid":34898,"ports":{...},"config_stale":false}
  },
  "gateway": {"port":8700,"running":false,"pid":null,"health":null},
  "diag":    {"task":"FC-Diag-Service","task_state":"Running","health_ok":true,
              "health":{"version":"0.3.1"},"state":{"port":"COM8","port_open":true}},
  "ports":   [{"id":"sta-main","com":"COM18","owner":"serial-bridge","enabled":true,
               "com_present":true,"bridge":{"client_connected":true},"conflict":false}]
}
```

## 看板

- 地址：`http://127.0.0.1:8766/`（Windows 本机，自动注入令牌）。
- 从 WSL 打开 `http://<宿主IP>:8766/` 时页面会要求粘贴一次令牌。
- 内容：服务卡片（启动/停止/重启 + 跳转链接）、串口表（归属/通道/编辑/释放/接管 +
  「一键接管」「一键释放」「＋添加串口」，一键操作有二次确认）、
  自检结果、最近操作；每 6 秒自动刷新。
- 添加/编辑弹窗的 COM 下拉来自 `/api/v1/coms?fresh=1`（已被映射的口在候选里标出），
  支持手输（设备暂时没插时）；编辑占用中的口会红字提示会断开重连。

## 服务管理（Windows 侧，管理员）

```powershell
schtasks /Run   /TN FC-Wsl-Control
schtasks /End   /TN FC-Wsl-Control
schtasks /Query /TN FC-Wsl-Control
```

- 日志：`%USERPROFILE%\.wsl-control\service.log`（2MB × 3 滚动）
- 审计：`%USERPROFILE%\.wsl-control\audit.jsonl`
- 配置：`%USERPROFILE%\.wsl-control\settings.json`
- 令牌：`%USERPROFILE%\.wsl-control\token`

## 迁移提示（v1 → v2）

1. 调用方需带 `X-Host-Token`（非回环）。
2. `/api/status` 的 `data.usbipd` 字段已删除；改用 `data.ports` 与 `data.host.coms`。
3. `/api/usbipd/*` 返回 410；串口请走 `/api/v1/services/bridge/*` 与 `/api/v1/ports`。
4. 逐口开关会重启 **Windows 半边**桥（5–15 秒），WSL 侧 PTY 不中断。
