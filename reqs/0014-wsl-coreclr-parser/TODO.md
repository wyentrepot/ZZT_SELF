# TODO — REQS-0014 国网 HPLC 解析库双目标构建

> 每次进度变更只追加不覆盖；完成项打 `[x]` 并写明验证证据。未经用户确认，不跨越阶段出口。

## 阶段 0 · 需求与基线

- [x] 新建 REQS-0014 并在 `REQS-INDEX.md` 登记；冻结 Windows `net48` 保持支持、WSL `net8.0 + CoreCLR` 原生解析、串口映射不改的边界（2026-08-31）
- [x] 在独立工作树 `codex/0014-wsl-coreclr-parser` 建立环境；主工作区未提交的 0013/UI 变更未带入（2026-08-31）
- [x] 校验主工作区 Debug DLL 并临时复制到工作树：SHA-256 `AE81768AAD7A5383AC73899CF51E021EFA30E9586E783B1534262E212BED8F8E`；文件受忽略规则保护，不纳入 Git（2026-08-31）
- [x] 使用只读 `HPLC_TEST_DATA_ROOT=D:\\2-侦听台改造\\测试文件` 复用历史夹具并执行基线；复现 `apps/listener/test_concurrent_meter_e2e.py::TestConcurrentMeterE2E::test_all_frames_enrich_nested_698` 失败，同行其余 3 项通过（2026-08-31）
- [x] **阶段出口（用户决策）**：用户于 2026-08-31 确认将上述失败登记为既有独立风险并继续 `net8.0` 开发；不得将其归因于本需求。

## 阶段 1 · C# 双目标与 JSON 契约

- [x] 建立 `GwHPLCAnalysis.Net8.csproj` 与 `Directory.Build.props`：`net8.0` 编译同一份 parser source，程序集名 `GwHPLCAnalysis`、根命名空间 `TestDll` 不变；`test_net8_project.py` 由 2 failed → 2 passed（2026-08-31）
- [x] 替换 `System.Web.Script.Serialization.JavaScriptSerializer` 的跨目标兼容实现：`JsonCompat` 在 `net48` 继续使用原序列化器，`net8.0` 显式 `IncludeFields=true`；所有原 `ScriptIgnore` 字段在 `net8.0` 改用 `JsonIgnore`（2026-08-31）
- [x] Windows 执行 `MSBuild libs/shared/dll/DLL_NwHPLCAnalysis.csproj /p:Configuration=Debug /p:BaseIntermediateOutputPath=obj\\net48\\`；输出 `libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll`，SHA-256 `1A4558F2A580A269C09C008800FC55909287E72DD6FBDA84DAF71D19A8A15656`（2026-08-31）
- [x] Windows 与 WSL 分别执行 `dotnet build libs/shared/dll/GwHPLCAnalysis.Net8.csproj -c Debug`；WSL 使用 `/root/.dotnet` 的 SDK `8.0.424`，输出 `bin/Debug/net8.0/GwHPLCAnalysis.dll`，SHA-256 `5CB2C348936AC74403D2439EAC6EF3D90EBE60F670B9DBE2A339E0CC018DC893`（2026-08-31）
- [x] **阶段出口**：双目标构建成功；Windows `test_net8_project.py`、`test_dotnet_parser.py`、`test_dll_python_meter_consistency.py` 共 **13 passed**；跨环境逐帧 JSON semantic diff 留待阶段 3（2026-08-31）

## 阶段 2 · Python.NET CoreCLR 与 DLL 选择

- [x] 先新增 Python 单测：Linux 在 `import clr` 前请求 CoreCLR；Windows 不改变既有 CLR 路径；无运行时时返回可诊断错误而非进程崩溃（23 passed）。
- [x] 修改 `libs/shared/dotnet_runtime.py`，将运行时探测和 CoreCLR 显式加载封装为可测试接口。
- [x] 修改 `libs/shared/dotnet_parser.py`，按平台选择 `net48` / `net8.0` DLL 并维持 `DotNetHplcParser` 三个既有 Python 方法签名。
- [x] 修改 `apps/listener/app.py`，由探测结果决定 parser 是否启用，不再以 `sys.platform != "win32"` 直接禁用；保留不可用时的 503 降级语义。
- [x] **阶段出口**：Windows 路径、Linux 路径和无运行时路径的单测均通过；真实 WSL CoreCLR 与 Windows 解析回归均通过。

## 阶段 3 · 跨环境差分与 listener 集成

- [x] 新增 golden corpus 生成/比对工具：同一原始帧分别由 Windows `net48` 与 WSL `net8.0` 输出，比较 JSON 语义结构并只对白名单环境字段豁免。
- [x] 覆盖现有最小国网帧、短物理块、E4 应用负载、中央信标后的载波帧及并发抄表样本（6 类）。
- [x] Windows 验证 `/api/version` 的 `dll_available=true` 与 `/api/parse` 200；WSL 做同样验证。
- [x] **阶段出口**：golden diff 为零；两环境 listener API 均通过。

## 阶段 4 · 交付与回归

- [x] 更新 WSL 开发说明：.NET 8 / pythonnet 安装前提、CoreCLR 验证命令、`net8.0` 构建命令和故障诊断。
- [x] 更新 Windows 构建说明：继续构建并使用 `net48`，Windows 打包只携带该目标所需资产。
- [x] 执行目标测试、全量 Python 回归、`git diff --check`；将阶段 0 的既有失败与本需求结果分开报告。
- [x] 将完成项和证据追加到 `DONE.md`；未暂存、未提交、未 push。

## 阶段 2-4 完成追加记录

- [x] 阶段 2：新增测试先失败后通过；Linux 显式 CoreCLR、Windows 默认 CLR、平台 DLL 选择和 listener 非 Windows 装配均已实现。目标回归 23 passed。
- [x] 阶段 3：新增跨进程 golden 工具，覆盖 6 类报文。Windows net48 与 WSL net8.0 的 simple/full JSON 为 equal，唯一白名单 version.date。
- [x] 阶段 3：Windows 与 WSL listener 均实测 version 200、parse 200、dll_available=true、ProType=GW。
- [x] 阶段 4：新增 RUNBOOK.md，明确 WSL .NET 8/Python.NET 前提、CoreCLR 自检、Windows net48 构建与打包边界。
- [x] 阶段 4：完整 pytest 已执行：1268 passed、68 skipped、4 failed。4 项均为 REQS-0014 之外的 listener/workbench UI 静态断言，详见 DONE.md。
- [x] 阶段 4：未暂存、未提交、未 push。
