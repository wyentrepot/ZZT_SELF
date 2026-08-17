# 任务3 三源 Evidence 接入记录（2026-08-17）

> 本记录供查阅：任务 3 的三源 Evidence 适配（侦听台帧/模拟集中器步骤/loghooks 事件）内容、验证证据、决策记录。

## 1. 背景

- 任务 3 验收出口：**一个 Run 同时消费三源证据**；资源冲突可预测；迁移用例与旧工具结果一致。
- 三源：侦听台帧（listener）、模拟集中器步骤（sim_concentrator）、loghooks 事件。
- 契约：docs/03 骨架设计 §3（Evidence 模型）、§5（SourceAdapter 适配器）。

## 2. 新增代码

`libs/test_automation/sources.py`（三源 Evidence 适配）：

| 组件 | 输入 → 输出 |
|---|---|
| `listener_frame_evidence()` | 侦听台帧 `(sequence, log_time, hex_frame)` → Evidence(kind=frame, source=listener) |
| `simcon_step_evidence()` | sim_concentrator runner 步骤结果 dict → Evidence(kind=interaction, source=sim_concentrator) |
| `loghooks_event_evidence()` | loghooks Event dataclass → Evidence(kind=event, source=loghooks) |
| `SourceAdapterBase` | SourceAdapter 便捷基类（start/stop/health，可注入数据源） |
| `ListenerFrameAdapter` | 帧记录列表 → Evidence 列表（SourceAdapter 实现） |
| `SimConcentratorAdapter` | 步骤结果列表 → Evidence 列表 |
| `LoghooksEventAdapter` | Event 列表 → Evidence 列表 |

## 3. Evidence 字段映射

- `raw_ref`：`listener:seq:N` / `simcon:step:N` / `loghooks:<rule_id>`（可追溯锚点）
- `correlation_key`：sequence / step index / rule_id（关联键）
- `metadata.origin`：`listener.serial_capture` / `sim_concentrator.runner` / `loghooks.engine`
- `payload`：携带各源原始结构化字段（hex_frame 规范化大写、step 结果、event 字段）

## 4. 测试

`libs/test_automation/test_sources.py`（7 用例）：

```text
pytest libs/test_automation  → 58 passed（原 51 + 三源适配 7）
```

覆盖：三源纯函数字段映射、hex_frame 规范化、correlation_key、SourceAdapter 生命周期（start/collect/stop/health）、run_id 传播。

## 5. 验证证据

```text
pytest libs/test_automation            → 58 passed
全量回归（apps/* + libs/*）            → 432 passed / 66 skipped / 13 failed
13 failed 均为既知 WSL Windows 资源缺失（DLL/启动工具.bat/样本数据），与本次改动无关。
```

## 6. 任务状态更新（docs/04-任务安排.md）

- 任务 3 明细：加三源 Evidence 适配（sources.py），标注剩余 Run 级三源编排
- 推进重点注记：加三源适配
- 矩阵 FR-3/FR-4：加 LoghooksEventAdapter / SimConcentratorAdapter

## 7. 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 三源接入方式 | `libs/test_automation/sources.py` 统一适配器 | 三源数据形态不同但统一转 Evidence 契约 |
| 适配器形态 | 纯函数 + SourceAdapter 子类 | 可单测、不依赖真实串口，符合 docs/03 §5 |
| 帧规范化 | hex 去空格+大写 | 便于断言匹配与审计 |

## 8. 未完成/后续

- ~~任务 3：一个 Run 同时消费三源证据的编排~~ → **已落地（2026-08-17，见 §9）**
- `libs/loghooks` 既有引擎的 Event 直接 Evidence 化（当前用适配器包装，未改引擎本体）
- 迁移用例的机器可执行帧定义（当前 GW-CASS 为语义断言）
- 全量测试 13 个 WSL 失败项需 Windows + DLL 环境终验（既有基线）

## 9. Run 级三源编排接入（2026-08-17 补充）

> 任务 3 剩余项"一个 Run 同时消费三源证据的编排"已落地，本节约定其实现与验收证据。

### 9.1 新增代码

`apps/workbench/orchestration/evidence.py`（Run 级三源 Evidence 接入）：

| 组件 | 作用 |
|---|---|
| `collect_three_source_evidence()` | loghooks 事件 / sim_concentrator 步骤 / listener 帧 → 同一 run 级 `EvidenceStore`（sequence 单调，可 freeze） |
| `_dict_to_loghooks_event()` | `_scan_logs` 产出的 dict 事件 → `loghooks_event_evidence` 可消费（`getattr` 兼容） |
| `_evidence_to_store_sink()` | `SourceAdapter` sink 契约（`sink(ev)` 收 Evidence 对象）→ `EvidenceStore.append` 字段式契约桥接 |
| `acquire_serial_lease()` | 串口资源独占租约（`ResourceLeaseManager`），冲突抛 `ResourceConflictError` |
| `evidence_index()` | EvidenceStore → 可下钻索引 `{total, sources:{source:[raw_ref,...]}}` |

`apps/workbench/orchestration/runner.py`：`RunExecutor._run_steps` 接入三源收集——
monitor 事件 → Evidence、stimulus 步骤 → Evidence（stimulus 前取串口独占租约）、
listener 帧（`RunInput.extras["listener_frames"]`）→ Evidence；Report 新增
`evidence_index` / `evidence_frozen` / `sources.listener`。

`apps/workbench/orchestration/models.py`：`Report` 补 `evidence_index`、`evidence_frozen` 字段。

### 9.2 测试

`apps/workbench/orchestration/test_evidence.py`（11 用例）：

```text
pytest apps/workbench/orchestration/test_evidence.py        → 11 passed
pytest libs/test_automation libs/sim_concentrator apps/workbench → 150 passed
```

覆盖：三源同时消费进同一 store、dict 事件代理、freeze 拒绝追加、evidence_index
下钻、串口资源冲突可预测（独占冲突 / shared 共存）、RunExecutor 端到端 Report
含 evidence 字段、stimulus 步骤 → sim_concentrator Evidence。

### 9.3 验收出口对照

- ✅ **一个 Run 同时消费三源证据**：`collect_three_source_evidence` + `RunExecutor`
  端到端测试（monitor 事件 + listener 帧 + stimulus 步骤 → 同一 EvidenceStore）
- ✅ **资源冲突可预测**：`acquire_serial_lease` 独占冲突抛 `ResourceConflictError`，
  shared 离线文件可共存
- ⏳ **迁移用例与旧工具结果一致**：GW-CASS 迁移用例结果一致性仍属既有验证项，
  不在本次 Run 级接入范围

### 9.4 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| Run 级编排落点 | `apps/workbench/orchestration/evidence.py` + 复用 `test_automation` EvidenceStore/适配器 | 复用任务 1 领域契约，不重复实现 |
| listener 帧来源 | `RunInput.extras["listener_frames"]` 注入 | 符合 SourceAdapter 可注入数据源契约，不依赖真实 listener 运行时，可单测 |
| Evidence 写入 | sink 桥接（Evidence 对象 → append 字段） | 弥合 SourceAdapter 与 EvidenceStore 契约断层 |
| 模型变更 | `Report` 增字段（不替换现有 Run/Report） | 不破坏既有 orchestration 契约与测试 |
