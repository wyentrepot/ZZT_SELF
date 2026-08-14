# 侦听台改造

> HPLC 抄表通信报文侦听、解析与分析工具

本项目已拆分为**两个完全解耦的独立应用**（共享一套基础设施）。代码按「应用 / 库」分两层：

| 项目 | 目录 | 端口 | 用途 |
|------|------|------|------|
| **侦听台** | `apps/listener/` | 8765 | 串口采集 + HPLC 报文解析 + 日志索引/分钟分析 |
| **模块日志/烧录** | `apps/module_log/` | 8766 | 模块串口实时日志 + XMODEM 固件烧录 |
| **共享库** | `libs/shared/`、`libs/parser_lib/`、`libs/loghooks/`、`libs/sim_concentrator/` | — | 被上面两个应用引用 |

两者各自独立运行、互不依赖，仅通过仓库根 `启动工具.bat` 一键选择启动。

## 〇、根目录速览

> 打开仓库先看这里：哪些是代码、哪些是文档、哪些是本地噪音（不需要关注）。

| 顶层项 | 类型 | 说明 |
|--------|------|------|
| `apps/listener/` | ✅ 应用 | 侦听台（8765）：串口采集 + HPLC 报文解析 + 日志索引/分钟分析 |
| `apps/module_log/` | ✅ 应用 | 模块日志/烧录（8766）：串口日志 + XMODEM 烧录 + 对照解析 + 模拟集中器页签 |
| `libs/shared/` | 📦 库 | 共享基础设施（infra / dotnet_parser / parser_service / application_service / dll） |
| `libs/parser_lib/` | 📦 库 | 独立解析库（adapters：10376 / 645 / 698 / 双模43） |
| `libs/loghooks/` | 📦 库 | 事件监控引擎（配置驱动的日志运行状态钩子，规则在 `libs/loghooks/rules/`） |
| `libs/sim_concentrator/` | 📦 库 | 模拟集中器（13762 帧激励/应答/验证任务，可独立跑 8781） |
| `docs/` | 📄 文档 | 使用手册 / 设计方案 / 需求进度 / 协议规范（含本文档族） |
| `docs/协议/` | 📄 文档 | 协议规范（国网/南网协议、报文格式、DLL 接口说明） |
| `data/` | 📄 数据 | 数据与代码分析输出（`data/logs/` 运行时日志） |
| `legacy/` | 🗄 归档 | 历史遗留（C# 测试工程、编译产物快照） |
| `tools/packaging/` | 🔧 工具 | PyInstaller spec 与打包脚本 |
| `tools/scripts/` | 🔧 工具 | 辅助脚本（OAD 覆盖分析、冒烟测试等） |
| `reqs/` | 📄 需求 | 需求会话归档（配合 `REQS-INDEX.md`） |
| `DLL.sln` | 🔧 工程 | C# 解决方案（`libs/shared/dll` + `legacy/use` 两个工程） |
| `启动工具.bat` | 🚀 入口 | 一键启动（1=侦听台 / 2=模块日志 / 3=全部） |
| `build/ dist/` | 🚫 本地噪音 | PyInstaller 中间/发布产物（gitignore，可随时重建） |
| `data/logs/` | 🚫 本地噪音 | 运行时日志（gitignore，frozen exe 下为 exe 同目录 `LOG/`） |
| `packages/` | 🚫 本地噪音 | NuGet 还原包（gitignore，`libs/shared/dll` 构建时自动还原） |
| `测试文件/` | 🚫 本地噪音 | 本地大体积测试数据（gitignore） |
| `.venv/` `.pytest_cache/` | 🚫 本地噪音 | Python 虚拟环境 / pytest 缓存（gitignore） |

> **目录演进**：目标布局（阶段一归组 / 阶段二 apps+libs 分层）见 `docs/总设计框架/AI闭环平台项目设计需求文档.md` §6.2。

## 一、项目简介

侦听台是一款面向电力线载波（HPLC，High-speed Power Line Communication）抄表场景的**通信报文侦听与分析工具**。它可以捕获智能电表与集中器之间的 HPLC 通信帧，依据**国网 / 南网**两套协议规范对原始报文进行解析，并提供分钟级报表分析、报文文件筛选等能力。

## 二、两个应用

### 侦听台（listener，端口 8765）

- **HPLC 报文侦听与解析**：按国网、南网协议解析原始报文。
- **串口实时采集**：从 COM 口裸 7E 帧流实时读取，自动加时间戳，先落盘 `data/logs/侦听台/`，再实时入库。
- **数据源二选一**：串口实时监听与日志文件分析运行时互斥。
- **分钟报表分析**、**报文文件筛选**（`/api/fs/*`）。
- 入口：`python -m listener.run`（从仓库根运行，自动注入 apps/ 与 libs/ 路径）。

### 模块日志 / 烧录（module_log，端口 8766）

- **模块串口实时日志**：RX / TX 按换行切行、每行带 `YYYYMMDD-HH:MM:SS:mmm` 时间戳，分类落盘 `data/logs/模块/`（下分 `cco/` 与 `sta/`）。
- **XMODEM 固件烧录**：同一串口句柄传输固件，RX 监控线程全程不停。
- 独立烧录脚本：`apps/module_log/flash_module.py`（项目不运行时直接开 COM 烧录）。
- 入口：`python -m module_log.run`。

## 三、技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 解析引擎 | C# / .NET Framework 4.8 | 动态库 `GwHPLCAnalysis.dll`（工程 `libs/shared/dll/DLL_NwHPLCAnalysis`，仅侦听台使用） |
| Web 服务 | Python 3 + FastAPI + Uvicorn | `apps/listener/` 与 `apps/module_log/` 两个独立应用，通过 `pythonnet`（`clr`）调用 C# 动态库（仅 listener） |
| 解析库 | Python 3 | `libs/parser_lib/`（独立共享库，核心解析与适配器，未来可被其他项目引用） |
| 共享基础设施 | Python 3 | `libs/shared/`（dotnet_parser / parser_service / application_service / infra / dll） |

## 四、目录结构

```
侦听台改造/
├── apps/                      # 应用层（可独立运行）
│   ├── listener/              #   项目 A：侦听台（串口采集 + 解析，端口 8765）
│   │   ├── app.py             #     FastAPI 应用（create_app 工厂 + 模块级 app）
│   │   ├── run.py             #     启动入口（python -m listener.run）
│   │   ├── log_service.py     #     大文件流式读取 / SQLite 索引 / 分页
│   │   ├── serial_service.py  #     串口实时采集
│   │   ├── static/            #     前端 SPA（index.html / app.js / styles.css）
│   │   ├── requirements.txt
│   │   ├── 启动侦听台.bat      #     一键启动
│   │   └── test_*.py          #     侦听台单元测试
│   └── module_log/            # 项目 B：模块日志 + 烧录（端口 8766）
│       ├── app.py             #     FastAPI 应用（create_app 工厂）
│       ├── run.py             #     启动入口（python -m module_log.run）
│       ├── module_serial_service.py  # 串口 + 日志 + 烧录服务
│       ├── xmodem_flash.py    #     XMODEM 烧录核心
│       ├── flash_module.py    #     独立烧录脚本
│       ├── static/            #     前端（module-serial.html / module-serial.js / styles.css）
│       ├── requirements.txt
│       ├── 启动模块日志.bat    #     一键启动
│       └── test_*.py          #     模块日志单元测试
├── libs/                      # 库层（被 apps/ 引用）
│   ├── shared/                #   共享基础设施（两个项目复用）
│   │   ├── infra.py           #     通用工具（文件选择 / 盘符 / 目录列举）
│   │   ├── dotnet_parser.py   #     C# DLL 解析封装（仅 listener 用）
│   │   ├── parser_service.py  #     输入校验 + DLL 串联
│   │   ├── application_service.py  # 应用层富化
│   │   ├── dll/               #     C# 解析动态库源码，输出 GwHPLCAnalysis.dll
│   │   └── test_*.py          #     共享解析链路测试
│   ├── parser_lib/            #   独立共享解析库（adapters / core），未来可被其他项目引用
│   ├── loghooks/              #   配置驱动的日志运行状态钩子（模块日志关键事件解析）
│   └── sim_concentrator/      #   模拟集中器验证工具（模拟 1376.2 集中器模块）
├── tools/scripts/             # 辅助脚本（OAD 覆盖分析、冒烟测试等）
├── tools/packaging/           # PyInstaller spec 与打包脚本
├── docs/                      # 项目文档（使用手册 / 设计方案 / 历史 AI 会话计划 specs）
├── docs/协议/                  # 协议规范与接口说明文档（国网/南网协议、报文格式、DLL 接口）
├── data/logs/                 # 运行时日志目录（分类存储：侦听台/、模块/）
├── legacy/                    # 历史遗留归档（use/ C# 测试工程、dll_Tesll/ 编译产物快照）
├── build/  dist/              # PyInstaller 中间产物 / 打包产物（git 忽略）
├── DLL.sln                    # C# 解决方案文件（libs/shared/dll + legacy/use 两个工程）
├── 启动工具.bat                # 总启动入口（1=侦听台 / 2=模块日志 / 3=全部）
└── conftest.py                # 仓库级 pytest 配置（注入 apps/ 与 libs/ 到 sys.path）
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

首次运行会自动创建 `.venv` 并安装对应依赖；每个应用的启动脚本（`apps/listener/启动侦听台.bat`、`apps/module_log/启动模块日志.bat`）也可单独双击使用。

## 七、构建与运行

1. **构建解析动态库**：用 Visual Studio 打开 `DLL.sln`，产物为 `libs\shared\dll\bin\Debug\GwHPLCAnalysis.dll`（仅侦听台需要）。命令行方式：双击根目录 `build_dll.bat`（GBK 编码，MSBuild Debug 编译，需 VS2022 BuildTools）。
2. **启动服务**：双击 `启动工具.bat` 选择应用，或直接运行 `python -m listener.run` / `python -m module_log.run`（从仓库根运行；`python -m` 需要 apps/ 与 libs/ 在 `PYTHONPATH` 或已由入口脚本自动注入，Windows 下建议用启动脚本）。
3. **启动一体化工作台（FR-6）**：`python -m workbench.run`（端口 8790，统一页签界面：验证工作台 / 模块日志 / 侦听台）；桌面模式 `python -m workbench.desktop`（pywebview 单窗口）。依赖 `libs\shared\dll\bin\Debug\GwHPLCAnalysis.dll`（未编译时侦听台页签自动降级为"不可用"，其余功能不受影响）。
4. **停止服务**：关闭对应窗口，或在任务管理器中结束 python 进程。

## 八、单元测试

```
.venv\Scripts\python.exe -m pytest apps/listener apps/module_log apps/workbench libs/shared libs/parser_lib
```

> 注：`apps/listener` 与 `libs/shared` 的部分测试依赖 C# DLL 编译产物（`build_dll.bat` 后即可全绿）；`test_concurrent_meter_e2e.py`、`test_dotnet_parser.py`、`test_dll_python_meter_consistency.py` 属 DLL/串口集成用例，需真实 DLL 环境。

## 九、相关文档

详见仓库 `docs/协议/` 目录：

- `侦听台上位机软件协议解析动态库接口说明.docx` —— 动态库接口说明
- `侦听台报文格式.docx` —— 报文格式说明
- `南网协议/` —— 南方电网《低压电力线宽带载波通信规约》数据链路层 / 应用层规范
- `国网协议/` —— 国网《双模通信互联互通技术规范》数据链路层 / 应用层规范

## 十、说明

- `测试文件/` 为本地大体积测试数据（含超过 100MB 的原始报文），已被 `.gitignore` 忽略，**不纳入版本库**。
- 本项目源码托管于 `git@github.com:wyentrepot/ZZT_SELF.git`。
