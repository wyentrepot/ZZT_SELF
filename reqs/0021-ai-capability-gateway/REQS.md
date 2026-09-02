# REQS-0021 — AI 能力任务门面（v2：本机全权限、任务级编排、分层证据与低 token 工作流）

> 状态：✅ P0-P4 完成；真实烧录暂不执行，保留人工验收
>
> 创建：2026-09-02
>
> 代码基线：`master` / `4ed9384`
>
> 关联：REQS-0005（AI 观察）、REQS-0008（simcon AI 控制面）、REQS-0009（listener trace）、REQS-0017（AI 排查方法论）、REQS-0018（只读接收库查询）
>
> 需求映射：[FR-10 AI 任务级能力门面](../../docs/02-总需求.md)

## 1. 背景

当前工作台具有 138 个后端处理函数，其中 `/api/ai/v1` 有 34 个路由。后者包含
3 个仅限人工本机管理员的 grant 路由；其余路由按模块会话、观察、operation、
Artifact、listener、trace 和 simcon 细分。底层拆分有助于幂等、审计和硬件资源
独占，但 AI 要完成一次跨源排查时必须自行决定顺序、拼装请求、轮询 operation、
关联多个来源并裁剪大日志，调用和 token 成本过高。

本需求新增 `/api/ai/v2` 任务门面。目标不是物理删除 v1 路由，而是让默认 AI
调用从“理解后端路由”变为“提交有界任务并读取有界证据”。

## 2. 当前生效基线

1. `/api/ai/v1`、`/api/*` 页面/子应用接口和现有项目内 `observe-workbench-logs`
   技能均保持路径和语义兼容；v2 只能复用它们的服务层，不能反向 HTTP 调用。
2. 本机全权限只定义为 v2 的 loopback 信任域：`WORKBENCH_LOCAL_FULL_ACCESS=1`
   时，实际对端为 `127.0.0.1`、`::1` 或测试客户端的请求可调用全部 v2 能力，
   仍写审计。不得依据 `Host`、`X-Forwarded-For` 或可伪造请求头判断本机。
3. 管理员签发、列举和撤销 grant 继续要求“本机 + admin key”；本机全权限不授予
   远程授权管理权。
4. 非 loopback 的 v2 请求必须使用 Bearer grant，按 capability 映射到既有
   scope/resource 校验。**本需求首期不改造旧 v1/页面路由的局域网策略，不能宣称
   已完成全局 LAN 权限收敛。**
5. 三源的事实语义保持独立：module_log、listener、simcon 不合并原始 schema。
   只统一 source、window、稳定关联键、来源健康度、job 和证据引用。
6. `job_state` 与业务 `verdict` 分离。前者为 queued/running/succeeded/failed/
   cancelled；只有观察/验证可产生 pass/fail/inconclusive/error。发送成功、打开
   串口或烧录完成不是业务 pass。
7. Evidence 默认 L1（1--3 KB 摘要），L2 最大 16 KB 或 50 条证据，L3 仅按稳定
   `evidence_id`、`raw_ref`、`index_id + frame_id` 下钻。历史 listener 查询必须
   保持同一 `index_id`；没有可信到达时间的 live `not_seen` 是
   `live_window_unverified`/`inconclusive`，不是缺失证明。
8. `cleanup=owned_only` 是默认且唯一自动清理策略：仅关闭当前 job 创建的资源，
   不关闭复用的页面、人工或其他 job 会话。

## 3. 目标与验收

| 目标 | 可验证出口 |
| --- | --- |
| 默认 AI 调用面收敛 | v2 默认仅暴露 8 条任务级路径；v1 兼容测试保持通过 |
| 本机全权限可解释 | loopback + feature flag 返回全部 capability；admin grant 仍需 admin key |
| 局域网 v2 最小授权 | 非 loopback 缺 token 为 401；有效 grant 只显示/执行被授权 capability |
| 机器可读契约 | v2 每条请求/响应有命名 Pydantic schema；OpenAPI 不含公开 `dict[str, Any]` body |
| 工作流更短 | 固定离线 fixture 的常见排查在创建、等待、按需 L2/L3 三次以内得到 L1 与证据引用 |
| 证据不失真 | 历史 `index_id`、cursor_range 负证据、来源健康度和关联键回归通过 |
| 硬件安全不退化 | 写操作幂等、资源租约、固件白名单、409 映射、审计和不可取消约束均保留 |

## 4. v2 公共契约

| 方法与路径 | 输入 | 输出 | 边界 |
| --- | --- | --- | --- |
| `GET /capabilities` | 无 | `CapabilitySnapshot` | 返回 capability revision、后端可用性、资源别名；不泄漏 token/绝对路径 |
| `POST /investigations` | `InvestigationRequest` | `JobEnvelope` | 并行只读查询/观察；不新建 VerificationEngine |
| `POST /verification-runs` | `VerificationRunRequest` | `JobEnvelope` | 复用既有 simcon verify/Run 逻辑 |
| `POST /module-actions` | `ModuleActionRequest` 判别联合 | `JobEnvelope` | action 仅 allowlisted `ensure|send|stop` |
| `POST /flash-jobs` | `FlashJobRequest` | `JobEnvelope` | 固件目录白名单、幂等、不可取消 |
| `GET /jobs/{job_id}` | `wait_seconds=0..30` | `JobEnvelope` | 读取不得推进状态；后台执行器推进 job |
| `POST /jobs/{job_id}/cancel` | 无 | `JobEnvelope` | 不可取消任务返回稳定冲突码 |
| `GET /jobs/{job_id}/evidence` | `level=L1|L2|L3` | `EvidenceView` | L3 只接受服务端登记引用 |

必备模型：

```python
AccessContext(zone: Literal["local_full", "lan_scoped"], actor: str)
Capability(name: str, allowed: bool, resources: list[str], reason: str | None)
JobEnvelope(job_id: str, job_state: JobState, verdict: Verdict | None,
            source_health: dict[str, SourceHealth], summary: str | None,
            evidence_refs: list[EvidenceRef], underlying_refs: list[str])
EvidenceRef(source: SourceKind, evidence_id: str | None, raw_ref: str | None,
            index_id: str | None, frame_id: int | None, correlation: CorrelationKeys)
```

## 5. 非目标

- 不删除、重命名或批量迁移 v1、页面、listener、module_log、simcon 的既有路由。
- 不在本需求中实现新 `VerificationEngine`、新协议解析、向量数据库、全量日志 embedding、
  自动修代码、自动提交或硬件测试自动化。
- 不将局域网旧路由的全局鉴权/网络隔离伪装为本期完成；若启动该工作，另立安全需求。
- 不让服务端的紧凑摘要取代原始证据、人工复核或确定性判定。

## 6. 阶段与人机门

| 阶段 | 交付 | 状态 | 退出条件 |
| --- | --- | --- | --- |
| P0 | 本需求基线、骨架设计、任务安排与阶段计划 | ✅ 完成 | 本文、`TODO.md`、`DONE.md`、`REQS-INDEX.md` 和权威文档链接校验通过 |
| P1 | v2 模型、访问域、capabilities、OpenAPI 契约 | ✅ 完成（2026-09-02） | fake/TestClient 覆盖本机、LAN、schema、错误与审计；不改 v1 |
| P2 | investigations/jobs/evidence 只读门面 | ✅ 完成（2026-09-02） | 离线三源 fixture、index_id、L1/L2/L3 与三调用门通过 |
| P3 | module/verify/flash 写任务门面 | ✅ 完成（2026-09-02） | 幂等、owned_only、409、审计、白名单与不可取消回归通过 |
| P4 | 文档、skill、库存自动校验与 v2 使用统计 | ✅ 完成（2026-09-02） | 文档单一事实、自动库存校验和完整回归通过 |

每个阶段完成后必须报告“做了什么、未做什么、边界、验证证据与恢复入口”，并等待
用户显式指定下一个阶段；不得自动越过阶段。

## 7. 变更记录

### 变更 1 ｜ 2026-09-02 ｜ 用户 / Codex

- **改成什么**：新建 REQS-0021；采用 v2 任务门面而非删减 v1 路由，本机全权限仅限 v2 loopback 域，局域网旧路由收敛延后。
- **为什么**：减少 AI 为接口发现、轮询、跨源关联和大日志搬运付出的时间与 token，同时不损失证据、审计、资源独占和兼容性。
- **影响**：未来涉及 `apps/workbench` AI 控制面、Pydantic/OpenAPI、离线证据回归、控制面 skill 与接口库存文档；不改变 REQS-0017/0018 已交付行为。
- **被取代**：无（新增需求）。

### 变更 2 ｜ 2026-09-02 ｜ Codex

- **改成什么**：完成 P1：新增命名 Pydantic 契约、基于真实 ASGI 对端的访问域解析、
  v2 capability-to-scope/source 映射和 `GET /api/ai/v2/capabilities`。
- **为什么**：让本机全权限必须同时满足 loopback 和显式开关，让 LAN 只能看见 grant
  实际覆盖的逻辑资源与能力，且不泄漏 token、路径或物理串口句柄。
- **影响**：只新增 v2 P1 文件及 router 注册；v1 路由、grant 存储/认证语义、页面路由
  和局域网旧路由均未修改。其余七条 v2 任务路径仍属于 P2/P3，尚未注册。
