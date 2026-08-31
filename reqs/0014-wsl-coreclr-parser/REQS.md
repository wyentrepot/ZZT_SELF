# REQS-0014：国网 HPLC 解析库双目标构建（Windows net48 + WSL net8.0 CoreCLR）

> 状态：✅ 阶段 0-4 已完成；Windows net48 与 WSL net8.0 CoreCLR 均已验收
>
> 分支：`codex/0014-wsl-coreclr-parser`
>
> 触发：用户要求 WSL 直接支持国网解析；确认串口映射已自行验证可用，并要求原 Windows 解析能力继续支持。

## 1. 目标

将 `libs/shared/dll/DLL_NwHPLCAnalysis.csproj` 演进为同一程序集名 `GwHPLCAnalysis` 的双目标库：

- Windows：继续提供现有 `.NET Framework 4.8`（`net48`）解析链，既有 Windows Python.NET 调用、桌面打包和解析结果不退化；
- WSL/Linux：提供 `.NET 8`（`net8.0`）解析链，Python 通过 CoreCLR 直接加载，不经 Windows 进程代理；
- 侦听台：按运行时选择对应 DLL，成功加载时保持 `/api/parse`、`/api/version` 与日志索引的深度解析能力。

## 2. 已冻结的边界

### 纳入

1. C# 解析库项目文件、跨目标 JSON 序列化兼容层及其构建产物定位。
2. `shared.dotnet_runtime`、`shared.dotnet_parser` 与 listener 的运行时选择和错误语义。
3. Windows `net48` 与 WSL `net8.0` 的同帧 JSON 契约差分测试。
4. WSL 开发依赖说明、验证命令与 Windows/WSL 双环境回归说明。

### 不纳入

1. 不将 C# 协议解析重写为 Python，不逆编译，不改变协议字段语义。
2. 不使用 Mono、Wine 或 Windows 解析 HTTP 代理作为 WSL 的解析实现。
3. 不改串口映射、串口采集状态机、烧录流程、页面业务逻辑或 Windows EXE 打包流程。
4. 不提交 DLL、PDB、历史报文或其他忽略构建/测试资产。

## 3. 兼容性契约

| 契约 | Windows `net48` | WSL `net8.0` |
|---|---|---|
| 程序集 / 命名空间 / 外部类 | `GwHPLCAnalysis` / `NW.NwHPLCAnalysis` | 完全相同 |
| 外部方法 | `GetProtocolVersion`、`GetProtocolSimpleDesc`、`GetProtocolFullDesc`、FCH/MAC/Beacon 方法 | 完全相同 |
| JSON 结果 | 保留公共字段、字段名、数值、`null`、`ScriptIgnore` 的隐藏语义 | 对同一输入解析为语义相等 JSON |
| Python 调用 | 继续使用 Windows CLR | Linux 在导入 `clr` 前选择 CoreCLR |
| listener 可用性 | `dll_available=true` | CoreCLR 和 `net8.0` DLL 可用时为 `true`；缺失时保持可诊断降级 |

## 4. 验收标准

1. Windows 上 `net48` 构建成功，现有 `DotNetHplcParser` 解析集成测试保持通过。
2. WSL 上 `dotnet build -f net8.0` 成功，Python.NET 以 CoreCLR 加载同名程序集并能执行 `version`、`parse_simple`、`parse_full`。
3. 对固定 golden corpus 的每帧，Windows `net48` 与 WSL `net8.0` 的 simple/full JSON 经解析后严格相等；版本日期等环境生成字段单独白名单说明。
4. WSL 下 listener `/api/version` 返回 `dll_available=true`，`/api/parse` 返回 200；Windows 同一路径继续可用。
5. `pytest` 回归中新增的跨平台单测、Windows 集成测试、WSL 集成测试均有可复现命令和结果记录。
6. 当前已确认的既有基线失败必须与本需求结果分离，不得归因于本需求。

## 5. 风险与决策门

| 项 | 风险 / 处理 |
|---|---|
| JSON 兼容 | 原实现使用 `JavaScriptSerializer`、公开字段与 `[ScriptIgnore]`；不得直接改用默认 `System.Text.Json`，需先以 golden diff 锁定行为。 |
| 构建迁移 | 当前为旧式 `net48` csproj，需保留 Windows 构建入口并为 `net8.0` 提供可在 WSL 构建的项目配置。 |
| Python.NET | CoreCLR 必须在 `import clr` 之前选择；不能仅删除非 Windows 保护。 |
| 既有基线 | 2026-08-31 已复现 `apps/listener/test_concurrent_meter_e2e.py::test_all_frames_enrich_nested_698` 失败（其余同文件 3 项通过）；在获准带病推进前不改生产代码。 |
| 资产可用性 | `测试文件/` 与 DLL 构建产物被 Git 忽略；工作树测试仅可只读使用明确配置的夹具根，DLL 必须重建或 SHA-256 校验后临时复制，绝不提交。 |
| 阶段 1 构建证据 | 2026-08-31：Windows `net48` 与 Windows/WSL `net8.0` 均构建成功；Windows 解析回归 13 passed。已知 `sniffer` 小写类型名编译警告为既有命名警告，不影响输出。 |

## 6. 变更记录

### 变更 2 ｜ 2026-08-31 ｜ 实施完成

- 阶段 2：WSL/Linux 在导入 clr 前选择 CoreCLR，并定位 net8.0 DLL；Windows 保持原 net48 路径。
- 阶段 3：6 类固定报文的 Windows/WSL simple/full JSON diff 为零；唯一白名单是 version.date。
- 阶段 4：新增 RUNBOOK.md；全量 pytest 为 1268 passed、68 skipped、4 failed。4 项均为无关 UI 静态断言，详见 DONE.md。

### 变更 1 ｜ 2026-08-31 ｜ 用户

- **改成什么**：新增 WSL 原生解析支持，采用 C# 库增加 `net8.0` 并由 Python.NET CoreCLR 加载；Windows `net48` 解析必须同步继续支持。
- **为什么**：WSL 串口映射已验证，现有解析库受 `.NET Framework` / Python.NET 平台边界限制。
- **影响**：`libs/shared/dll/`、`libs/shared/`、`apps/listener/`、解析测试与 WSL 开发文档；不影响串口映射实现。
- **被取代**：原“WSL 仅做纯逻辑，C# 解析库留 Windows”的设计边界，仅在解析库运行时维度被本需求取代。
