# tools/scripts — 运维脚本

## 宿主控制（串口桥 / 解析网关 / 海思诊断服务）

> 2026-11-17 迁移：`uart-map.ps1` 与新的控制面服务都在 **`tools/win_host/`**
> （`tools/scripts/uart-map.ps1` 已删除，避免两份脚本漂移）。
> 本目录的 `wsl环境部署.bat` 已改为调用 `..\win_host\uart-map.ps1`。

| 入口 | 说明 |
|---|---|
| `tools/win_host/uart-map.ps1` | **人用的**交互菜单（UTF-8 BOM，中文） |
| `tools/win_host/wsl-control.py` + `dashboard.html` | 常驻控制面（8766）+ 看板，计划任务 `FC-Wsl-Control` 拉起 |
| `wsl环境部署.bat` | 双击入口（转调 `..\win_host\uart-map.ps1`） |

**usbipd 已移除**：串口统一走 Python 桥（Windows COM ↔ TCP ↔ WSL `/run/hplc/serial/<id>`），
不再 attach 到 WSL；HTTP 侧 `/api/usbipd/*` 返回 410。详见 `tools/win_host/README.md`
与 `tools/win_host/WSL-CONTROL-API.md`。

### 菜单（`tools/win_host/uart-map.ps1`）

| 选项 | 功能 |
| --- | --- |
| [1] | 查看宿主状态（系统串口 + 桥 + 解析网关 + 诊断服务） |
| [2] | 启动串口桥（Windows 桥接管 COM） |
| [3] | 停止串口桥（释放 COM 给 Windows 工具） |
| [4] | 重启串口桥 |
| [5] | 启动解析网关（8700） |
| [6] | 停止解析网关 |
| [7] | 启动诊断服务（`FC-Diag-Service`，8765） |
| [8] | 停止诊断服务 |
| [9] | 重启诊断服务 |
| [0] | 查看诊断服务状态 |
| [Q] | 退出 |

### 非交互调用（自动化 / 脚本）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action start-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action stop-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action host-info  # COM 列表/桥配置/工作台探活
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-toggle  # Python 桥开关
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-start   # 启动桥
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-stop    # 停止桥（释放 COM）
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-status  # 桥状态
```

### 依赖

- **WSL2** 发行版（脚本默认 `Ubuntu-22.04`，可改顶部 `$script:WslDist`）
- Windows Python（桥的 Windows 半边：`pythonw.exe` / `pyw.exe`）
- **解析网关**：`D:\019-wy-tool\ZZT_SELF\.build_plain`（明文区，规避 E-SafeNet）+
  主工作区 `.venv`（含 fastapi/uvicorn/httpx/pythonnet）

### 串口对应速查

端口/波特率一律以 `config/serial_bridge.json` 为准，WSL 路径 `/run/hplc/serial/<id>`。
现场口径见 `tools/win_host/VERIFY-20261117.md`（2026-11-17：COM18=非降本 STA、
COM28/29=海思业务/管理、COM8=海思诊断/升级、COM12=CCO、COM27=侦听台、COM20=模拟表）。

## 解析网关说明（REQS-0019）

- 网关 = Windows 侧**纯解析服务**（`apps/parser_service`，net48 `GwHPLCAnalysis.dll`，
  绑定 `0.0.0.0:8700`），只做深度解析，不碰串口、不采集。
- WSL 侦听台经 `http://<WSL网关IP>:8700` 委托解析（地址见 `config/remote_parse.json`
  或环境变量 `HPLC_REMOTE_PARSE_URL`）；`parse_backend` 三档：`local` / `remote` / `none`。
- **防火墙放行**（管理员 PowerShell，只需一次）：
  `New-NetFirewallRule -DisplayName "HPLC_Parse_Service" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8700`
- WSL 侧查档位：`GET http://127.0.0.1:8765/api/version` → `parse_backend`。
- 独立启动解析服务（不走部署菜单）：双击 `apps/parser_service/启动解析服务.bat`。

## 其他脚本

| 脚本 | 用途 |
| --- | --- |
| `gen_ai_key.ps1` / `一键生成AI密钥.bat` | 生成并安装 AI 管理密钥到用户环境变量 `WORKBENCH_AI_ADMIN_KEY` |
| `factory_test.py` | 模拟厂测上位机：A5 帧解析/构帧、进入厂测、读写 RF/HPLC 参数、RF/HPLC 点名（默认 COM7 PTY，9600 8N1） |
| `compare_hplc_parser_runtimes.py` | Windows net48 与 WSL net8 解析一致性对比（golden） |
| `extract_log_sample.ps1` / `extract_concurrent_sample.py` / `build_all_frames_txt.py` | 日志 / 并发样本 / 全帧文本提取 |
| `verify_concurrent_sample.py` | 并发抄表样本验证 |
| `smoke_test_packaged.py` / `smoke_test_workbench_packaged.py` | 打包产物冒烟测试 |
| `start_module_log.sh` | WSL 启动 module_log |
| `check_tkinter.py` / `check_module_serial_ids.js` / `check_module_serial_bind.js` / `analyze_oad_coverage.py` | 环境与数据检查 |

## 部署到桌面

```powershell
# 控制面三件套（wsl-control.py / dashboard.html / uart-map.ps1）都在 tools/win_host
Copy-Item ..\win_host\wsl-control.py, ..\win_host\dashboard.html, ..\win_host\uart-map.ps1 "$env:USERPROFILE\Desktop\"
Copy-Item wsl环境部署.bat "$env:USERPROFILE\Desktop\"
```
