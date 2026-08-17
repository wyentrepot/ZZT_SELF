# TODO.md — 需求 0003 可执行任务

> ⛔ **状态：已屏蔽（暂不实现）**（2026-08-17 用户决定）。恢复执行前不推进以下阶段；恢复时从本文件阶段 0 建功能分支继续。
> 其他 AI 按顺序执行。每阶段先写失败测试，再做最小实现，再验证和评审。协议以 ../../docs/05-Windows串口转TCP与WSL虚拟串口方案.md 为准。

## 0. 执行纪律

- [x] 需求、设计、总需求、骨架、总任务和 ADR 已登记。
- [ ] ZZT_SELF 实现前建功能分支并保存 git status --short。
- [ ] 确认 D:\019-wy-tool\uart_to_tcp 无需保留的同名内容。
- [ ] WSL 仓库只在默认 WSL 写入/Git/测试/构建；Windows 项目只用 Windows 工具链。
- [ ] 每阶段评审通过再继续；不顺手改协议、loghooks、编排。
- [ ] 不加自动重连、续跑、COM 共享、局域网监听。

## 1. Windows 骨架和日志

文件：src\main.py、ui.py、models.py、config.py、runtime_info.py、logging_setup.py；tests\test_runtime_info.py、test_logging_setup.py；依赖文件。

接口：BridgeConfig；RuntimeInfoWriter.write/remove；setup_logging；BridgeWindow 只读状态快照。

- [ ] 先写 bridge.json 原子替换/schema/退出删除测试。
- [ ] 运行 py -m pytest tests/test_runtime_info.py -q；预期模块不存在。
- [ ] 最小实现后复跑；预期通过。
- [ ] 先写日志轮转/字段/诊断摘要上限测试，运行后预期失败。
- [ ] 最小实现后复跑；预期通过。
- [ ] 实现 Tkinter 窗口和 py -m src.main --dry-run；预期退出 0、不打开 COM。
- [ ] 评审：UI 不持有串口；无固定发行版；日志可轮转。

## 2. COM 会话和独占

文件：src\session_manager.py、serial_session.py、models.py；tests\fakes.py、test_session_manager.py。

接口：SerialConfig；SessionManager.list_ports/acquire/get/release；七态状态机。

- [ ] 写假串口，覆盖枚举不打开、二次冲突、不同 COM 并存、关闭幂等、打开失败释放。
- [ ] 运行 py -m pytest tests/test_session_manager.py -q；预期模块不存在。
- [ ] 最小实现并复跑；预期通过且不访问真实 COM。
- [ ] 接入可注入 pyserial factory。
- [ ] 评审：阻塞 read 不在管理锁内；不排队/抢占；异常释放句柄。

## 3. Control API

文件：src\control_api.py、models.py、main.py；tests\test_control_api.py。

- [ ] 写 token、health、ports、session CRUD、config、统一错误体测试。
- [ ] 运行目标测试；预期 create_control_app 不存在。
- [ ] 最小实现设计第 5 节接口并复跑；预期通过。
- [ ] 精确断言 PORT_IN_USE、SESSION_NOT_FOUND、INVALID_REQUEST、PORT_OPEN_FAILED。
- [ ] 评审：全部鉴权；不泄漏 token、句柄、堆栈。

## 4. TCP 数据转发

文件：src\data_server.py、serial_session.py；tests\test_data_server.py。

- [ ] 写错误握手、超时、双向字节、半包、粘包、大块顺序测试。
- [ ] 运行目标测试；预期 DataServer 不存在。
- [ ] 最小实现握手、双向复制、有界缓冲、keepalive、清理并复跑。
- [ ] 增加 TCP 断开和 serial.read/write 异常，断言 closed 且释放。
- [ ] 运行 session/control/data 三组测试；预期通过。
- [ ] 评审：不解析/转码/补换行；无无界队列；不重连。

## 5. Windows XMODEM

文件：src\xmodem_service.py、serial_session.py、control_api.py；tests\test_xmodem_service.py。

- [ ] 从 apps/module_log/xmodem_flash.py 提取行为和假串口向量。
- [ ] 写路径/可读/size/hash/baud_plan 校验；失败时串口零写入。
- [ ] 运行目标测试；预期模块不存在。
- [ ] 最小实现校验/job 状态并复跑校验。
- [ ] 写成功、ACK/NAK、超时、动态波特率、恢复测试；预期失败。
- [ ] 最小移植 XMODEM，唯一 read 循环投递响应队列；复跑应通过。
- [ ] 写恢复失败，断言 FLASH_RESTORE_FAILED 且 session 关闭。
- [ ] 接入 POST/GET flash；control+xmodem 测试通过。
- [ ] 评审：失败不整任务重跑；flashing 拒绝普通写/配置。

## 6. WSL 共享后端

文件：libs/shared/serial_transport.py、windows_uart_bridge.py 及两个测试和依赖文件。

- [ ] 写 SerialEndpoint、local 默认、工厂、最小成员、close 幂等测试。
- [ ] WSL 运行 python -m pytest libs/shared/test_serial_transport.py -q；预期模块不存在。
- [ ] 最小实现 local，复跑 local 通过。
- [ ] 用假 HTTP/TCP 写 bridge.json、ports、session、握手、read/write/in_waiting、断开、close 测试。
- [ ] 运行 windows bridge 测试；预期 windows_tcp 未实现。
- [ ] 最小实现客户端和 WindowsTcpSerialTransport，不加重连；两组测试通过。
- [ ] iconv 校验新文件并运行 git diff --check。
- [ ] 评审：apps 不读取 bridge.json；断开后读写失败；无重连线程。

## 7. listener 接入

文件：apps/listener/serial_service.py、app.py、后端/UI 测试、独立前端和 workbench listener 副本。

- [ ] 写旧请求 local、windows_tcp 工厂、虚拟列表、busy、断开 error 测试。
- [ ] 运行 listener 后端测试；预期新断言失败。
- [ ] 最小接入共享工厂，保留切帧/落盘/索引；复跑通过或仅既知 DLL 跳过。
- [ ] 写来源控件、字段、busy 禁选、workbench 前缀测试；预期失败。
- [ ] 同步两套页面；运行 UI 测试和两个 JS node --check。
- [ ] 评审：枚举不占 COM；断开不新建 session；切帧仍在 WSL。

## 8. module_log 和烧录接入

文件：apps/module_log/module_serial_service.py、flash_module.py、app.py、对应测试、独立前端和 workbench module 副本。

- [ ] 写 CCO/STA transport、默认 local、工厂、冲突错误测试；运行预期失败。
- [ ] 最小接入共享工厂，保留日志/内存/loghooks。
- [ ] 写 flash 分流：local 旧逻辑；windows_tcp 用 wslpath -w、size/SHA-256、Windows flash/轮询；预期失败。
- [ ] 最小实现远程烧录，不拼发行版、不重试；后端测试通过。
- [ ] 写 CCO/STA/simcon 面板来源、占用、中断、workbench 前缀测试。
- [ ] 同步两套页面；前端测试和 JS 语法通过。
- [ ] 评审：windows_tcp 不在 WSL 执行 XMODEM；只有 Windows 读真实串口。

## 9. sim_concentrator 接入

文件：libs/sim_concentrator/serial_io.py、api.py、runner.py、cli.py 和目标测试。

- [ ] 写默认 local、windows_tcp 工厂、偶校验、断开任务 error；运行预期失败。
- [ ] 最小透传 transport，不改 frame_codec/matcher/responder。
- [ ] 目标测试和 python -m pytest libs/sim_concentrator -q 通过。
- [ ] 评审：1376.2 切帧在 WSL；Windows 只见字节；旧 JSON 默认 local。

## 10. 回归、打包和实机

- [ ] 写 BRIDGE_UNAVAILABLE、PORT_IN_USE、SERIAL_READ_FAILED、SERIAL_WRITE_FAILED、TCP_DISCONNECTED、FLASH_FAILED 映射测试。
- [ ] 最小补齐异常；Windows py -m pytest -q 全过。
- [ ] WSL 运行 python -m pytest apps/listener apps/module_log apps/workbench libs/shared libs/sim_concentrator -q；除明确 DLL 跳过外无失败。
- [ ] WSL iconv 和 git diff --check 通过；双方日志用 session_id 对齐。
- [ ] 写 packaged smoke，构建前因 dist 缺失而失败。
- [ ] 实现 uart_to_tcp.spec/build.bat，生成 dist\uart_to_tcp\uart_to_tcp.exe，冒烟通过。
- [ ] 双击验收窗口、bridge.json、health、日志、退出释放。

真实证据保存到 Windows acceptance\日期 和 WSL data/runs/uart-bridge-日期：

- [ ] 记录环境、版本、COM、固件 hash。
- [ ] 分别验收 listener、CCO、STA、sim_concentrator。
- [ ] 验收共享路径 XMODEM。
- [ ] 验收二次申请 PORT_IN_USE、断 TCP、拔串口、重启不恢复。
- [ ] 连续运行至少 8 小时，检查内存、线程、日志轮转。
- [ ] 写入 DONE.md；无真实证据不得标记完成。
