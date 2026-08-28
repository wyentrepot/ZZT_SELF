# DESIGN.md — 需求 0007 首期架构规范解耦设计（草案）

> 状态：✅ 已批准，G1 已验收，G2 已验收，G3 未验收（Terra 复核退回；检查点提交）
>
> 本草案依据 `架构分析报告.md`、`docs/03-骨架设计.md`、ADR-3/ADR-5 和 2026-08-28 源码只读核查形成。未批准前不得实现。

## 1. 设计目标与非目标

### 目标

- 以可替换的公开契约替代跨库内部导入。
- 编排业务依赖抽象 ports，具体实现只在组合层接线。
- 每个数据语义只有一个 canonical model；REST/UI 只用明确命名的 DTO/view。
- 每个迁移步骤先有失败的契约测试，再有最小实现；首期只使用 FakeIO、临时目录和 SQLite。

### 非目标

- 不进行 `shared`、`ai_operations.py`、`adapter_698`、`module_serial_service.py` 或包管理重构。
- 不把 Workbench 报告粗暴替换成 `test_automation.Report`。
- 不在自动测试中打开 `COM9/COM8/CON4/COM19`，不发送、不烧录。
- 不改变 1376.2 单 68 帧和 Profile 语义（ADR-3、ADR-5）。

## 2. 当前证据

| 事实 | 证据 |
|---|---|
| loghooks 直接导入 sim_concentrator 内部 codec | `libs/loghooks/sources.py:154-204`，尤其 :189 |
| simcon codec 是 parser_lib 的薄包装 | `libs/sim_concentrator/frame_codec.py:23-30,99-137` |
| runner 直接导入具体监控/激励实现 | `apps/workbench/orchestration/runner.py:130-228` |
| RunStatus 已是 canonical enum | `apps/workbench/orchestration/models.py:19,52-55`；`libs/test_automation/models.py:30-52` |
| 两个 Run/Report 的字段与职责不同 | Workbench `models.py:34-156`；执行域 `libs/test_automation/models.py:211-444` |

## 3. 方案选择

| 路径 | 优点 | 风险 | 结论 |
|---|---|---|---|
| A. Workbench 模型直接替换为 test_automation 模型 | 表面文件更少 | 丢失报告 sources/feedback、Artifact 字段等语义 | 不采用 |
| B. 保留重复模型，仅补注释 | 改动最小 | 双源事实与内部导入仍在 | 不采用 |
| C. 领域 canonical + 显式 DTO/mapper + ports | 可渐进迁移、可替换、可测试 | 新增少量转换代码 | **采用** |

## 4. 目标边界

```text
REST / CLI / UI
        │  RunRequest、RunView、ReportView（DTO）
        ▼
Workbench composition root
        │ 注入具体适配器
        ▼
RunExecutor ──▶ MonitorPort / StimulusPort
        │                 │
        │                 ├── LoghooksMonitorAdapter ─▶ loghooks public API
        │                 └── SimconStimulusAdapter  ─▶ sim_concentrator public API
        ▼
test_automation canonical models
Run / Evidence / AssertionResult / Artifact / Report / RunStatus

loghooks concentrator source ─▶ parser_lib.protocol_13762 public facade
sim_concentrator.frame_codec ─▶ parser_lib（仅 simcon 内部组合）
```

依赖规则：

1. `parser_lib` 不得依赖 `loghooks`、`sim_concentrator`、Workbench 或 FastAPI。
2. `loghooks` 不得导入 `sim_concentrator`；只调用 parser facade。
3. `RunExecutor` 不得导入具体领域库；只接受 ports 和 canonical model/result。
4. 具体适配器只能位于 Workbench 组合/基础设施边界，禁止泄露 `Engine`、`Event`、串口句柄或 `SerialIO` 给 runner/API。
5. REST/CLI 不得直接序列化执行域对象；必须通过显式 mapper 得到 view。

## 5. 契约设计

### 5.1 1376.2 parser facade

新增 `libs/parser_lib/protocol_13762.py`，并从 `parser_lib` 顶层显式导出稳定入口：

```python
def decode(request: dict) -> dict:
    # request: {"frame": "68 ... 16"} 或 {"frame_bytes": [0, ...]}
    # result: {"action": "parse", "ok": bool, "structure": ..., "raw_hex": ...,
    #          "fields": ..., "items": ..., "nested": ..., "warnings": ...}
```

- 输出形状以现有 `adapter_10376.decode_frame_json` 为基准；适配器内部类不是调用方契约。
- `parse_concentrator_10376()` 保留“日志文本 → 大写空格 hex → ParsedLine”职责，只通过 facade 传 `{"frame": normalized_hex}`。
- 无效帧、非 hex 或解析异常必须转为 `ok=false`/无 fields 的可诊断结果；不能使 loghooks 扫描中断。
- 删除 loghooks 对 `hex_to_bytes`/`decode_frame` 的依赖；不得把 simcon codec 原样搬入 parser_lib。

### 5.2 编排 ports

在 `libs/test_automation/ports.py` 定义无 UI、无框架类型的请求、结果、异常和 Protocol：

```python
class MonitorPort(Protocol):
    def scan(self, request: MonitorRequest) -> MonitorResult: ...

class StimulusPort(Protocol):
    def execute(self, request: StimulusRequest) -> StimulusResult | None: ...
```

- `MonitorRequest`：`log_dir, rules, run_id`；结果保留 files、events、evidence、summary、drift、total_lines、unmatched。
- `StimulusRequest`：语义 task 或 task_file、资源身份；结果保留现有 task/steps/summary 的必要 JSON 数据。
- `apps/workbench/orchestration/adapters/` 实现 Loghooks 和 Simcon 适配器；只有这里可导入具体模块。
- `RunExecutor` 通过构造函数接收 ports；Workbench composition root 提供默认实现，测试只注入 fake ports。
- 适配器把底层异常转换为稳定 code/message/details 的端口错误；runner 转换为步骤结果和报告证据。

### 5.3 模型单一真相

- 保持 `test_automation.models.RunStatus` 为唯一状态枚举。
- 执行/审计域 canonical：`Run`、`Evidence`、`AssertionResult`、`Artifact`、`Report`。
- Workbench 层改为语义明确的 `RunRequest`、`RunView`、`AssertionView`、`ArtifactView`、`ReportView`；不再与领域对象同名。
- 新增 mapper，明确并测试 `run_id ↔ id`、`scenario_id ↔ case_id`、`result ↔ outcome`、`ArtifactInfo ↔ Artifact`，以及不可丢失的审计字段。
- `RunInput` 是 API/编排入口 DTO，不伪装成领域 `Run`；先映射到 CasePackage/执行上下文。
- 若外部 JSON 不兼容，新增显式版本路径或迁移器；不得静默改变字段语义。

## 6. 错误、数据与硬件边界

- parser facade：协议不支持/输入无效可返回结构化失败；程序错误不得被静默吞掉。
- ports：底层异常不得以 `Engine`、串口对象或本机绝对路径形式泄露。
- runner：证据缺失与执行错误继续区分为 inconclusive/error/fail，并经 canonical result 记录。
- 首期所有端口实现必须可由 fake 替换，测试中禁止使用真实串口。
- 硬件阶段以用户最新确认值为准：CCO COM9、STA COM8、侦听台 CON4（待核实是否 COM4）115200/E、模拟集中器 COM19 9600/E；打开前先枚举并更新专门证据。

## 7. 阶段门与验收

1. **G0 设计审批**：本设计、范围、API 演进规则获用户确认。
2. **G1 parser 解耦**：先写 facade/loghooks 失败测试；通过 parser、loghooks、simcon codec 回归与“禁止导入 sim_concentrator”架构测试。
3. **G2 ports 解耦**：先写 fake-port 注入和 runner 契约测试；通过 Workbench 编排/API 回归，确认 runner 无具体实现导入。
4. **G3 模型收敛**：先写 mapper round-trip、JSON fixture、状态机和报告快照测试；完成 DTO 改名/映射和迁移说明。
5. **G4 离线集成验收**：全部测试、`git diff --check`、UTF-8 校验、依赖边界扫描通过。
6. **G5 真实设备联调**：仅在 G4 后执行；先确认 CON4/COM4，再分设备最小闭环并保存证据。烧录不在本需求自动执行。

## 8. 角色与实时治理

- 技术总管：维护 REQS/DESIGN/TODO、批准阶段门、派发任务、审查 diff 和测试证据；不直接实施业务代码。
- Luna：主实现代理，按 G1→G3 以 TDD 完成最小代码改动。
- Terra：独立契约/依赖审查与回归证据复核；不替代 Luna 的实施。
- 每项开始、完成、阻塞和验证结果必须同步更新 `TODO.md`；设计变化必须先更新本文件并重新评审。

## 9. 设计变更记录

| 时间 | 变更 |
|---|---|
| 2026-08-28 | 创建草案；范围和三项关键边界已获用户确认；等待本蓝图批准。 |

| 2026-08-28 | 用户批准首期蓝图；ADR-6 生效，开始按 PLAN.md 受控派发。 |

| 2026-08-28 | G1 已派发 Luna：按 RED→GREEN→REFACTOR 实施 parser facade 与 loghooks 解耦；Terra 等待独立复核。 |

| 2026-08-28 | Luna 完成 G1 实现：RED 已证实缺 facade 与旧内部导入；目标离线回归 23 passed，等待 Terra 独立审查。 |

| 2026-08-28 | Terra G1 独立复核：23 passed 但发现 3 项阻塞（facade 非纯委托、嵌套测试无真实嵌套、生产 docstring 残留 simcon）；已退回 Luna 最小修正，G2 未启动。 |

| 2026-08-28 | G1 已验收：Terra 二次复核通过，23 项目标离线测试全绿；获用户授权后转入 G2 Luna ports/组合层实施。 |

| 2026-08-28 | G2 检查点：ports RED 已证实、ports 单测 4 passed；集成两失败定位为新测试使用 apps/scenarios 而非 apps/workbench/scenarios，Luna 仅修正 fixture 路径后重跑。 |

| 2026-08-28 | G2 设计复核退回：Runner 默认构造仍导入具体 adapters，违反组合根唯一接线；静态门禁由文本 grep 收敛为 AST import 测试，报告 JSON 的 sim_concentrator 语义键保留至 G3。 |

| 2026-08-28 | Luna 完成 G2 架构修正：runner 强制显式 ports 注入，composition 为唯一默认接线；AST 门禁 4 passed，G2 回归 59 passed，进入 Terra 审查。 |

| 2026-08-28 | G2 已验收：Terra 独立复核通过；ports 8 passed、编排/API 59 passed，组合根唯一接线与 AST import 边界满足设计；进入 G3 Luna 实施。 |

| 2026-08-28 | G3 检查点：DTO/mapper 与 focused 98 项回归通过；Runner/Store/API 尚未迁移 canonical execution/audit model，避免破坏 REST/SQLite 契约，暂停写入并交 Terra 做字段矩阵审查。 |

| 2026-08-28 | Terra 字段矩阵确认无损路径，ADR-7 生效：canonical 审计模型经兼容投影接入，旧 REST/SQLite 字段保留；G3 转入 Luna 迁移实施。 |

| 2026-08-28 | G3 存储兼容层通过：Artifact.size、旧 schema additive migration、legacy ReportView 投影和 101 项相关回归通过；Runner/API canonical 接线仍未完成，拆分为下一受控切片。 |

| 2026-08-28 | G3 Runner/Store canonical Run 兼容接线完成：focused 9 passed、全相关回归 141 passed；canonical Report/Assertion/Artifact 与完整 scenario fingerprint 尚待 Terra 审查后继续。 |

| 2026-08-28 | G3 A/C 前置收敛通过：完整 scenario fingerprint 与 Store canonical-only write 的 RED/GREEN 已锁定（11+71 passed）；B/D/E/F execution/report/API 接线待下一切片。 |

| 2026-08-28 | G3 B/D Terra 复核退回：canonical lifecycle/error/resource lease 未持久化，Store 仍有旧模型/dict 写入旁路，generic error 可能泄露内部路径，StepResult 映射及 REST DTO/API/旧模型收敛未完成。G3 保持未验收，仅作为本地检查点提交。 |

| 2026-08-28 | 检查点全相关离线集实际为 156 passed、2 failed、1 warning：legacy Report step kind 投影缺失，旧 Workbench Run 调用点被 canonical-only Store 拒绝。两项均归入 G3 未验收迁移，不得进入 G4/G5。 |
