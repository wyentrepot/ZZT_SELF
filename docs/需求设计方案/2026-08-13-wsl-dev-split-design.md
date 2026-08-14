# WSL 开发环境 + Windows 硬件环境 拆分设计

日期：2026-08-13
状态：已确认设计，待实现

## 背景与动机

当前项目在 Windows + E-SafeNet 透明加密环境下开发，痛点：

1. 所有 `.py` 源文件在磁盘上是 E-SafeNet 密文（文件头 `b.#...E-SafeNet...LOCK`），非白名单进程（IDE 子进程、测试进程、PyInstaller 等）读到的都是密文，导致：
   - PyInstaller 打包必须先从 git 导出明文副本（已修复，但绕）
   - 任何非白名单的开发工具都可能读不到明文源码
2. 纯软件开发（写码、跑 pytest、起 Web 服务）与硬件操作（串口、烧录、桌面窗口）混在同一环境，互相拖累。

目标：**纯开发环境迁到 WSL（ext4 明文区），硬件相关保留在 Windows**，两侧通过 git 远程仓库同步。

## 已验证的事实（2026-08-13 实测）

| 项目 | 结果 |
|---|---|
| WSL Ubuntu-22.04（WSL2） | ✅ 运行中，Python 3.10.12 |
| WSL 读 `/mnt/d/.../*.py` | ❌ 仍是 E-SafeNet 密文（drvfs 受加密驱动影响） |
| WSL 里 `git clone` 到 ext4（`/tmp`、`~`） | ✅ 明文（ext4 不受加密） |
| WSL import 核心模块 | ✅ loghooks.*、parser_lib.core.*、module_log.app、module_log.module_serial_service、module_log.xmodem_flash、module_log.flash_module 全部成功 |
| WSL 依赖 | ✅ fastapi / uvicorn / httpx / pyserial 已装 |
| WSL 串口 `/dev/ttyS0-7` | ⚠️ 设备节点存在，但未映射真实 COM（`Input/output error`），需 `.wslconfig` 配置 |
| pywebview 桌面（desktop.py） | ❌ 依赖 Windows GUI，WSL 不可跑 |
| git 远程仓库 | ✅ `git@github.com:wyentrepot/ZZT_SELF.git`（SSH） |

## 架构

```
┌─────────────────────────── Windows 侧（硬件 / 打包）──────────────────────────┐
│  E-SafeNet 加密区（D:\019-wy-tool\ZZT_SELF）                                 │
│  ├─ 串口硬件脚本：module_log/module_serial_service.py、xmodem_flash.py、      │
│  │                  flash_module.py                                          │
│  ├─ 桌面窗口：module_log/desktop.py（pywebview，连 WSL 的 Web 服务）            │
│  ├─ C# 解析库：shared/dll（DLL 编译产物，供 listener 解析）                    │
│  ├─ 打包：packaging/build_exe.bat（已适配 E-SafeNet，git 明文副本构建）         │
│  └─ 一键启动：启动工具.bat / 启动模块日志.bat / 启动侦听台.bat                   │
└────────────────────────────────────────────────────────────────────────────┘
                          │  git push / pull（origin: GitHub）
                          ▼
┌─────────────────────────── WSL 侧（开发 / 测试 / Web）────────────────────────┐
│  ext4 明文区（~/zzt）                                                       │
│  ├─ git clone 远程仓库（明文源码，不受 E-SafeNet 影响）                         │
│  ├─ 日常开发：编辑、重构、pytest 单元测试                                     │
│  ├─ Web 服务：module_log.app（uvicorn，浏览器 / 或经 Windows desktop.py 访问） │
│  ├─ 纯逻辑：loghooks（规则引擎）、parser_lib（解析路由）                        │
│  └─ 串口：经 .wslconfig 映射后访问 /dev/ttyS*（可选，用于无 GUI 的串口测试）      │
└────────────────────────────────────────────────────────────────────────────┘
```

## 组件拆分清单

### 留 Windows（硬件 / 交互）
- `module_log/desktop.py` — pywebview 桌面入口（GUI 必须 Windows）
- `module_log/module_serial_service.py`、`xmodem_flash.py`、`flash_module.py` — 串口烧录（依赖真实 COM，需 Windows 侧或映射后 WSL 侧运行）
- `shared/dll` — C# 解析库编译产物
- `listener/serial_service.py` — 侦听台串口采集
- `packaging/*` — 打包脚本（exe 产物在 Windows）
- 各 `.bat` 启动脚本

### 迁 WSL（开发 / 逻辑 / Web）
- 全部 `module_log/*.py`、`loghooks/*`、`parser_lib/*`、`shared/infra.py` 等纯逻辑
- `module_log/app.py` 的 Web 服务（uvicorn，浏览器访问）
- `listener/app.py` 的 Web 服务（浏览器访问；串口采集部分仍需 Windows 或映射串口）
- 单元测试 `test_*.py`（pytest）

## 关键流程

### 1. WSL 初始化
```bash
# 一次性初始化
git clone git@github.com:wyentrepot/ZZT_SELF.git ~/zzt
cd ~/zzt
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate
pip install -r module_log/requirements.txt -r requirements.txt  # 按需
```

### 2. 日常开发循环（WSL）
```bash
cd ~/zzt
git pull                # 拉 Windows/他侧提交
# ... 编辑、测试 ...
pytest module_log/ test_loghooks.py   # 明文环境跑测试
git add -A && git commit -m "..."
git push                # 推回远程
```

### 3. 桌面使用（Windows）
- Windows 侧 `desktop.py` 已构建 exe（`dist\模块日志\模块日志.exe`）或源码直跑
- 其内嵌 Web 指向 WSL 的 uvicorn 服务（`http://localhost:8766`，WSL2 localhost 自动转发）
- 或直接浏览器访问 WSL 服务地址

### 4. 串口映射（可选，需真实 COM）
在 `C:\Users\A24006872\.wslconfig` 增加：
```ini
[wsl2]
[serialports]
COM3 = /dev/ttyS3   # 按实际 COM 口调整
```
`wsl --shutdown` 后重启 WSL 生效。然后 WSL 里 pyserial 可打开 `/dev/ttyS3`。
> 若目标机器串口不固定/无法映射，回退方案：Windows 侧跑 TCP 串口代理，WSL 连 TCP。

### 5. 打包（保持 Windows）
- `packaging/build_exe.bat` 已适配 E-SafeNet（自动 git 明文副本构建）
- 打包产物 `dist\` 仅存 Windows，不入 git

## 边界与约束

1. **WSL 不读 `/mnt/d` 上的项目源码**（是密文）；只用自己的 ext4 副本。
2. **E-SafeNet 加密只影响 Windows 侧**；WSL ext4 永久明文，开发不再受折磨。
3. **SSH key**：WSL 需配置 GitHub SSH key（`~/.ssh/id_ed25519`），或改用 HTTPS + token。
4. **串口真实映射**依赖实际硬件 COM 口，需现场确认。
5. **WSL 不打包 exe**（PyInstaller 打 Windows exe 需 wine，复杂度高，不做）。

## 非目标（YAGNI）
- 不在 WSL 里跑 pywebview 桌面（WSLg 兼容性不投入）
- 不在 WSL 里打包 Windows exe
- 不做双向实时文件同步（git 同步足够，避免加密/权限混乱）

## 待现场确认项
- [ ] 实际串口使用的 COM 口编号（映射用）
- [ ] WSL 里 GitHub SSH key 是否已配置
- [ ] Windows 侧 desktop.py 连 WSL Web 服务的端口约定（8765/8766）
