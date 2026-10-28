# tools/scripts — 运维脚本

## WSL 环境部署（Python 串口桥 + usbipd + 解析网关）

**入口**：`wsl环境部署.bat`（双击启动）+ `uart-map.ps1`（主脚本，UTF-8 BOM，中文界面）。

**默认串口方案是 Python 桥**：Windows COM ↔ TCP ↔ WSL `/run/hplc/serial/<id>`，
不需要管理员、不再依赖 usbipd；菜单 `[6]` 一键启停（停止即释放 COM 给 Windows 工具）。
`[1]/[2]` 保留 usbipd 旧方案；`[4]/[5]` 控制 Windows 侧**解析网关**（REQS-0019）。

> **不用 usbipd 时不需要关 Hyper-V 防火墙**：Python 桥走 Windows→WSL 的 TCP
> localhost，菜单 [6]/[7] 不调用 usbipd、不检查防火墙；[3] 的 usbipd 视图带 3 秒
> 超时，usbipd 因防火墙卡住时只会提示并跳过，不会卡死菜单。前提是 COM 在 Windows
> 侧可用（设备为 `Shared`/未 `Attached`）。

### 菜单

| 选项 | 功能 |
| --- | --- |
| [1] | 映射串口到 WSL（attach + 加载驱动） |
| [2] | 恢复串口到 Windows（detach） |
| [3] | 查看状态（Python 桥 + usbipd + 解析网关 `/health`） |
| [4] | 启动解析网关（Windows 侧，端口 8700） |
| [5] | 停止解析网关 |
| [6] | **Python 串口桥开关**：启动 WSL supervisor + Windows `pythonw` 桥；再按一次停止，释放 COM |
| [7] | 查看 Python 串口桥状态（Windows 侧 PID / WSL 侧 PID / 每口 data/control/baud） |
| [Q] | 退出 |

### 非交互调用（自动化 / 脚本）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action start-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action stop-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action map      # 映射串口（usbipd）
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action restore  # 恢复串口（usbipd）
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-toggle  # Python 桥开关
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-start   # 启动桥
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-stop    # 停止桥（释放 COM）
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action bridge-status  # 桥状态
```

### 依赖

- **usbipd-win**（`winget install usbipd`；服务需运行）
- **WSL2** 发行版（脚本默认 `Ubuntu-22.04`，可改顶部 `$script:WslDist`）
- **解析网关**：`D:\019-wy-tool\ZZT_SELF\.build_plain`（明文区，规避 E-SafeNet）+
  主工作区 `.venv`（含 fastapi/uvicorn/httpx/pythonnet）

### 串口对应速查（usbipd 旧方案实测）

> Python 桥的端口/波特率以 `config/serial_bridge.json` 为准，WSL 路径为
> `/run/hplc/serial/<id>`（simcon/cco-main/sta-main/listener/factory）。
> 下表仅用于 `[1]` usbipd 分支排查。

| Windows COM | 芯片 | WSL 设备 |
| --- | --- | --- |
| COM3 | CH343 (1a86:55d3) | `/dev/ttyACM0`（需 cdc_acm） |
| COM4 / COM24 | CP210x (10c4:ea60) | `/dev/ttyUSB0` / `/dev/ttyUSB1` |
| COM23 | CH340 (1a86:7523) | `/dev/ttyUSB2` |

> 映射后 Windows 侧对应 COM 被独占，WSL 侧使用完毕用菜单 `[2]` 恢复。

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
Copy-Item wsl环境部署.bat, uart-map.ps1 "$env:USERPROFILE\Desktop\"
```
