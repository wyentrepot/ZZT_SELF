# WSL 开发环境使用手册

> 配套设计文档：`docs/需求设计方案/2026-08-13-wsl-dev-split-design.md`
> 核心思路：**开发/测试/Web 在 WSL（ext4 明文），串口/桌面/打包在 Windows**，两侧用 git 远程仓库同步。

## 环境概览

| 项目 | 值 |
|---|---|
| WSL 发行版 | Ubuntu-22.04（WSL2） |
| WSL 工作区 | `/01-workfile-ai/01-zzt/ZZT_SELF` |
| Windows 工作区 | `D:\019-wy-tool\ZZT_SELF` |
| git 远程 | `git@github.com:wyentrepot/ZZT_SELF.git` |
| WSL Python | 3.10（系统自带 fastapi/uvicorn/httpx/pyserial/pytest） |

## 为什么用 WSL 开发

Windows 侧受 **E-SafeNet 透明加密**影响：所有 `.py` 在磁盘上是密文（文件头 `b.#...E-SafeNet...LOCK`），非白名单进程读到密文，导致：
- PyInstaller 打包必须先从 git 导出明文副本（`build_exe.bat` 已自动处理）
- IDE/测试进程可能读不到明文源码

WSL 的 **ext4 文件系统不受 E-SafeNet 加密**，`git clone` 下来的源码是**明文**，开发、测试、跑 Web 服务毫无障碍。

## 一次性初始化（已配置）

- [x] WSL 工作区已 clone 远程仓库（`/01-workfile-ai/01-zzt/ZZT_SELF`）
- [x] 系统 Python 已具备依赖（fastapi/uvicorn/httpx/pyserial/pytest）
- [x] `.wslconfig` 已配置串口映射（见下）

## 日常开发循环（WSL）

```bash
cd /01-workfile-ai/01-zzt/ZZT_SELF

# 1. 拉取 Windows / 他侧的提交
git pull

# 2. 编辑源码（明文，任意编辑器皆可）
#    code module_log/app.py   # 或 vim / VSCode WSL 远程

# 3. 跑测试（纯逻辑部分）
python3 -m pytest loghooks/test_loghooks.py module_log/test_loghooks_api.py -q

# 4. 起 Web 服务（module_log）
python3 -m uvicorn module_log.app:app --host 0.0.0.0 --port 8766
#   Windows 浏览器访问 http://localhost:8766  （WSL2 自动转发 localhost）

# 5. 提交推送
git add -A && git commit -m "..."
git push
```

## 串口映射（WSL 直通 Windows 串口）

已在 `C:\Users\A24006872\.wslconfig` 配置：

```ini
[wsl2]
networkingMode=nat
autoProxy=false

[serialports]
COM3 = /dev/ttyS3
COM23 = /dev/ttyS23
COM4 = /dev/ttyS4
COM24 = /dev/ttyS24
```

> 本项目实际烧录/采集使用 **COM3 / COM23**（COM4/COM24 为备用 CP210x，一并映射以防切换）。

**生效方式**：执行 `wsl --shutdown` 后重新打开 WSL。

验证：
```bash
python3 -c "import serial; s=serial.Serial('/dev/ttyS3',115200,timeout=0.5); print('OK',s.name); s.close()"
python3 -c "import serial; s=serial.Serial('/dev/ttyS23',115200,timeout=0.5); print('OK',s.name); s.close()"
```

> 若某台机器 COM 口不同，改 `.wslconfig` 的 `[serialports]` 段再重启即可。
> 若串口无法直通（如 COM 号超限），回退方案：Windows 侧跑 TCP 串口代理，WSL 连 TCP。

### 原理：WSL 串口直通是怎么做的

**一句话**：在 `.wslconfig` 里声明"把 Windows 的哪个 COM 口暴露给 WSL"，WSL 启动时把该物理串口"直通"进 Linux 内核，在 `/dev/ttyS{n}` 上挂一个虚拟串口节点，两端读写完全透传。

**三步机制**：

1. **配置声明**：`[serialports]` 段，左侧 `COM{n}` 是 Windows 串口名，右侧 `/dev/ttyS{n}` 是 WSL 里的 Linux 设备路径（习惯同名）。
2. **WSL 启动时桥接**：`wsl --shutdown` 后重新打开时，WSL 驱动打开 Windows 侧对应 COM 口句柄，通过虚拟串口通道在 WSL 内核挂出 `ttyS{n}` 节点。
3. **数据透传**：WSL 里 `write` 的数据物理上从 USB 串口芯片发出；外部设备回的数据 WSL 里 `read` 能收到。两侧字节流完全一致。

**关键细节**：
- **不是复制/模拟**：是同一物理串口的两个视图。
- **独占**：同一时刻只能一端打开。WSL 持有 COM3 时，Windows 侧其他程序再开会 `access denied`，反之亦然。
- **不抢 Windows**：不映射时 WSL 碰不到 COM 口；映射只是"多一个入口"，Windows 侧程序不受影响。
- **默认 ttyS0-7 是占位**：没配 `[serialports]` 时打开报 `Input/output error`（无真实后端）。
- **COM{n} 与 /dev/ttyS{n} 一一对应**：WSL2 是轻量 VM，Linux 侧本无 Windows 串口，`[serialports]` 本质是把 Windows 串口对象以 Linux tty 设备形式桥接进 VM（与 `networkingMode=nat` 桥接网络同理）。

**排查**：若映射后仍打不开，先确认该 COM 未被 Windows 侧程序占用（`Access denied`），或设备未插（`Input/output error`）。


## 哪些留 Windows

| 模块 | 原因 |
|---|---|
| `module_log/desktop.py` | pywebview 桌面窗口，需 Windows GUI |
| 串口烧录（`module_serial_service`/`xmodem_flash`/`flash_module`） | 依赖真实 COM 口（或用映射后的 `/dev/ttyS*`） |
| `listener/serial_service.py` | 侦听台串口采集 |
| `shared/dll`（C# 解析库） | .NET 编译产物 |
| `tools/packaging/`（打包脚本） | 产 Windows exe |

## 哪些在 WSL

| 模块 | 用途 |
|---|---|
| `loghooks/*` | 规则引擎（纯逻辑） |
| `parser_lib/*` | 解析路由（纯逻辑） |
| `module_log/app.py` | Web 服务（uvicorn，浏览器访问） |
| `listener/app.py` | Web 服务（串口采集部分仍留 Windows） |
| 全部 `test_*.py` | pytest 单元测试 |

## 注意

1. **WSL 不要读 `/mnt/d` 上的项目源码**——那是 E-SafeNet 密文，会乱码。只用自己的 ext4 副本。
2. **桌面 exe 只能在 Windows 打包**（`tools/packaging/build_exe.bat`），产物 `dist\` 不入 git。
3. **WSL 里 git push 需要 SSH key**（`~/.ssh/id_ed25519`），若未配置改用 HTTPS + token。
