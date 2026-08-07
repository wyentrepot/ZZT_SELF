# 侦听台改造

> HPLC 抄表通信报文侦听、解析与分析工具

## 一、项目简介

侦听台是一款面向电力线载波（HPLC，High-speed Power Line Communication）抄表场景的**通信报文侦听与分析工具**。它可以捕获智能电表与集中器之间的 HPLC 通信帧，依据**国网 / 南网**两套协议规范对原始报文进行解析，并提供分钟级报表分析、报文文件筛选等能力，帮助研发与运维人员排查通信问题、核对抄表数据。

## 二、主要功能

- **HPLC 报文侦听与解析**：按国网、南网协议解析原始报文。
- **串口实时采集**：从 COM19（115200/N/8/1）裸 7E 帧流实时读取 HPLC 帧，自动加时间戳，先落盘项目根 `LOG/` 文件夹，再实时入库，前端实时浏览。
- **数据源二选一**：串口实时监听与日志文件分析运行时互斥，切换即清空重建索引，避免数据混在一起。
- **报文解析工具**：对抓取的报文文件做结构化解析。
- **分钟报表分析**：对分钟级上报数据做状态统计与分析（`/api/logs/minute-analysis`）。
- **报文文件筛选**：从报文文件中筛选目标记录（`/api/fs/pick`）。
- **本地 Web 服务**：基于 FastAPI 提供 API 与可视化界面，默认监听 `http://127.0.0.1:8765/`。

## 三、技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 解析引擎 | C# / .NET Framework 4.8 | 动态库 `GwHPLCAnalysis.dll`（工程 `dll/DLL_NwHPLCAnalysis`） |
| Web 服务 | Python 3 + FastAPI + Uvicorn | `hplc_web/`，通过 `pythonnet`（`clr`）调用 C# 动态库 |
| 解析库 | Python 3 | `parser_lib/`（核心解析与适配器） |
| 依赖管理 | NuGet / pip | `Newtonsoft.Json` 等；`hplc_web/requirements.txt` |

## 四、目录结构

```
侦听台改造/
├── dll/                    # C# 解析动态库源码（DLL_NwHPLCAnalysis），输出 GwHPLCAnalysis.dll
├── use/                    # C# 测试工程（DLL_Test）
├── hplc_web/               # Python Web 服务（FastAPI），入口 run.py，监听 127.0.0.1:8765
├── parser_lib/             # Python 解析库（adapters / core）
├── scripts/                # 辅助脚本
├── 侦听台文档/              # 协议规范与接口说明文档
│   ├── 南网协议/            # 南方电网宽带载波通信规约
│   ├── 国网协议/            # 国网双模通信互联互通技术规范
│   ├── 侦听台上位机软件协议解析动态库接口说明.docx
│   └── 侦听台报文格式.docx
├── doc/  docs/             # 其他文档
├── DLL.sln / NwHplcDll.sln # 解决方案文件
├── 启动解析工具.bat          # 生产模式启动
├── 启动解析工具-测试模式.bat  # 测试模式启动
└── hplc_launcher.bat         # 启动器核心脚本
```

## 五、环境要求

- Windows 操作系统
- .NET Framework 4.8（构建并运行 C# 动态库）
- Python 3.x（建议 3.10+），并已加入系统 `PATH`
- Visual Studio（用于构建 C# 工程）

## 六、一键使用（打包版）

仓库 `dist/侦听台/` 是免安装打包产物（onedir，Git LFS 跟踪）：

1. 克隆仓库（需 Git for Windows 或单独安装 git-lfs，首次 clone 自动拉取 LFS 对象）；
2. 双击 `dist/侦听台/侦听台.exe`；
3. 浏览器自动打开 `http://127.0.0.1:8765/`，即可选择日志建立索引、查看帧详情与分钟分析；
4. 关闭 exe 所在控制台窗口即停止服务；索引数据库等运行时数据写入 exe 同目录 `runtime/`，不会污染仓库。

> 目标机要求：Windows 10/11（自带 .NET Framework 4.8），无需安装 Python。

## 七、构建与运行

1. **构建解析动态库**：用 Visual Studio 打开 `DLL.sln`（或 `NwHplcDll.sln`）生成工程，产物为 `dll\bin\Debug\GwHPLCAnalysis.dll`。
2. **启动服务**：双击 `启动解析工具.bat`（生产模式）或 `启动解析工具-测试模式.bat`（测试模式）。
3. **首次运行**：启动器会自动创建 Python 虚拟环境 `.venv` 并安装 `hplc_web/requirements.txt` 中的依赖，随后打开浏览器访问 `http://127.0.0.1:8765/`。
4. **停止服务**：关闭启动器窗口即可停止本地服务。
5. **重新打包 exe**：运行 `packaging\build_exe.bat`（需先按本节省 1-2 步准备 venv 与 DLL），产物输出到 `dist\侦听台\`。

> 说明：启动器会检测本地服务版本，若接口版本过旧会自动重启服务，无需手动干预。

## 八、API 概览

Web 服务默认监听 `127.0.0.1:8765`，主要接口：

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/version` | GET | 获取服务版本与接口 revision |
| `/api/fs/pick` | POST | 报文文件筛选 |
| `/api/logs/minute-analysis` | POST | 分钟报表数据分析 |
| `/api/serial/ports` | GET | 列出可用串口 |
| `/api/serial/status` | GET | 串口采集状态（frame_count/log_dir/log_file） |
| `/api/serial/start` | POST | 启动串口采集（默认 COM19/115200/N/8/1） |
| `/api/serial/stop` | POST | 停止串口采集 |
| `/openapi.json` | GET | OpenAPI 描述（完整接口列表） |

## 九、相关文档

详见仓库 `侦听台文档/` 目录：

- `侦听台上位机软件协议解析动态库接口说明.docx` —— 动态库接口说明
- `侦听台报文格式.docx` —— 报文格式说明
- `南网协议/` —— 南方电网《低压电力线宽带载波通信规约》数据链路层 / 应用层规范
- `国网协议/` —— 国网《双模通信互联互通技术规范》数据链路层 / 应用层规范

## 十、说明

- `测试文件/` 为本地大体积测试数据（含超过 100MB 的原始报文），已被 `.gitignore` 忽略，**不纳入版本库**。
- 本项目源码托管于 `git@github.com:wyentrepot/ZZT_SELF.git`。
