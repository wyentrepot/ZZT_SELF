# REQS.md — 需求基线（0003 Windows 串口网关 + WSL 虚拟串口）

> v1｜2026-08-17｜**⛔ 已屏蔽（暂不实现）**——2026-08-17 用户决定从需求范围屏蔽；需求基线保留，恢复执行时解除。
> 设计：../../docs/05-Windows串口转TCP与WSL虚拟串口方案.md
> 任务：TODO.md

## 目标

在 D:\019-wy-tool\uart_to_tcp 新建可双击运行的 Windows 网关。主程序在 WSL，通过网关访问真实 COM；虚拟串口只作为普通串口的另一来源。

## 已确认需求

- Windows 提供窗口、COM 枚举、原始 TCP、HTTP 控制、轮转日志。
- 覆盖 listener、CCO、STA、独立烧录、sim_concentrator；保留 local，旧请求默认 local。
- 同一 COM 严格独占；不共享、抢占、排队。
- TCP/串口中断立即失败并释放；不自动重连或续跑。
- 普通数据在 Windows 不解析、不修改。
- XMODEM 是唯一业务例外；共享路径只读固件，先校验 size/SHA-256。
- 动态波特率、重试、恢复、烧录日志在 Windows。
- 只限同机 Windows/WSL；不绑定物理网卡；HTTP/TCP 使用随机令牌。
- 不安装虚拟 COM/PTY，不注册 Windows 服务。
- 缓冲、内存、日志有上限；双方只用各自原生工具链。

## 验收

- [ ] Windows 双击启动并生成有效 bridge.json。
- [ ] WSL 读取 COM、描述、busy、owner。
- [ ] listener、CCO、STA、sim_concentrator 通过 windows_tcp 工作。
- [ ] Windows XMODEM 从共享路径校验并烧录。
- [ ] 二次申请返回 PORT_IN_USE。
- [ ] TCP 中断/串口拔出使任务失败、释放且不重连。
- [ ] Windows 日志可区分连接、串口、文件、烧录故障。
- [ ] local 无回归；双方测试、打包冒烟和实机证据齐全。

## 变更记录

2026-08-17 用户确认方案 A、全入口覆盖、严格独占、同机限制、断线按串口失败、简洁窗口与日志，以及 Windows 侧 XMODEM 共享路径读取。
