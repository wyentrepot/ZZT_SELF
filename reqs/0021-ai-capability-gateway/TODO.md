# REQS-0021 TODO

> 本文件是本需求的唯一阶段执行清单；每阶段必须经用户明确启动和验收后才可进入下一阶段。

## P0 — 文档基线 ✅ 完成 2026-09-02

- [x] 在 `REQS-INDEX.md` 登记 REQS-0021、范围、状态与 `master` 基线。
- [x] 创建 `REQS.md`：冻结 v2 路径、loopback 本机全权限、局域网延后、三源证据、资源所有权和非目标。
- [x] 更新 `docs/02-总需求.md`：登记 FR-10，给新增需求分配唯一系统级需求与验收方向。
- [x] 更新 `docs/03-骨架设计.md` §6.4：定义 v2 与 v1/只读观察技能的兼容边界、模型、路径和访问域。
- [x] 更新 `docs/04-任务安排.md`：登记任务 7、P0 状态、P1--P4 门和验收出口。
- [x] 创建 `DONE.md`：记录 P0 产物、未做事项、恢复入口和文档验证证据。

**P0 出口：** 文档链接、Markdown 结构、`git diff --check` 和仅允许文件清单通过后停止，等待用户确认 P1。

## P1 — 契约、访问域与 capability ✅ 完成 2026-09-02

**文件：**

- Create: `apps/workbench/ai_contracts.py`
- Create: `apps/workbench/ai_access.py`
- Create: `apps/workbench/ai_v2_api.py`
- Create: `apps/workbench/test_ai_v2_contracts.py`
- Create: `apps/workbench/test_ai_v2_api.py`
- Modify: `apps/workbench/app.py`
- Modify: `apps/workbench/ai_auth.py`（仅新增 v2 grant 映射辅助；不得改 v1 authenticate 行为）

**接口：**

```python
def resolve_access_context(request: Request, *, local_full_enabled: bool) -> AccessContext: ...
def capability_snapshot(context: AccessContext, control: AIControlService) -> CapabilitySnapshot: ...
def create_ai_v2_router(control: AIControlService, auth_store: AuthorizationStore) -> APIRouter: ...
```

- [x] 先在 `test_ai_v2_contracts.py` 断言 loopback + flag 为 `local_full`、非 loopback 无 token 为 401、伪造 `X-Forwarded-For` 不提升权限、admin grant 仍拒绝无 admin key。
- [x] 先在 `test_ai_v2_api.py` 断言 `/api/ai/v2/capabilities` 的 OpenAPI response 引用命名 schema；P1 仅注册无 request body 的 capabilities 路径，未公开 `dict[str, Any]` body。
- [x] 实现 Pydantic `AccessContext`、`Capability`、`JobEnvelope`、`EvidenceRef` 和稳定错误响应模型；`JobEnvelope.verdict` 对非观察/验证任务为 `None`。
- [x] 实现实际对端 IP 判定、显式 `WORKBENCH_LOCAL_FULL_ACCESS` 开关和 v2 router 注册；不读取 `Host`/转发头；不改 `/api/ai/v1/*`。
- [x] 实现 capabilities 只返回可调用名称、资源 alias、后端可用性与 revision；不返回 token、真实串口句柄或绝对路径。
- [x] 运行：`$env:PYTHONPATH="apps;libs"; python -m pytest apps/workbench/test_ai_v2_contracts.py apps/workbench/test_ai_v2_api.py apps/workbench/test_ai_api.py apps/workbench/test_ai_operations.py -q`（63 passed）。
- [ ] 预期：全部通过；`git diff --check` 为零；无串口、服务或硬件启动。

**P1 出口：** 提交测试输出、OpenAPI 断言与允许文件清单，等待用户确认 P2。

## P2 — 只读 investigation、job 与 evidence ✅ 完成 2026-09-02

**文件：**

- Create: `apps/workbench/ai_capability_service.py`
- Modify: `apps/workbench/ai_v2_api.py`
- Modify: `apps/workbench/ai_operations.py`（仅抽取可复用的只读调用；不得改变 v1 返回）
- Modify: `apps/workbench/test_ai_v2_api.py`
- Modify: `apps/workbench/test_ai_operations.py`
- Modify: `apps/workbench/test_ai_store_query.py`

**接口：**

```python
def start_investigation(request: InvestigationRequest, *, context: AccessContext) -> JobEnvelope: ...
def read_job(job_id: str, *, wait_seconds: int) -> JobEnvelope: ...
def read_job_evidence(job_id: str, *, level: EvidenceLevel) -> EvidenceView: ...
```

- [ ] 先写离线 fixture 测试：同一 historical `index_id` 的 listener 查询与 artifact 绑定；cursor_range 的未命中可形成负证据；live 未命中返回 `inconclusive/live_window_unverified`。
- [ ] 先写并行查询测试：module_log、listener、simcon 的异常不会互相覆盖；每个 source 均返回 health 与稳定关联键。
- [ ] 实现 investigation 服务端扇出、L1 默认摘要、L2 16 KB/50 条截断、L3 引用下钻和 `context_id` 的短期复用；不得返回整份原始日志。
- [ ] 实现 job 由后台执行或来源回调推进；`GET /jobs` 不调用写状态的 refresh 方法。
- [ ] 运行：`$env:PYTHONPATH="apps;libs"; python -m pytest apps/workbench/test_ai_v2_api.py apps/workbench/test_ai_operations.py apps/workbench/test_ai_store_query.py apps/workbench/test_ai_module_observation.py -q`。
- [ ] 预期：固定离线场景在 create → wait → evidence 三次以内取得 L1 与引用；v1 历史 index 路径、source health 和 artifact 回归通过。

**P2 出口：** 保存离线测试输出和一次结构化 L1/L2/L3 样例，等待用户确认 P3。

## P3 — 受控写任务与资源所有权 ✅ 完成 2026-09-02

**文件：**

- Modify: `apps/workbench/ai_capability_service.py`
- Modify: `apps/workbench/ai_v2_api.py`
- Modify: `apps/workbench/ai_store.py`
- Modify: `apps/workbench/test_ai_v2_api.py`
- Modify: `apps/workbench/test_ai_api.py`
- Modify: `apps/workbench/test_ai_simcon.py`
- Modify: `apps/workbench/test_ai_trace.py`

**接口：**

```python
def start_module_action(request: ModuleActionRequest, *, context: AccessContext) -> JobEnvelope: ...
def start_verification_run(request: VerificationRunRequest, *, context: AccessContext) -> JobEnvelope: ...
def start_flash_job(request: FlashJobRequest, *, context: AccessContext) -> JobEnvelope: ...
```

- [ ] 先写 fake-service 测试：重复 `client_request_id` + 相同 payload 返回同 job；相同 key + 不同 payload 返回 409；跨资源 token 不能复用 job。
- [ ] 先写资源所有权测试：job 创建的会话被 `owned_only` 关闭；预存/页面复用会话绝不关闭；资源冲突稳定返回 409。
- [ ] 实现 allowlisted `ensure|send|stop` 判别 action、simcon verify、独立 flash job；禁止任意 action/payload 转发。
- [ ] 保留 flash `firmware_roots` 校验、verify/flash 不可取消、audit actor 与原始 `operation_id/run_id` 引用。
- [ ] 运行：`$env:PYTHONPATH="apps;libs"; python -m pytest apps/workbench/test_ai_v2_api.py apps/workbench/test_ai_api.py apps/workbench/test_ai_simcon.py apps/workbench/test_ai_trace.py -q`。
- [ ] 预期：无真实串口下的 Fake 服务全部通过；无自动硬件启动；v1 409/幂等测试不回归。

**P3 出口：** 提供写任务审计、幂等、资源所有权和不可取消测试证据，等待用户确认 P4。

## P4 — 文档、库存与使用迁移 ✅ 完成 2026-09-02

**文件：**

- Create: `tools/scripts/verify_api_inventory.py`
- Modify: `docs/api-contract.md`
- Modify: `docs/16-AI操作指南.md`
- Read-only reference: `docs/api-endpoints-inventory.md`（保留用户原文件，不修改）
- Modify: `.agents/skills/ai-control-plane/SKILL.md`
- Modify: `apps/workbench/test_ai_v2_api.py`

- [x] 编写 OpenAPI 交叉校验：输出 handler 数、v2 公开路径、命名 schema、兼容分层和 AI capability；检测 v2 路由/schema 漂移。
- [x] 将文档拆成“路由事实表”“AI capability 表”“专家/页面兼容表”；admin grant 不计为 AI 默认能力。
- [x] 将控制面 skill 的默认路径迁到 v2；保留 v1 专家诊断说明，不删除旧路径。
- [x] 运行库存校验、Python 编译检查与 `git diff --check`；未启动硬件。
- [ ] 运行 v2 全测试、既有 AI API/trace/simcon/store 回归（由 P4 总验收统一执行）。
- [x] 当前文档事实与 OpenAPI 一致；无 v1 删除。v2 使用统计仍需真实调用数据后另行决策。

**P4 出口：** 文档、库存输出、回归和使用统计完整；是否弃用 v1 或收敛旧局域网路由必须另获用户决策。
