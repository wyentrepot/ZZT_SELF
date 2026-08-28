# REQS.md — 需求 0007：架构规范解耦重构

> 状态：✅ G1-G4 已验收；CCO 烧录验证已完成，G5 全设备联调另行排期。
>
> 基线：`master @ 2b679b9`，2026-08-28 核查工作树干净。
>
> 本需求只在当前默认 WSL 环境中修改、测试和验证；本次用户已授权创建本地检查点提交并推送至当前 origin/master。

## 1. 目标

以**解耦**为第一目标，首期完成：

1. 移除 `loghooks → sim_concentrator.frame_codec` 的生产依赖；1376.2 日志帧统一经 `parser_lib` 公开契约解析。
2. Workbench 编排器只依赖监控与激励端口，不直接导入 `loghooks` 或 `sim_concentrator` 的具体实现。
3. 消除同名模型的“伪单一真相”：执行/审计领域模型与 REST/报告视图必须有明确归属、名称和转换器。

既有 REST API、任务 JSON 或报告 JSON 若阻碍解耦，可以重构；变更必须有迁移说明、契约测试和重新落地测试。不得以“兼容”为由保留跨层内部导入。

## 2. 已确认边界

- `parser_lib` 是协议解析的唯一公开入口；`sim_concentrator.frame_codec` 仅是模拟集中器内部的兼容/组合层。
- 首期仅执行离线单元与集成回归，不打开真实串口、不发送、不烧录。
- 离线门通过后可进行真实设备联调；每次执行前仍须记录实际端口枚举与操作范围。
- 用户给出的硬件预设：CCO `COM9`、STA `COM8`、侦听台 `CON4`（115200/E）、模拟集中器 `COM19`（9600/E）。`CON4` 是否为 `COM4` 必须在硬件阶段前核实，首期不得改写运行配置。
- 首期不包含：`shared` 拆包、三个大文件拆分、`pyproject` 包管理正规化。
- 不创建 Git 分支；本次已获用户授权创建本地检查点提交，仍不得推送远程。

## 3. 验收标准

- `libs/loghooks` 不再导入 `sim_concentrator`。
- `apps/workbench/orchestration/runner.py` 不再导入 `loghooks` 或 `sim_concentrator`；仅消费公开 ports。
- `RunStatus`、执行域 `Run/Evidence/AssertionResult/Artifact/Report` 与 Workbench DTO 的归属和映射由测试锁定。
- 受影响 API/JSON 均有明确版本或迁移策略、fixture/快照测试和变更说明。
- 全部首期离线回归通过；硬件联调另立阶段门和证据记录。
