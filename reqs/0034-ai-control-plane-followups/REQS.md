# REQS-0034 — ai-control-plane / mclt 工具待办收口（启动前置检查、401 区分、job 终态提示、L3 ref 示例、mclt 三改进、README 首屏）

> 状态：🚧 进行中（已登记，待实施）｜ 当前基线：v1.0（2026-09-21）
> 创建：2026-09-21
> 来源：2026-09-21 第三方反馈核查（属实但待办部分），关联 REQS-0033（技能边界优化 + capabilities call_examples 已完成）
> 关联：ai-control-plane 技能（全局库 v2.7.0）、apps/workbench（ai_v2_api / ai_capability_service / ai_auth）、
> tools/taiti/分钟采集（mclt 工具）、README.md

## 1. 背景与问题

2026-09-21 第三方反馈逐条核查后，以下问题**属实但尚未落地**（REQS-0033 只覆盖了其中的
启动边界文档化、capabilities call_examples、归档清单、离线日志索引）：

1. **启动/运行环境门槛**：`run.py` 不检测环境变量，缺 `WORKBENCH_LOCAL_FULL_ACCESS=1` 时
   v2 capabilities 裸 401「缺少 Bearer token」，不提示"请设环境变量"；README.md 首屏
   只有 `python -m workbench.run`，未写明环境变量与常驻方式。
2. **investigation 信封恒 queued 的语义混淆**：同步历史路径已在返回前执行完，但响应仍为
   `queued`，客户端必须再轮询 `GET /jobs/{id}`；响应中没有"必读 job"的显式提示字段。
3. **L3 ref 格式易拼错**：`_l3_items` 对格式错误只报「L3 ref 格式错误：xxx」，未给出
   合法格式示例（`listener:<index_id>:<frame_id>`）。
4. **mclt 工具三处使用体验缺口**：`miss --freeze` 对"页面无该周期"输出"缺失 N 块"无告警
   （极易误读为真缺）；`cco` 只给 06F230 总数不按冻结周期分布；各命令输出格式不统一、无 `--json`。
5. **README 首屏未写启动门槛与常驻方式**：与 REQS-0033 已固化的 SKILL.md 边界条款不同步。

> 不在本需求范围（外部/固件侧）：CCO 固件告警补打 MAC（drop stale/rejected）、页面快照
> 全周期导出——属固件/流程侧，由对应侧跟进，此处仅登记不实施。

## 2. 用户诉求（原话归纳）

属实但待办部分先新增 req（设计 + 业务需求与业务逻辑 + plan）。

## 3. 目标（当前生效基线 v1.0）

### BR-1 启动前置检查与 401 区分（run.py + 401 文案）

- **业务需求**：启动工作台时环境变量缺失能**直接看到原因**，而不是启动成功但 v2 调用裸 401；
  401 文案区分"缺 token"与"缺环境变量"。
- **业务逻辑**：
  - `apps/workbench/run.py`：启动横幅打印前置检查清单——`WORKBENCH_LOCAL_FULL_ACCESS` 当前值
    （缺省提示"v2 将需 token"）、`HPLC_OPEN_WORKBENCH`（headless 提示加 `=0`）、端口/健康地址。
  - `apps/workbench/ai_v2_api.py` `_bearer_grant`：401 detail 区分场景——`resolve_access_context`
    未达 local_full 且无 Bearer 时，文案含"v2 需 Bearer token；本机 loopback 可设
    WORKBENCH_LOCAL_FULL_ACCESS=1 免 token"；有 token 但无效/过期/撤销时保留原文案。

### BR-2 investigation 响应带"必读 job"提示字段

- **业务需求**：客户端一眼可知"此响应只是受理回执，必须读 `GET /jobs/{id}` 拿终态"，
  消除"信封 queued + 又要轮询"的两步语义困惑。
- **业务逻辑**：
  - `JobEnvelope`（ai_contracts.py）增加可选字段，如 `follow_up: str | None`（investigation
    创建响应固定为 `"GET /api/ai/v2/jobs/{job_id}"` 之类提示，其他类型任务同）；或更轻的方案：
    在 `investigation queued` 的 `summary` 里带上"读 job 拿终态"提示——**实现时二选一并写清
    取舍**（首选显式字段，语义清晰；summary 是纯文本改动最小）。
  - 契约兼容：可选字段默认 null，旧客户端无影响；补单测断言。

### BR-3 L3 ref 格式错误提示带合法示例

- **业务需求**：L3 ref 拼错时，422 报错直接给出合法格式，客户端不用翻文档。
- **业务逻辑**：`ai_capability_service.py` `_l3_items` 的格式错误分支文案改为
  `L3 ref 格式错误：{ref}；合法格式 listener:<index_id>:<frame_id>，如 listener:idx-xxx:123`。
  补单测断言文案。

### BR-4 mclt 工具三处改进（tools/taiti/分钟采集）

- **BR-4.1 `miss --freeze` 页面空集告警**
  - 业务需求：目标冻结周期不在页面快照里时，输出明确"页面无该周期，无法对账（仅机制推断）"，
    而不是静默输出"缺失 184 块"。
  - 业务逻辑：`analyze_miss` 中 `page.get(fk)` 为空时，`result[fk]` 增加
    `"page_missing": True` 与提示文案；`cmd_miss` 据此打印告警行；单测覆盖。
- **BR-4.2 `cco` 输出 per-freeze 06F230 分布**
  - 业务需求：一条命令直接看 06F230 按冻结周期的分布，不必自写脚本。
  - 业务逻辑：`parse_cco` 的 `f230` 已带 (t, ts, sta, fr)，`analyze_cco` 增加按 `fr`（冻结分钟）
    聚合的分布 dict，`cmd_cco` 打印；单测覆盖。
- **BR-4.3 `--json` 结构化输出**
  - 业务需求：各命令输出统一可程序化消费（AI/脚本对接）。
  - 业务逻辑：`run.py` 全局加 `--json` 开关，各 `cmd_*` 返回 dict、`--json` 时
    `json.dumps(..., ensure_ascii=False, indent=2)` 到 stdout、关闭人类可读打印；
    单测断言 JSON 结构。

### BR-5 README 首屏写明启动门槛与常驻方式

- **业务需求**：README「快速运行」段同步 REQS-0033 已固化的启动边界（环境变量前置 +
  双环境常驻 + 停止姿势），人/AI 看首屏即知正确姿势。
- **业务逻辑**：README.md「快速运行」补三行：`WORKBENCH_LOCAL_FULL_ACCESS=1`（v2 免 token）、
  headless 加 `HPLC_OPEN_WORKBENCH=0`、agent 沙箱用受管后台任务 / 桌面用 nohup（指向 SKILL.md）。

## 4. 边界

- **不重复 REQS-0033**：技能文档（SKILL.md/offline-analysis/listener）已在 0033 完成，本需求
  只做**运行时行为与工具/README** 侧；如技能文档需同步（如 401 文案变化），作为附带项登记。
- **固件/流程侧不实施**：CCO 告警补 MAC、页面快照全周期导出仅登记，不做代码改动。
- **mclt 工具改动只动 tools/taiti/分钟采集**：不动 parser_lib 与工作台解析路径。
- **契约兼容**：BR-2/BR-3 均为可选字段或文案变化，旧客户端无破坏。

## 5. 验收

- [ ] 启动横幅含前置检查清单；缺环境变量时 v2 401 文案含"可设 WORKBENCH_LOCAL_FULL_ACCESS=1"提示。
- [ ] investigation 创建响应带"必读 job"提示（显式字段或 summary 提示，二选一）；单测断言。
- [ ] L3 ref 格式错误 422 文案含 `listener:<index_id>:<frame_id>` 合法示例；单测断言。
- [ ] `miss --freeze` 对页面无该周期输出"无法对账（仅机制推断）"告警；单测覆盖。
- [ ] `cco` 输出 06F230 per-freeze 分布；单测覆盖。
- [ ] `--json` 开关可用，各命令输出 JSON 结构化；单测断言。
- [ ] README 首屏含环境变量 + 双环境常驻说明。
- [ ] 全量回归无新增失败；技能校验通过（如技能侧有同步）。

## 6. 变更记录

### 变更 1 ｜ 2026-09-21 ｜ 用户
- **改成什么**: 需求建立（基线 v1.0）：2026-09-21 第三方反馈核查中"属实但待办"部分的收口——
  启动前置检查/401 区分、investigation 必读 job 提示、L3 ref 格式示例、mclt 三改进、README 首屏。
- **为什么**: 用户要求把属实但待办的建议纳入后续（先新增 req，设计 + 业务需求与业务逻辑 + plan）。
- **影响**: 新建 reqs/0034-ai-control-plane-followups/ 并登记 REQS-INDEX.md；
  apps/workbench run.py/ai_v2_api/ai_contracts/ai_capability_service + 单测；tools/taiti/分钟采集；README.md。
- **变更前基线**: 无（新建）
- **变更后基线**: v1.0
- **被取代**: 无。PLAN.md 动工前由 writing-plans 生成。
