# tools — 运维工具集

| 子目录 | 用途 | 文档 |
| --- | --- | --- |
| `tools/scripts/` | 运维脚本：WSL 环境部署（串口映射 + 解析网关）、AI 密钥、样本提取、打包冒烟 | [tools/scripts/README.md](scripts/README.md) |
| `tools/packaging/` | Windows 打包（PyInstaller spec、build_exe.bat、运行时 hooks、E-SafeNet 规避） | [tools/packaging/README.md](packaging/README.md) |
| `tools/taiti/高频采集` | 高频采集失败分析工具集（CCO / 侦听台 / 台体） | [tools/taiti/高频采集/README.md](taiti/高频采集/README.md) |

## 真机环境部署（最重要）

桌面入口 **`wsl环境部署.bat`**（源：`tools/scripts/wsl环境部署.bat`）：

- **串口**：把 Windows 物理串口经 usbipd 挂载到 WSL（`/dev/ttyUSB*`），用完恢复回 Windows；
- **解析网关**（REQS-0019）：一键启动 / 停止 Windows 侧解析服务（net48 DLL，端口 8700），
  供 WSL 侦听台深度解析（`parse_backend=remote`）。

详见 [tools/scripts/README.md](scripts/README.md)。
