# Windows 串口转 TCP 与 WSL 虚拟串口专项方案

> 状态：已确认｜日期：2026-08-17｜需求：0003
> 选型：方案 A——原始 TCP 数据通道 + HTTP 控制接口；XMODEM 是唯一 Windows 侧业务例外。

## 1. 目标和边界

在 D:\019-wy-tool\uart_to_tcp 新建可双击运行的 Windows 串口网关，主程序继续运行在 /01-workfile-ai/01-zzt/ZZT_SELF。WSL 的“虚拟串口”是应用层后端，不安装虚拟 COM/PTY，也不创建 /dev/tty。

覆盖：

- apps/listener/serial_service.py：侦听台只读采集。
- apps/module_log/module_serial_service.py：CCO/STA 日志和普通收发。
- apps/module_log/flash_module.py：独立烧录入口。
- libs/sim_concentrator/serial_io.py：模拟集中器双向交互。
- CCO/STA XMODEM：Windows 使用真实串口执行。
- listener、module_log 独立页面和 workbench 复制页面。

保留 local 模式；旧请求未传 transport 时默认 local。

明确不做：

- 不允许局域网访问，不注册 Windows 服务。
- 不共享、不抢占、不排队同一 COM。
- 不自动重连、恢复采集或续跑任务。
- Windows 不解析侦听帧、模块日志、1376.2。
- 不改用 RFC2217；除 XMODEM 外 Windows 只做转发、会话和诊断。

## 2. 架构

~~~text
listener ─┐
module_log ├─> libs/shared/serial_transport.py
simcon ────┘          ├─ local -> pyserial
                      └─ windows_tcp
                         ├─ HTTP：枚举/租约/状态/配置/烧录
                         └─ TCP：原始串口字节
                                      │
Windows uart_to_tcp
├─ Tkinter 简洁窗口
├─ SessionManager（COM 独占权威）
├─ FastAPI Control API
├─ Data TCP Server
├─ pyserial
├─ XMODEM Service
└─ 轮转日志 -> COM3/COM4/...
~~~

推荐 Python 3.11、Tkinter、pyserial、FastAPI/uvicorn、PyInstaller。首版优先稳定 onedir，双击 exe 即用；单文件不是验收条件。

Windows 项目文件：

~~~text
D:\019-wy-tool\uart_to_tcp\
├── src\
│   ├── main.py ui.py models.py config.py
│   ├── runtime_info.py logging_setup.py
│   ├── session_manager.py serial_session.py
│   ├── control_api.py data_server.py
│   └── xmodem_service.py
├── tests\
├── runtime\
├── logs\
├── requirements.txt requirements-build.txt
├── uart_to_tcp.spec build.bat
└── README.md
~~~

## 3. 同机发现与鉴权

Windows 原子写入 D:\019-wy-tool\uart_to_tcp\runtime\bridge.json，WSL 从 /mnt/d/019-wy-tool/uart_to_tcp/runtime/bridge.json 读取：

~~~json
{
  "schema_version": 1,
  "instance_id": "uuid",
  "pid": 1234,
  "started_at": "ISO-8601",
  "control_url": "http://WSL可达地址:32100",
  "data_host": "WSL可达地址",
  "data_port": 32101,
  "token": "本次运行随机令牌"
}
~~~

- 临时文件写完、flush 后 replace；正常退出删除，下次启动覆盖。
- 只绑定 127.0.0.1 和自动识别的 WSL 虚拟接口，不绑定物理网卡。
- HTTP 使用 Bearer token；TCP 握手校验 token、instance_id、session_id。
- 不硬编码 WSL 发行版或 Windows 主机 IP。
- 默认控制端口 32100、数据端口 32101，以 bridge.json 为事实来源。

## 4. WSL 串口接口

~~~python
@dataclass(frozen=True)
class SerialEndpoint:
    transport: Literal["local", "windows_tcp"]
    port: str

class SerialTransport(Protocol):
    port: str
    baudrate: int
    @property
    def is_open(self) -> bool: ...
    @property
    def in_waiting(self) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...
~~~

请求增加 transport，不把来源编码进 port：

~~~json
{"transport":"windows_tcp","port":"COM3","baudrate":115200,"bytesize":8,"parity":"N","stopbits":1}
~~~

in_waiting 表示 WSL 客户端本地接收缓冲的可读字节数。

## 5. Control API v1

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /v1/health | 实例健康 |
| GET | /v1/ports | COM、描述、busy、owner |
| POST | /v1/sessions | 校验参数、打开并租用 COM |
| GET | /v1/sessions/{id} | 状态、参数、计数 |
| PATCH | /v1/sessions/{id}/config | 修改允许的串口参数 |
| DELETE | /v1/sessions/{id} | 幂等关闭和释放 |
| POST | /v1/sessions/{id}/flash | 启动 XMODEM |
| GET | /v1/sessions/{id}/flash | 查询进度和终态 |

owner 固定：listener、module_log:cco、module_log:sta、module_log:flash、sim_concentrator。枚举不打开串口，启动时才申请租约。

统一错误：

~~~json
{"schema_version":1,"error":{"code":"PORT_IN_USE","message":"COM3 已被 module_log:cco 占用","details":{"port":"COM3","owner":"module_log:cco"}}}
~~~

错误码至少包括 BRIDGE_UNAVAILABLE、AUTH_FAILED、INVALID_REQUEST、PORT_NOT_FOUND、PORT_IN_USE、PORT_OPEN_FAILED、SERIAL_READ_FAILED、SERIAL_WRITE_FAILED、SERIAL_CONFIG_FAILED、DATA_HANDSHAKE_TIMEOUT、TCP_DISCONNECTED、SESSION_NOT_FOUND、SESSION_FLASHING、FIRMWARE_NOT_FOUND、FIRMWARE_UNREADABLE、FIRMWARE_SIZE_MISMATCH、FIRMWARE_HASH_MISMATCH、FLASH_FAILED、FLASH_RESTORE_FAILED。

## 6. Data TCP

连接后先发一行 UTF-8 JSON：

~~~json
{"schema_version":1,"instance_id":"uuid","session_id":"uuid","token":"..."}
~~~

收到 {"ok":true} 后切换成原始字节流：

- WSL -> TCP -> serial.write；serial.read -> TCP -> WSL。
- 不转码、不补 CR/LF、不解析协议；TCP 分包不是帧边界。
- 使用有界缓冲和背压；keepalive 只发现死连接，不用于重连。
- 任一方向不可恢复错误关闭整个会话。
- write 返回提交给本地 TCP socket 的字节数；Windows 后续写串口失败则关闭连接，不增加逐写 ACK。

## 7. 独占和状态机

~~~text
created -> awaiting_data -> open -> flashing -> open -> closing -> closed
任一活动状态 -> error -> closed
~~~

- 同一 COM 只允许一个非 closed 会话。
- 第二申请返回 409 PORT_IN_USE 和 owner。
- TCP 中断等同串口中断：操作失败、关闭串口、释放租约。
- 串口拔出/read/write 异常关闭 TCP。
- 不从 error 自动恢复；Windows 重启后旧 session 全失效。
- flashing 禁止普通写和配置修改；关闭/释放必须幂等。

## 8. XMODEM 例外

WSL 使用当前环境的 wslpath -w 转换固件路径，不拼发行版名称。请求包含 firmware_path、size、sha256、slot、baud_plan、no_reboot_after。

Windows 在任何烧录写入前校验 session、owner、文件存在且只读可打开、size、SHA-256、slot、baud_plan。

- 使用当前 session 的同一 pyserial handle。
- 只有一个 read 循环；接收字节可同时转发 WSL 并投递 XMODEM 队列。
- Windows 本地完成动态波特率、ACK/NAK、重试、超时。
- 完成或失败后恢复初始波特率；恢复失败则 session error 并关闭。
- 不自动重跑任务。
- 进度：state、phase、packet、total、baudrate、message、error。

## 9. WSL 改造

新增：

- libs/shared/serial_transport.py
- libs/shared/windows_uart_bridge.py
- libs/shared/test_serial_transport.py
- libs/shared/test_windows_uart_bridge.py

修改：

- apps/listener/serial_service.py、app.py
- apps/module_log/module_serial_service.py、flash_module.py、app.py
- libs/sim_concentrator/serial_io.py、api.py、runner.py、cli.py
- listener/module_log 独立前端和 workbench 复制前端
- 对应后端、API、前端、打包测试

apps 不得直接读取 bridge.json 或发送网关 HTTP；这些细节只在 libs/shared/windows_uart_bridge.py。

## 10. UI 和日志

所有串口区增加来源选择：

~~~text
串口来源 [本地串口 | Windows 虚拟串口]
串口设备 [COM3 - USB Serial Port]
波特率   [115200]
~~~

- windows_tcp 动态加载 /v1/ports；busy 显示 owner 并禁选。
- Windows 未运行时明确报错，不回退 local。
- 运行中不可切换 transport；中断进入 error，不重连。
- 独立页面和 workbench 副本同时更新并以测试锁定。

窗口显示服务地址、instance、COM、owner、参数、状态、收发计数、最后活动、XMODEM 和最近错误。

日志位于 D:\019-wy-tool\uart_to_tcp\logs，按天和大小轮转，包含毫秒时间、level、event、instance_id、session_id、owner、port、参数、状态、计数和异常。默认不保存无限原始内容；诊断模式只保存有上限的十六进制摘要。

## 11. 文件系统规则

- ZZT_SELF 是 WSL 原生目录；创建、编辑、Git、测试、构建只能在默认 WSL 内执行，不通过 \\wsl.localhost 或 Windows 工具写入。
- 每批 WSL 修改后执行 UTF-8、git diff --check 和最窄测试。
- uart_to_tcp 是 Windows 原生项目；只用 Windows 工具链写入、Git、测试、打包，并建立独立版本控制。
- Windows 只读固件，不修改或删除 WSL 文件。

## 12. 完成定义

Windows 双击启动、bridge.json/窗口/日志/发布包可用；WSL 动态枚举 COM；listener、CCO、STA、sim_concentrator 支持 local/windows_tcp；严格独占；断线失败并释放且不恢复；普通字节透明；Windows XMODEM 共享路径烧录成功；双方自动测试、打包冒烟和四类实机验收有证据。
