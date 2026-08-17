# Windows ↔ WSL 串口映射工具

将 Windows 物理串口（USB 转串口）通过 usbipd 挂载到 WSL2，并在 WSL 内加载串口驱动，
使 WSL 中的程序（sim_concentrator / module_log 等）可直接访问 `/dev/ttyUSB*`、`/dev/ttyACM*`。

## 文件

- `串口映射.bat` — 双击启动器（ASCII，无编码问题）
- `uart-map.ps1` — 主脚本（UTF-8 BOM，中文界面）

## 依赖

- **usbipd-win**（`winget install usbipd`；服务需运行）
- **WSL2** 发行版（脚本默认 `Ubuntu-22.04`，可改顶部 `$script:WslDist`）
- WSL 内核自带 usbip 客户端 + ch341/cp210x/cdc_acm 串口驱动模块

## 使用

双击 `串口映射.bat` → 弹出菜单：

1. **映射串口到 WSL**：attach 全部共享 COM → WSL 内 `modprobe ch341 cp210x cdc_acm ftdi_sio pl2303` → 验证 `/dev/tty*`
2. **恢复串口到 Windows**：detach 全部，COM 回到 Windows
3. **查看当前状态**：列出 USB 串口与映射状态

## 串口对应速查（实测）

| Windows COM | 芯片 | WSL 设备 |
|---|---|---|
| COM3 | CH343 (1a86:55d3) | `/dev/ttyACM0`（需 cdc_acm） |
| COM4 / COM24 | CP210x (10c4:ea60) | `/dev/ttyUSB0` / `/dev/ttyUSB1` |
| COM23 | CH340 (1a86:7523) | `/dev/ttyUSB2` |

> 注：映射后 Windows 侧对应 COM 被独占，WSL 侧使用完毕用菜单 `[2]` 恢复。

## 部署到桌面

```powershell
Copy-Item 串口映射.bat, uart-map.ps1 "$env:USERPROFILE\Desktop\"
```
