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

## 3. G3 — canonical model 与 REST DTO 映射（🟡 实现收束，待 Terra 验收）

- [x] 新增 mapper round-trip、旧 JSON fixture、状态机、Report/Artifact 快照测试：mapper focused 27 passed。
- [x] Luna：完成 execution/audit canonical 对应 DTO/view 与显式 mapper。
- [~] Luna：完成 Artifact/Report/旧 schema 兼容层与 canonical Run→Store 接线；Report/Assertion/Artifact execution 接线待审。
- [~] Terra：待独立复核本轮 lifecycle/REST/兼容投影实现；在复核通过前不视为 G3 完成。
- [ ] 运行 test_automation serialization/state machine、orchestration contract、Workbench API 回归。
- [~] G3 验收：实现已收束，待 Terra/技术总管确认 runner 内部兼容状态与模型清理边界。

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

- [x] 初始检查点曾为 156 passed、2 failed、1 warning；本轮已完成最小修复并重跑。
- [x] G4 收束集实际为 167 passed、1 warning；但最终阶段门仍待 Terra/技术总管验收。


## 当前受控设备配置（用户确认，2026-08-28）

- CCO：COM9；本条仅记录串口角色与已完成 CCO IAP 验收，不覆盖 CCO 日志口既有波特率/校验位配置语义。
- STA：COM8。
- 侦听台：COM4，115200/E。
- 模拟集中器：COM19，9600/E。
- CCO 实机最小闭环已由技术总管完成：固件 iap_cco_AN_HUI_hv0201_sv002601_date260509_9600_E_FC_F8_isv090013_idate260513.bin，sha256 5daa35a95ee392e88bd79b22b34ad5c8a131b93a2c5d495be818e78620b8b9bb；应用验证 sversion=002601、AN_HUI_MODE；证据目录 /d/cco/001-cco/20260828-201717/。
- 本代理未打开串口、未发送数据、未执行烧录；STA/侦听台/模拟集中器仍待受控硬件验收。


## G4 收束回归（本轮，2026-08-28）

- [x] 两个原始 RED 已修复：legacy Report steps 兼容投影恢复 kind/result/detail；旧 Workbench Store round-trip 调用点迁移到 canonical Run/Report。
- [~] canonical Run 全链路尚未完成：本轮只验证现有 lifecycle/Store 兼容路径，runner 仍需迁移为 canonical Run/Report/StepResult 单一执行事实。
- [x] Store 旧公开写口已私有化，canonical Report/dict 门禁与 legacy projection 回归通过；旧报告提供单向兼容读取。
- [x] StepResult 兼容投影保留 skipped detail；未知异常对外使用受控摘要，不回显路径。
- [x] REST：RunRequest 入参、RunView/ReportView 出参；parameters/resource_leases/error/report_path 与 Artifact.path 不进入 REST 序列化。
- [x] 当前设备配置：CCO COM9、STA COM8、侦听台 COM4 115200/E、模拟集中器 COM19 9600/E；任务 JSON/离线夹具已同步。
- [x] G4 离线集：167 passed, 1 warning；新增黑盒/AST 6 passed；git diff --check、UTF-8 校验通过。
- [~] G3/G4 最终验收仍待 Terra/技术总管审核；本轮未提交、未推送、未打开真实串口。

### Terra 终审退回与本轮修正（2026-08-28）

- [x] Terra 指出的执行双事实、Store 旧写入口、REST 敏感字段/Artifact.path、generic 异常回显、skipped detail 五类可观察阻断已修复。
- [x] runner/store 现仅导入 libs/test_automation.models；legacy_models.py 已删除；models.py 仅保留 reporting 值对象。
- [x] 新增黑盒/AST 门禁 6 passed；完整 G4 离线集 167 passed、1 warning；diff check 与 UTF-8 校验通过。
- [~] 本轮只证明阻断修复和离线回归通过，不代表 G3/G4 最终验收；需 Terra 独立二次复核后方可进入真实硬件联调。


### 最新修正（2026-08-28，待 Terra 二次复核）
- [x] REST Artifact 列表改为只返回 ArtifactView 脱敏字段，彻底移除 `path` 键。
- [x] REST Artifact 下载对 ArtifactPathUnsafe/OSError 统一返回固定 `Artifact 不可访问`，不回显内部路径。
- [x] 黑盒回归覆盖不可访问 `/private/secret/x.log` 的列表与下载：7 passed；UTF-8 与 `git diff --check` 通过。
- [~] 上述修正仅完成 Luna 实施与本地验证，仍待 Terra 独立复核；G3/G4 未验收，未执行真实串口/烧录。


### 模拟集中器端口回归修正（2026-08-28）

- [x] 修正 libs/sim_concentrator 过时 COM24 断言：当前受控默认映射保持 COM19 / 9600 / E，POSIX 设备仍为 /dev/ttyUSB1。
- [x] 新增显式未映射端口覆盖回归：resolve_serial_config(port="COM24") 保留 COM24，不套用 simcon 默认映射。
- [x] 三个原始失败集已复跑：19 passed, 1 warning。
- [x] 按 PLAN.md G4 文件集复跑：180 passed, 1 warning；该文件集当前实际收集 180 项，不宣称为 184 项。
- [~] 可行全量 pytest libs apps/workbench -q 在约 30% 处 Python 进程 Aborted (core dumped)；已保留原始输出于 /tmp/zzt-full-regression.log，需总管/ Terra 继续定位，不能据此通过全量验收。

### G3/G4 最终验收（2026-08-28）

- [x] Terra 发布门批准：Runner/Store/API 仅使用 canonical execution/audit 模型；REST 不暴露 execution parameters、error、report_path 或 Artifact 真实路径。
- [x] 黑盒门禁：10 passed（包括 canonical 缺失回退和不可访问 Artifact 的脱敏路径）。
- [x] 全量离线：pytest libs apps/workbench -q 为 629 passed、77 skipped、1 个既有 Starlette/httpx 弃用警告。
  - 77 个 skip 是既有不支持协议样例、Mono/Python.NET 不满足安全前置条件和未配置 SIMCON_TEST_COM1/2 的真实串口测试；不是业务断言回归。
- [x] G4 通过；G5 保持另行排期。CCO 指定 IAP 已在 COM9 传输并验证 sversion 002601 / AN_HUI_MODE，证据 /d/cco/001-cco/20260828-201717/。
