# tools/scripts — 运维脚本

## WSL 环境部署（串口映射 + 解析网关）

**入口**：`wsl环境部署.bat`（双击启动）+ `uart-map.ps1`（主脚本，UTF-8 BOM，中文界面）。

把 Windows 物理串口（USB 转串口）通过 usbipd 挂载到 WSL2（`/dev/ttyUSB*`），并控制
Windows 侧**解析网关**（REQS-0019：WSL 深度解析委托 Windows net48 DLL，端口 8700）。

### 菜单

| 选项 | 功能 |
| --- | --- |
| [1] | 映射串口到 WSL（attach + 加载驱动） |
| [2] | 恢复串口到 Windows（detach） |
| [3] | 查看状态（串口 + 解析网关 `/health`） |
| [4] | 启动解析网关（Windows 侧，端口 8700） |
| [5] | 停止解析网关 |
| [Q] | 退出 |

### 非交互调用（自动化 / 脚本）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action start-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action stop-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action map      # 映射串口
powershell -NoProfile -ExecutionPolicy Bypass -File uart-map.ps1 -Action restore  # 恢复串口
```

### 依赖

- **usbipd-win**（`winget install usbipd`；服务需运行）
- **WSL2** 发行版（脚本默认 `Ubuntu-22.04`，可改顶部 `$script:WslDist`）
- **解析网关**：`D:\019-wy-tool\ZZT_SELF\.build_plain`（明文区，规避 E-SafeNet）+
  主工作区 `.venv`（含 fastapi/uvicorn/httpx/pythonnet）

### 串口对应速查（实测）

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
