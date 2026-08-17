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

- 任务 3：**一个 Run 同时消费三源证据的编排**（Run 级接入：把三源适配器接进 Run 执行流程）
- `libs/loghooks` 既有引擎的 Event 直接 Evidence 化（当前用适配器包装，未改引擎本体）
- 迁移用例的机器可执行帧定义（当前 GW-CASS 为语义断言）
- 全量测试 13 个 WSL 失败项需 Windows + DLL 环境终验（既有基线）
