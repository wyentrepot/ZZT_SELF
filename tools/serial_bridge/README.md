# serial_bridge — Windows COM ↔ WSL PTY（纯 Python）

不依赖 usbipd、不需要管理员。Windows 侧用 pyserial 打开 COM，连到 WSL
`127.0.0.1:<tcp_port>`；WSL 侧建 PTY，暴露稳定软链
`/run/hplc/serial/<id>`，并写状态文件供工作台验证。

## 快速开始

```bash
# WSL 侧（先起）
python3 tools/serial_bridge/serial_bridge.py wsl start --config config/serial_bridge.json
python3 tools/serial_bridge/serial_bridge.py wsl status --config config/serial_bridge.json

# Windows 侧（把 serial_bridge.py + serial_bridge.json 人工复制过去）
python.exe serial_bridge.py windows --config serial_bridge.json
```

完整部署、工作台切换、验证和故障排查见
`skills/share/serial-bridge/SKILL.md`。

## 边界

- 一个物理口同一时刻只有一个持有者；Windows 工具要用时先 `wsl stop`。
- PTY 不接受 PARENB：WSL 侧应用一律 `parity="N"`，真实校验位在 Windows 侧。
- 波特率可由 WSL 应用动态切换（控制口 `tcp_port+1000`），支持 COM4 上
  CCO 的 9600↔115200；切换瞬间有几十毫秒中断。
- 校验位固定：PTY 侧 `N`，真实校验在 Windows 侧。不支持 DTR/RTS。
- 只做通道，不做自动租约/抢占。
