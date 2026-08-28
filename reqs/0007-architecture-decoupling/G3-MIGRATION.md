# G3-MIGRATION.md — canonical 模型兼容投影迁移矩阵

> 状态：✅ 设计已定，Luna 可实施
>
> 决策：本期不改变现有 `/api/run`、`GET /api/run/{id}`、`GET /api/run/{id}/report` 的字段语义，也不删除 SQLite 既有列。执行/审计领域对象成为内部事实源；REST 和旧存储结构通过显式 mapper 投影。

## 1. 迁移原则

1. `test_automation.models` 是执行与审计的唯一 canonical source：Run、RunStatus、AssertionResult、Artifact、Report、ResourceLease、StepResult。
2. Workbench 只保留 `dto.py` 中明确命名的 RunRequest/RunView/ReportView 等边界对象；不得继续用同名 Pydantic Run/Report/Assertion/ArtifactInfo 表达另一套领域事实。
3. 旧 REST JSON 与旧 runs/run_steps 列是兼容投影，而不是新的 canonical source。
4. 新数据库列只允许幂等、事务化的 additive migration；旧行必须明确标记为 legacy，不能伪造 fingerprint。
5. 所有测试使用临时 SQLite/临时报告目录；禁止触碰项目真实 runtime 数据、串口或硬件。

## 2. 字段矩阵

| 现有 Workbench 语义 | canonical source/生成规则 | 兼容投影 |
|---|---|---|
| run_id / scenario_id / status | Run.id / Run.case_id / RunStatus | 保持旧 JSON 与 runs 列名 |
| firmware version/commit/sha | Run.parameters["firmware"]，并写入 Report.summary | 保留 firmware_ver/firmware_commit；新增 firmware_json |
| created/started/finished | Run.created_at/started_at/finished_at | 保留 created_at，新增 started_at/finished_at |
| updated_at | Store 审计元数据 | 保留旧 updated_at，不塞入 Run |
| case_version / case_fingerprint | 冻结 scenario 的 CasePackage：version 缺省 "1.0.0"，fingerprint 由规范对象计算 | 新增列；旧行填 legacy/legacy-unavailable |
| parameters | RunRequest 与有效 scenario 覆盖的冻结快照 | 新增 parameters_json，不公开绝对路径 |
| resource_leases | runner 获得租约时追加 ResourceLease，释放后不删除历史 | 新增 resource_leases_json |
| error | PortError 取 message；未知异常为受控 code/message 摘要 | 新增 error |
| run_steps | canonical StepResult 同时构造，旧 run_steps 保持兼容投影 | 保留现有表与 GET run.steps |
| assertions | AssertionResult（run_id、assertion_id、outcome、evidence_ids、message） | AssertionView 将 outcome 投影为 result |
| artifacts | canonical Artifact；新增 size:int=0 以覆盖旧 ArtifactInfo.size | ArtifactView 保留旧字段 |
| report sources/flow/feedback/verdict/ts/evidence | canonical Report.summary 的具名 keys | ReportView 从 summary 重构完全等价旧报告 JSON |

## 3. 实施切片

1. 将 FirmwareInfo 与所有 Workbench 请求/响应类型保留在 `dto.py`；删除或停止使用 `models.py` 内与 canonical 重名的 RunInput、Run、Assertion、ArtifactInfo、Report。FlowCompare/SourcesSummary 是 Workbench 专属值对象，可保留或迁至 dto。
2. runner 加载 scenario 后冻结 CasePackage：
   - case_id = scenario["id"]
   - case_version = scenario.get("version", "1.0.0")
   - parameters 至少包括 RunRequest 参数、firmware、skip 开关、log_dir/task_file/rules/extras 和去掉 `_file` 的规范化 scenario
   - case_fingerprint = CasePackage(...).fingerprint()
3. runner 建立 canonical Run；状态转移同步写入 Store。开始/结束时间、PortError、未知异常摘要和 ResourceLease 必须落入 canonical Run。未知异常不得把绝对路径回显到 REST。
4. runner 把当前报告内容装入 canonical Report：summary 具名保存 firmware、scenario、sources、flow_compare、feedback、verdict、ts、evidence_detail、evidence_frozen；旧断言/附件逐个转换为 canonical AssertionResult/Artifact/StepResult。
5. 扩展 canonical Artifact 的 additive `size: int = 0`；mapper 必须 round-trip size。
6. Store 接收 canonical Run/Report，通过 mapper 写旧列与新审计列；读取时重建 canonical，再投影 RunView/ReportView。API 改收 RunRequest、返回 view 的 `model_dump()`。
7. SQLite 启动迁移用 `PRAGMA table_info(runs)` 检查列；每列单独、事务化 `ALTER TABLE ADD COLUMN`。旧行 backfill：case_id=scenario_id、case_version=legacy、case_fingerprint=legacy-unavailable、parameters_json={}、resource_leases_json=[]，其他未知字段为 NULL。旧报告 JSON 可读，新归档可有内部 schema_version 信封，但 REST 返回前必须剥离。

## 4. 必测门禁

- 旧 POST/GET run/report JSON fixture 字段等价。
- 无 version scenario：fingerprint 稳定；改动 scenario 内容后 fingerprint 改变。
- 用旧 schema 创建临时 SQLite → migration → 旧行可读且 legacy 标记准确。
- canonical Run 的 case_version/fingerprint/parameters/leases/error/start/finish round-trip。
- PortError 与未知错误摘要不泄露绝对路径。
- AssertionResult、Artifact.size、Report.summary legacy 字段 round-trip。
- 旧 REST JSON 不公开 parameters/resource_leases/error 或真实 artifact 路径。
- 全套仅使用临时 SQLite 和 Fake ports/FakeIO。
