# TODO.md — 需求 0007 架构规范解耦重构

> 状态：🟡 执行中（G3 未验收（Terra 复核退回；检查点提交））。进度只以本文件勾选项和对应测试证据为准。
>
> 执行纪律：当前默认 WSL 内编辑和验证；本次用户授权创建本地检查点提交并推送当前 origin/master；首期禁止自动打开真实串口、发送或烧录。

## 0. 设计与基线

- [x] 确认首期范围：loghooks 解耦、编排 ports、模型单一真相。
- [x] 确认解耦优先于既有 REST/JSON 兼容；变更必须有迁移与重新测试。
- [x] 确认 parser_lib 为 1376.2 解析唯一公开入口。
- [x] 确认首期离线验证；记录后续硬件联调授权和待核实的 CON4/COM4。
- [x] 完成只读依赖与模型审计，保存证据至 DESIGN.md。
- [x] 建立需求、设计和任务进度文档。
- [x] G0：用户已批准 DESIGN.md，允许派发代码实现。

## 1. G1 — parser facade 与 loghooks 解耦（✅ 已验收）

- [x] 新增 parser facade 契约测试：hex/bytes、嵌套 645/698、无效输入及现有 JSON 形状等价（Luna RED 后完成）。
- [x] 新增 loghooks concentrator source 与禁止 simcon import 边界测试（Luna RED 后完成）。
- [x] Luna：完成 facade 与 loghooks 依赖替换，并修复 Terra 的纯委托/真实嵌套/docstring 阻塞。
- [x] Terra：二次独立审查通过；纯 facade、真实嵌套、生产依赖边界与 23 项离线回归均合格。
- [x] 运行 parser、loghooks、simcon codec/runner 离线回归：23 passed（Luna）。
- [x] G1 验收：架构扫描确认 libs/loghooks 无 sim_concentrator 生产导入。

## 2. G2 — 编排 ports 与组合层（✅ 已验收）

- [x] 完成 fake MonitorPort/StimulusPort 注入与端口结果字段测试：RED 已证实，ports 与集成测试 8 passed。
- [x] Luna：完成无框架 ports/request/result/error 与具体 adapters/组合根。
- [x] Luna：RunExecutor 强制显式 ports 注入；composition 成为唯一默认接线，AST 边界测试已通过。
- [x] Terra：独立审查通过；ports/组合根/异常边界/资源租约/AST import 均符合设计。
- [x] 运行 orchestration、Workbench API、profile loading、simcon FakeIO 回归：59 passed，1 个既有弃用警告。
- [x] G2 验收：AST 确认 runner 无具体 adapters/loghooks/sim_concentrator import；8+59 项离线回归通过。

## 3. G3 — canonical model 与 REST DTO 映射（🟡 Luna 兼容迁移中）

- [x] 新增 mapper round-trip、旧 JSON fixture、状态机、Report/Artifact 快照测试：mapper focused 27 passed。
- [x] Luna：完成 execution/audit canonical 对应 DTO/view 与显式 mapper。
- [~] Luna：完成 Artifact/Report/旧 schema 兼容层与 canonical Run→Store 接线；Report/Assertion/Artifact execution 接线待审。
- [~] Terra：正在审查 canonical Run→Store 接线与 scenario fingerprint；随后定义剩余 execution/audit 接线。
- [ ] 运行 test_automation serialization/state machine、orchestration contract、Workbench API 回归。
- [ ] G3 验收：同名模型不再表达不同语义，所有转换由测试锁定。

## 4. G4 — 离线集成验收（技术总管审核）

- [ ] Luna 汇总目标测试命令与原始输出。
- [ ] Terra 复核依赖边界、迁移文档、失败用例处理。
- [ ] 技术总管审核 diff、git diff --check、UTF-8 校验和测试证据。
- [ ] G4 通过后才可排期真实设备联调。

## 5. G5 — 真实设备联调（另行执行）

- [ ] 只读枚举并核实侦听台端口写法：CON4 或 COM4。
- [ ] 逐项记录 CCO COM9、STA COM8、侦听台、模拟集中器 COM19 的实际配置和独占状态。
- [ ] 按最小闭环执行联调并保存原始日志/报告。
- [ ] 不执行烧录，除非用户在该次操作单独确认。

## 6. 当前检查点状态（2026-08-28）

- [x] G1：Terra 独立复核通过；parser facade 与 loghooks 解耦已验收。
- [x] G2：Terra 独立复核通过；Runner ports 注入与 composition 唯一默认接线已验收。
- [~] G3：**未验收，Terra 对 B/D 切片退回**。完整 scenario fingerprint、DTO/mapper 和部分 Store 兼容投影已有测试覆盖，但不能视为 execution/audit 单一真相落地。
  - lifecycle、generic error 与 resource leases 没有在执行期间持续写回 canonical Run/SQLite；finished_at 也未形成真实结束时间。
  - Store 仍保留旧 Run 写入分支和 dict Report 回退，未实现 canonical-only 写入。
  - generic error 使用 str(exc)，存在路径等内部信息外泄风险；canonical StepResult 状态/详情映射也未满足契约。
  - runner/api 仍同时使用旧 Workbench 执行模型；RunRequest/RunView/ReportView 的 REST 边界迁移及旧同名模型清理尚未完成。
- [ ] 下一切片：先补 canonical Run 生命周期、lease/error 持久化和 Store 拒绝旧写入的 RED/GREEN；随后只经 mapper 迁移 REST 边界、移除重复执行模型，并重新执行 G3/G4 离线回归。

### 本次完整离线回归证据（2026-08-28）

- [~] 执行 15 个首期相关测试文件：156 passed, 2 failed, 1 warning；G4 不通过，不能转入硬件阶段。
  - apps/workbench/orchestration/test_ports_integration.py::test_runner_injects_fake_ports_and_preserves_results：Report steps 不再提供旧断言所需的 kind 字段。
  - apps/workbench/test_orchestration.py::test_store_roundtrip：旧 Workbench Run 被 Store 的 canonical-only 写入门禁拒绝。
- [ ] 下一切片必须先以这两项为 RED 基线：明确 REST/legacy Report projection 的兼容字段策略，并把旧 Run 调用点完成 canonical mapper 迁移；修复后重跑完整离线集。
