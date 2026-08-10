# 侦听台改造

> HPLC 抄表通信报文侦听、解析与分析工具

本项目已拆分为**两个完全解耦的独立应用**（共享一套基础设施）：

| 项目 | 目录 | 端口 | 用途 |
|------|------|------|------|
| **侦听台** | `listener/` | 8765 | 串口采集 + HPLC 报文解析 + 日志索引/分钟分析 |
| **模块日志/烧录** | `module_log/` | 8766 | 模块串口实时日志 + XMODEM 固件烧录 |

两者各自独立运行、互不依赖，仅通过仓库根 `启动工具.bat` 一键选择启动。

## 一、项目简介

侦听台是一款面向电力线载波（HPLC，High-speed Power Line Communication）抄表场景的**通信报文侦听与分析工具**。它可以捕获智能电表与集中器之间的 HPLC 通信帧，依据**国网 / 南网**两套协议规范对原始报文进行解析，并提供分钟级报表分析、报文文件筛选等能力。

## 二、两个应用

### 侦听台（listener，端口 8765）

- **HPLC 报文侦听与解析**：按国网、南网协议解析原始报文。
- **串口实时采集**：从 COM 口裸 7E 帧流实时读取，自动加时间戳，先落盘 `LOG/侦听台/`，再实时入库。
- **数据源二选一**：串口实时监听与日志文件分析运行时互斥。
- **分钟报表分析**、**报文文件筛选**（`/api/fs/*`）。
- 入口：`python -m listener.run`。

### 模块日志 / 烧录（module_log，端口 8766）

- **模块串口实时日志**：RX / TX 按换行切行、每行带 `YYYYMMDD-HH:MM:SS:mmm` 时间戳，分类落盘 `LOG/模块/`（下分 `cco/` 与 `sta/`）。
- **XMODEM 固件烧录**：同一串口句柄传输固件，RX 监控线程全程不停。
- 独立烧录脚本：`module_log/flash_module.py`（项目不运行时直接开 COM 烧录）。
- 入口：`python -m module_log.run`。

## 三、技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 解析引擎 | C# / .NET Framework 4.8 | 动态库 `GwHPLCAnalysis.dll`（工程 `shared/dll/DLL_NwHPLCAnalysis`，仅侦听台使用） |
| Web 服务 | Python 3 + FastAPI + Uvicorn | `listener/` 与 `module_log/` 两个独立应用，通过 `pythonnet`（`clr`）调用 C# 动态库（仅 listener） |
| 解析库 | Python 3 | `parser_lib/`（独立共享库，核心解析与适配器，未来可被其他项目引用） |
| 共享基础设施 | Python 3 | `shared/`（dotnet_parser / parser_service / application_service / infra / dll） |

## 四、目录结构

```
侦听台改造/
├── listener/                # 项目 A：侦听台（串口采集 + 解析，端口 8765）
│   ├── app.py               #   FastAPI 应用（create_app 工厂 + 模块级 app）
│   ├── run.py               #   启动入口（python -m listener.run）
│   ├── log_service.py       #   大文件流式读取 / SQLite 索引 / 分页
│   ├── serial_service.py    #   串口实时采集
│   ├── static/              #   前端 SPA（index.html / app.js / styles.css）
│   ├── requirements.txt
│   ├── 启动侦听台.bat        #   一键启动
│   └── test_*.py            #   侦听台单元测试
├── module_log/              # 项目 B：模块日志 + 烧录（端口 8766）
│   ├── app.py               #   FastAPI 应用（create_app 工厂）
│   ├── run.py               #   启动入口（python -m module_log.run）
│   ├── module_serial_service.py  # 串口 + 日志 + 烧录服务
│   ├── xmodem_flash.py      #   XMODEM 烧录核心
│   ├── flash_module.py      #   独立烧录脚本
│   ├── static/              #   前端（module-serial.html / module-serial.js / styles.css）
│   ├── requirements.txt
│   ├── 启动模块日志.bat      #   一键启动
│   └── test_*.py            #   模块日志单元测试
├── shared/                  # 共享基础设施（两个项目复用）
│   ├── infra.py             #   通用工具（文件选择 / 盘符 / 目录列举）
│   ├── dotnet_parser.py     #   C# DLL 解析封装（仅 listener 用）
│   ├── parser_service.py    #   输入校验 + DLL 串联
│   ├── application_service.py  # 应用层富化
│   ├── dll/                 #   C# 解析动态库源码，输出 GwHPLCAnalysis.dll
│   └── test_*.py            #   共享解析链路测试
├── parser_lib/              # 独立共享解析库（adapters / core），未来可被其他项目引用
├── scripts/                 # 辅助脚本
├── LOG/                     # 日志目录（分类存储：侦听台/、模块/）
├── 侦听台文档/               # 协议规范与接口说明文档
├── doc/  docs/              # 其他文档
├── DLL.sln / NwHplcDll.sln  # C# 解决方案文件
├── 启动工具.bat              # 总启动入口（1=侦听台 / 2=模块日志 / 3=全部）
└── conftest.py              # 仓库级 pytest 配置（包路径）
```

## 五、环境要求

- Windows 操作系统
- .NET Framework 4.8（构建并运行 C# 动态库）
- Python 3.x（建议 3.10+），并已加入系统 `PATH`
- Visual Studio（用于构建 C# 工程）

## 六、一键启动

双击根目录 **`启动工具.bat`**，选择：

```
1 = 侦听台（串口采集 + 日志解析，端口 8765）
2 = 模块日志 / 烧录（端口 8766）
3 = 全部启动（8765 + 8766）
```

首次运行会自动创建 `.venv` 并安装对应依赖；每个应用的启动脚本（`listener/启动侦听台.bat`、`module_log/启动模块日志.bat`）也可单独双击使用。

## 七、构建与运行

1. **构建解析动态库**：用 Visual Studio 打开 `DLL.sln`（或 `NwHplcDll.sln`），产物为 `shared\dll\bin\Debug\GwHPLCAnalysis.dll`（仅侦听台需要）。
2. **启动服务**：双击 `启动工具.bat` 选择应用，或直接运行 `python -m listener.run` / `python -m module_log.run`。
3. **停止服务**：关闭对应窗口，或在任务管理器中结束 python 进程。

## 八、单元测试

```
.venv\Scripts\python.exe -m pytest listener module_log shared parser_lib
```

## 九、相关文档

详见仓库 `侦听台文档/` 目录：

- `侦听台上位机软件协议解析动态库接口说明.docx` —— 动态库接口说明
- `侦听台报文格式.docx` —— 报文格式说明
- `南网协议/` —— 南方电网《低压电力线宽带载波通信规约》数据链路层 / 应用层规范
- `国网协议/` —— 国网《双模通信互联互通技术规范》数据链路层 / 应用层规范

## 十、说明

- `测试文件/` 为本地大体积测试数据（含超过 100MB 的原始报文），已被 `.gitignore` 忽略，**不纳入版本库**。
- 本项目源码托管于 `git@github.com:wyentrepot/ZZT_SELF.git`。
