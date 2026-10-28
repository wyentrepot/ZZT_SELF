# tools — 运维工具集

| 子目录 | 用途 | 文档 |
| --- | --- | --- |
| `tools/scripts/` | 运维脚本：WSL 环境部署（串口映射 + 解析网关）、AI 密钥、样本提取、打包冒烟 | [tools/scripts/README.md](scripts/README.md) |
| `tools/serial_bridge/` | 纯 Python 串口桥（Windows COM ↔ WSL PTY，无 usbipd/无管理员）；工作台 `/api/serial-bridge/*` 负责通道验证 | [tools/serial_bridge/README.md](serial_bridge/README.md) |
| `tools/packaging/` | Windows 打包（PyInstaller spec、build_exe.bat、运行时 hooks、E-SafeNet 规避） | [tools/packaging/README.md](packaging/README.md) |
| `tools/taiti/高频采集` | 高频采集失败分析工具集（CCO / 侦听台 / 台体） | [tools/taiti/高频采集/README.md](taiti/高频采集/README.md) |
| `tools/taiti/分钟采集` | 分钟采集（MCLT）离线分析工具集：11E4 主动上报 jitter/重传率、11E3 补抄延迟、两端时钟偏移、三端漏报对账、mclt_ut 校准参数（技能 mclt-collect-analysis 调用） | [tools/taiti/分钟采集/README.md](taiti/分钟采集/README.md) |
| `tools/vbench/` | 虚拟串口台架（实机平替）：openpty 伪终端对 + 模拟 CCO/STA 工作节奏，对 sim_concentrator / sim_meter 做黑盒/白盒与端到端测试；独立子项目，不做工作台技能，仅供日常开发/CI | [tools/vbench/README.md](vbench/README.md) |

## 真机环境部署（最重要）

桌面入口 **`wsl环境部署.bat`**（源：`tools/scripts/wsl环境部署.bat`）：

- **串口**：把 Windows 物理串口经 usbipd 挂载到 WSL（`/dev/ttyUSB*`），用完恢复回 Windows；
- **解析网关**（REQS-0019）：一键启动 / 停止 Windows 侧解析服务（net48 DLL，端口 8700），
  供 WSL 侦听台深度解析（`parse_backend=remote`）。

详见 [tools/scripts/README.md](scripts/README.md)。
