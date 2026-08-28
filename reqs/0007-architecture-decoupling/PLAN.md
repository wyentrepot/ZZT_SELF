# PLAN.md — 需求 0007 受控实施计划

> 状态：✅ G1-G4 已验收；CCO 烧录验证已完成，G5 全设备联调另行排期。
>
> 目标：在不触发真实硬件动作的前提下，完成 parser facade、编排 ports 和 canonical model/DTO 的首期解耦。
>
> 环境：所有命令在当前默认 WSL 的 `/01-workfile-ai/01-zzt/ZZT_SELF` 执行；使用 `./.venv/bin/python3`；本次用户已授权检查点提交并推送当前 origin/master；端口打开、发送和烧录仍不在本计划内。

## 文件与责任总表

| 文件 | 责任 |
|---|---|
| `libs/parser_lib/protocol_13762.py` | 1376.2 公开纯函数 facade：`decode(request) -> dict` |
| `libs/parser_lib/__init__.py` | 显式导出稳定 facade 名称 |
| `libs/parser_lib/test_protocol_13762.py` | facade 输入、输出和错误契约 |
| `libs/loghooks/sources.py` | 日志文本归一后调用 facade，不导入 simcon |
| `libs/loghooks/test_sources_concentrator.py` | concentrator source 行为回归 |
| `libs/loghooks/test_dependency_boundary.py` | 禁止 loghooks 生产导入 sim_concentrator |
| `libs/test_automation/ports.py` | Monitor/Stimulus requests、results、errors 与 Protocol |
| `libs/test_automation/test_ports.py` | ports 的无框架数据契约 |
| `apps/workbench/orchestration/adapters/monitor.py` | LoghooksMonitorAdapter 的唯一具体 loghooks 调用点 |
| `apps/workbench/orchestration/adapters/stimulus.py` | SimconStimulusAdapter 的唯一具体 simcon 调用点 |
| `apps/workbench/orchestration/composition.py` | 默认 adapters 的组合根 |
| `apps/workbench/orchestration/runner.py` | 只接收 ports；不再直接导入具体领域实现 |
| `apps/workbench/orchestration/test_ports_integration.py` | fake-port 注入、异常归一与边界测试 |
| `apps/workbench/orchestration/dto.py` | RunRequest/RunView/AssertionView/ArtifactView/ReportView |
| `apps/workbench/orchestration/mappers.py` | execution/audit canonical model 与 Workbench DTO 的显式映射 |
| `apps/workbench/orchestration/models.py` | 迁出或删除与 canonical 冲突的同名 DTO；只保留无对应领域语义的比较值对象 |
| `apps/workbench/orchestration/store.py`、`apps/workbench/api.py` | 通过 mapper/store 边界接收 DTO 或 canonical entity，不直接混用 |
| `apps/workbench/orchestration/test_model_mapping.py` | mapper round-trip、字段损失与旧 JSON fixture |
| `apps/workbench/test_app.py` | REST 端点和版本/迁移响应快照 |

## G1 — parser facade 与 loghooks 解耦

- [ ] **Luna：先创建失败测试。** 修改 `libs/parser_lib/test_protocol_13762.py`，覆盖 `{"frame": "..."}`、`{"frame_bytes": [...]}`、嵌套帧与非法输入；同一输入必须与 `adapter_10376.decode_frame_json` 的成功输出形状一致。修改 `libs/loghooks/test_sources_concentrator.py` 断言普通 hex、带时间戳、嵌套帧、callback 和无效帧的既有行为。新增 `libs/loghooks/test_dependency_boundary.py`，用 AST 检查 `libs/loghooks` 的生产 Python 文件没有 `sim_concentrator` import。

  - 前置命令：`./.venv/bin/python3 -m pytest libs/parser_lib/test_protocol_13762.py libs/loghooks/test_sources_concentrator.py libs/loghooks/test_dependency_boundary.py -q`
  - 预期失败：`ModuleNotFoundError: parser_lib.protocol_13762`，且旧 `sources.py` 触发禁止 sim_concentrator 导入断言。
  - 失败处理：若基线存在无关失败，记录完整输出并停止，不修改既有断言以掩盖失败。

- [ ] **Luna：最小实现 facade。** 新建 `libs/parser_lib/protocol_13762.py`，仅委托 `parser_lib.adapters.adapter_10376.decode_frame_json`；函数名固定为 `decode(request: dict) -> dict`。在 `libs/parser_lib/__init__.py` 用不冲突的显式导出名 `decode_13762` 导出该函数。facade 不得包含 IO、simcon、loghooks 或 FastAPI 导入。

  - 验证命令：`./.venv/bin/python3 -m pytest libs/parser_lib/test_protocol_13762.py -q`
  - 预期成功：所有 facade 输入、输出和错误测试通过。

- [ ] **Luna：替换 loghooks 内部依赖。** 修改 `libs/loghooks/sources.py` 的 `parse_concentrator_10376`：保留现有文本提取、callback 优先级、`ParsedLine` 字段和“解析失败不打断扫描”的行为；无 callback 时调用 `parser_lib.protocol_13762.decode({"frame": frame_hex})`，只在 `ok=true` 时写入 fields。删除 `sim_concentrator.frame_codec` 导入。

  - 验证命令：`./.venv/bin/python3 -m pytest libs/loghooks/test_sources_concentrator.py libs/loghooks/test_dependency_boundary.py libs/sim_concentrator/test_frame_codec.py libs/sim_concentrator/test_runner.py -q`
  - 预期成功：目标测试全绿，且 simcon 的原有 codec/runner 行为不变。

- [ ] **Terra：G1 独立审查。** 只读检查 facade 是否是 parser_lib 的唯一公开入口、loghooks 是否已无 simcon 生产导入、错误结果是否稳定且不存在反向依赖。

  - 复核命令：`/usr/bin/grep -RIn --include='*.py' 'sim_concentrator' libs/loghooks`
  - 预期成功：命中只允许测试文件；生产文件零命中。
  - 检查点：技术总管审查 Terra 证据、Luna diff 与 G1 测试输出后，才允许 G2。

## G2 — 编排 ports 与组合层

- [ ] **Luna：先创建 ports 和 fake 注入失败测试。** 新建 `libs/test_automation/test_ports.py` 与 `apps/workbench/orchestration/test_ports_integration.py`。测试固定以下公共形状：`MonitorRequest(log_dir, rules, run_id)`、`MonitorResult(files, events, evidence, summary, drift, drift_list, total_lines, unmatched)`、`StimulusRequest(task, task_file, resource_id)`、`StimulusResult(payload)`，以及含 `code/message/details` 的 `PortError`。测试向 `RunExecutor` 注入 fake ports，断言 monitor/stimulus 请求和回传事件/steps 不丢失，并断言底层异常不会泄露对象或绝对路径。

  - 前置命令：`./.venv/bin/python3 -m pytest libs/test_automation/test_ports.py apps/workbench/orchestration/test_ports_integration.py -q`
  - 预期失败：缺少 `test_automation.ports`，且现有 `RunExecutor` 不接受 ports 注入。
  - 失败处理：不调用真实 simcon；所有刺激结果由 fake payload 提供。

- [ ] **Luna：定义 ports。** 新建 `libs/test_automation/ports.py`，用 dataclass/Protocol 和现有 `AdapterError` 风格定义上述请求、结果、`MonitorPort.scan`、`StimulusPort.execute` 与 `PortError`。字段只能是标准值、dict/list、Path 或 canonical Evidence；不得引入 Pydantic、FastAPI、Engine、Event、SerialIO。

  - 验证命令：`./.venv/bin/python3 -m pytest libs/test_automation/test_ports.py -q`
  - 预期成功：所有 ports 默认值、不可变输入和错误结构测试通过。

- [ ] **Luna：实现具体适配器与组合根。** 新建 `apps/workbench/orchestration/adapters/__init__.py`、`monitor.py`、`stimulus.py` 和 `composition.py`。把当前 `runner._scan_logs` 的 loghooks 调用迁至 `LoghooksMonitorAdapter.scan`；把 `runner._run_stimulus` 的 task 路径解析与 simcon 调用迁至 `SimconStimulusAdapter.execute`。仅 adapters 可导入 `loghooks`/`sim_concentrator`；把异常转换为 `PortError`。

  - 验证命令：`./.venv/bin/python3 -m pytest apps/workbench/orchestration/test_ports_integration.py apps/workbench/orchestration/test_evidence.py apps/workbench/orchestration/test_profile_loading.py -q`
  - 预期成功：fake 与默认 adapters 通过；profile 的 FakeIO 路径不打开串口。

- [ ] **Luna：注入 runner。** 修改 `apps/workbench/orchestration/runner.py`：构造器接收 monitor_port/stimulus_port；删除具体模块延迟导入及 `_scan_logs/_run_stimulus`；资源租约仍由 runner 管理。修改 `apps/workbench/api.py` 的 `_executor()` 经 `composition.py` 构建一次默认 executor。

  - 验证命令：`./.venv/bin/python3 -m pytest apps/workbench/orchestration/test_ports_integration.py apps/workbench/orchestration/test_evidence.py apps/workbench/orchestration/test_profile_loading.py apps/workbench/test_app.py -q`
  - 预期成功：目标回归全绿；REST 状态/取消语义仍受测试覆盖。

- [ ] **Terra：G2 独立审查。** 检查 runner 只导入 ports/canonical model、组合根是唯一具体 adapters 接线点，端口不泄露 Engine/Event/SerialIO，资源租约仍在 runner。

  - 复核命令：`./.venv/bin/python3 -m pytest apps/workbench/orchestration/test_ports_integration.py -q`（其中 AST 用例检查 runner imports）。
  - 预期成功：AST 只允许 runner 导入 ports/canonical model；报告 JSON 字段名不作为依赖命中。
  - 检查点：技术总管审查注入路径、Terra 证据和完整 G2 测试后，才允许 G3。

## G3 — canonical model 与 Workbench DTO 映射

- [ ] **Luna：先创建映射失败测试。** 新建 `apps/workbench/orchestration/test_model_mapping.py`，以固定 fixture 断言：`run_id ↔ id`、`scenario_id ↔ case_id`、`result ↔ outcome`、`ArtifactInfo ↔ Artifact`；canonical Run 的 case_version/fingerprint/parameters/resource_leases/error 不丢失；Report 的 evidence_index/artifacts 不丢失；旧 `POST /api/run` 请求和 GET run/report JSON 有显式 fixture 断言。扩展 `apps/workbench/test_app.py` 覆盖旧版本保持或新版本迁移响应。

  - 前置命令：`./.venv/bin/python3 -m pytest apps/workbench/orchestration/test_model_mapping.py libs/test_automation/test_models_serialization.py libs/test_automation/test_state_machine.py apps/workbench/test_app.py -q`
  - 预期失败：缺少 dto/mapper，或当前同名 Workbench 模型无法满足 canonical 字段保留断言。
  - 失败处理：若旧 payload 必须改变，先增加版本化 endpoint 或可审计迁移器测试，不得直接改掉原 fixture。

- [ ] **Luna：定义 DTO 和 mapper。** 新建 `apps/workbench/orchestration/dto.py`：`RunRequest`、`RunView`、`RunStepView`、`AssertionView`、`ArtifactView`、`ReportView` 与必要的 firmware/source/flow views。新建 `mappers.py`：`request_to_execution_context`、`canonical_run_to_view`、`assertion_result_to_view`、`artifact_to_view`、`canonical_report_to_view`。执行和审计 canonical 类型只来自 `test_automation.models`；DTO 不得与其同名。

  - 验证命令：`./.venv/bin/python3 -m pytest apps/workbench/orchestration/test_model_mapping.py libs/test_automation/test_models_serialization.py -q`
  - 预期成功：所有 mapper round-trip 和字段损失测试通过。

- [ ] **Luna：迁移执行、存储与 REST 边界。** 修改 `runner.py` 使一次执行建立 `test_automation.models.Run` 与 canonical AssertionResult/Artifact/Report，再由 mappers 生成 Workbench view；修改 `store.py` 在写入/读取边界使用 mapper 的稳定 dict；修改 `api.py` 使用 `RunRequest` 与 `RunView/ReportView`。清理 `models.py` 的重复 `Run/Report/Assertion/ArtifactInfo/RunInput`，仅保留无 execution-domain 对应的 flow comparison 值对象，或将其迁至 dto。

  - 验证命令：`./.venv/bin/python3 -m pytest apps/workbench/orchestration/test_model_mapping.py apps/workbench/orchestration/test_contract_fixes.py apps/workbench/orchestration/test_evidence.py apps/workbench/test_app.py libs/test_automation/test_models_serialization.py libs/test_automation/test_state_machine.py -q`
  - 预期成功：领域序列化、状态机、DTO 映射、报告/Artifact、REST 生命周期均通过。

- [ ] **Terra：G3 独立审查。** 比较 canonical 序列化与 REST JSON fixture，确认没有静默字段损失；确认 `RunStatus` 只从 `test_automation.models` 导入；确认同名模型不再表示不同语义。

  - 复核命令：`/usr/bin/grep -RIn --include='*.py' '^class \(Run\|Report\|Assertion\|ArtifactInfo\|RunInput\)' apps/workbench/orchestration libs/test_automation`
  - 预期成功：仅 canonical domain 名称和明确命名的 DTO/view 保留。
  - 检查点：技术总管审查字段矩阵、API 迁移说明和 G3 证据后，才允许 G4。

## G4 — 离线集成验收与回滚处理

- [ ] **Luna：运行完整首期离线集。** 执行：`./.venv/bin/python3 -m pytest libs/parser_lib/test_protocol_13762.py libs/loghooks/test_sources_concentrator.py libs/loghooks/test_dependency_boundary.py libs/sim_concentrator/test_frame_codec.py libs/sim_concentrator/test_runner.py libs/test_automation/test_ports.py libs/test_automation/test_models_serialization.py libs/test_automation/test_state_machine.py apps/workbench/orchestration/test_ports_integration.py apps/workbench/orchestration/test_model_mapping.py apps/workbench/orchestration/test_contract_fixes.py apps/workbench/orchestration/test_evidence.py apps/workbench/orchestration/test_profile_loading.py apps/workbench/test_app.py -q`。

  - 预期成功：退出码 0；无真实串口打开。
  - 失败处理：停止在失败阶段，保留失败测试与输出；仅回退该阶段尚未验收的局部代码，不触碰已通过阶段或用户既有改动。

- [ ] **Terra：运行静态边界复核。** 执行 loghooks 的 AST 边界测试、runner ports AST 测试，并审阅 mapper 的字段矩阵。

  - 预期成功：两个 AST 边界测试通过，mapper 无未声明字段损失。

- [ ] **技术总管：审核并登记 G4。** 审核 Luna/Terra 证据，运行 `git diff --check`、UTF-8 `iconv -f UTF-8 -t UTF-8` 检查所有修改文本，并更新需求的进度与设计记录。

  - 预期成功：离线门通过后才可发起 G5。

### G3 当前门禁（2026-08-28）

Terra 已退回 G3 的 B/D execution/report 接线，故不得进入 G4。下一切片必须先让 canonical Run 在创建、租约变化、结束与错误时持久化；Store 只接收 canonical Run/Report；generic error 不泄露内部路径；随后再完成 API DTO/view 和旧模型收敛。
