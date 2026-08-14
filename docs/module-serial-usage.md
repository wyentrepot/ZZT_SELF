# 模块日志 / 烧录串口使用说明（serial-flash-session）

本文档说明 ZZT_SELF 的「模块日志口 + XMODEM 烧录」一体化功能如何使用。
对应需求：`reqs/0001-serial-flash-session`；设计文档权威位置：
`department-ai-skills/docs/serial-flash-session-design.md`。

## 一、功能概览

- **串口全程实时监控**：一个「模块日志口」被 `ModuleSerialService` 常驻独占，
  原始字节流实时落盘 `data/logs/MODCOM{port}_{ts}_模块日志.txt`，前端新标签页
  `/module-serial` 800ms 增量轮询实时查看。
- **同一串口上烧录**：XMODEM 烧录在**同一个已持有的 handle** 上传输文件 +
  动态切波特率，RX 监控线程全程不停、不关串口、不重开。
- **与侦听台完全独立**：两套 COM、两套数据处理，互不干扰，可同时运行。
- **双入口**：前端页面按钮 + 技能/脚本，都调 `/api/module-serial/*`。

## 二、启动项目

```bash
# ZZT_SELF 目录，Python 3（用项目 venv 或系统 python3 均可）
python -m module_log.run       # 模块日志/烧录，默认监听 http://127.0.0.1:8766
```

浏览器打开 `http://127.0.0.1:8765/module-serial`（独立新标签页）。

## 三、前端页面操作

1. **连接**：选串口 → 选波特率 → 点「启动」。串口常驻独占（start 打开、
   stop 关闭），状态栏显示 `running`。
2. **实时日志**：RX/TX/事件行同一条时间线自动滚动，可暂停自动滚动/清屏。
3. **烧录**：
   - 填「固件 .bin 路径」（或点「选择文件…」走系统文件对话框）；
   - 填 `image slot`（默认 0）；波特率方案（默认 `9600,115200,9600`，
     逗号分隔：第 1 段进入 bootloader、第 2 段传输、第 3 段恢复；单值跳过切换）；
   - 勾选「烧录后不重启」可选；
   - 点「开始烧录」→ 确认对话框 → 开始。进度条 + 日志同屏，状态栏显示
     烧录中 `packet/total`。烧录期间 RX 监控不停。
4. **停止**：点「停止」关闭串口。日志文件路径显示在状态栏/日志区。

## 四、REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/module-serial/ports` | 列出可用 COM |
| GET | `/api/module-serial/status` | 服务状态 + flash 进度 |
| POST | `/api/module-serial/start` | `{"port":"COM7","baudrate":9600}` |
| POST | `/api/module-serial/stop` | 停止并关闭串口 |
| POST | `/api/module-serial/write` | `{"data":"AA BB CC"}` 手动写字节（烧录中拒绝） |
| POST | `/api/module-serial/baudrate` | `{"baudrate":115200}` 动态改波特率 |
| POST | `/api/module-serial/flash` | `{"bin_path":"...","slot":0,"baud_plan":[9600,115200,9600],"no_reboot_after":false}` |
| GET | `/api/module-serial/logs?after=<seq>` | 增量拉取日志（after=-1 全部） |

## 五、独立烧录脚本（项目不在运行时）

ZZT_SELF 不在运行时，用 `flash_module.py` 自己开串口烧录（用完即关，
复用同一 XMODEM 核心）：

```bash
python flash_module.py --port COM7 --bin D:\\fw\\app.bin --slot 0
python flash_module.py --port COM7 --bin /mnt/c/fw/app.bin --baud-plan 9600,115200,9600
python flash_module.py --selftest        # 无硬件自检（CRC 0x31C3）
python flash_module.py --list-ports      # 列出串口
python flash_module.py --dry-run --port COM7 --bin app.bin   # 只校验参数
```

> 注意：项目运行时模块串口已被服务独占，请用页面/API，勿用脚本硬开同一
> COM（Windows 会端口占用）。

## 六、新技能 serial-flash-session

部门仓库 `department-ai-skills/skills/serial-flash-session/`（编排层）：
编排「启动服务 → 选模块日志 COM → 配置 → 烧录 → 实时查看 → 停止 → 取日志」。
安全规则：未确认 COM 口不烧、dry-run/自检不算硬件证据、烧录成功须有
bootloader `Image download OK` 确认文本。

## 七、验证记录

- XMODEM 移植：CRC 自检 `0x31C3` 通过；loopback 模拟接收方完整驱动烧录
  （300 字节 → 3 包全 ACK、波特率 9600→115200→9600、`Image download OK`、
  `BURN SUCCESS`）。
- 回归：`python3 -m unittest discover -s hplc_web/tests` 110 测试全绿
  （3 个 error 为既有 DLL 依赖 `GwHPLCAnalysis.dll` 缺失，与本次改动无关）。
