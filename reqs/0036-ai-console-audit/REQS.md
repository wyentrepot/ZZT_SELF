# REQS-0036 — AI 控制台技能体检与实测收口（expect 记法归一、v2 job 阻塞读、summary 判定、技能补 simeter/并发抄表）

> 状态：✅ 已完成（v1.0，2026-09-29）
> 创建：2026-09-29
> 来源：用户口头需求——"核查一遍当前的 ai 控制台的技能，然后测试一遍，用知识库里面的正式的
> 深化应用流程来做测试，看哪些部分会有误导，调用接口会不明确以及结果不对，以及是否有的
> 新增接口没有做到技能里面去"；实测后用户确认修复项与技能补录范围
> 关联：REQS-0021（v2 任务门面）、REQS-0033（call_examples）、REQS-0035（sim_meter）、
> docs/16-AI操作指南.md、技能 ai-control-plane（事实源 skill-fc shared，v2.8.0）

## 1. 背景与问题

按 docs/16 深化应用流程对工作台 AI 控制面（v2 四步流 + v1 token 流）做全链实测
（假对端 FakeCCO/日志源 + pty，不碰真机串口），并静态核查 ai-control-plane 技能
（SKILL.md + 12 references）与运行时能力的覆盖差，发现四类问题：

1. **expect 匹配的 AFN/Fn 记法坑**：matcher 对数值 `afn` 按原始字节值比较（10≠0x10），
   docs/16 §9.2 示例数值风格（`{"afn":6,...}`）对两位 AFN（10H/13H/14H）静默不匹配；
   `fn:"F2"`（知识库 JSON 约定记法）在 expect 侧 `int()` 直接抛异常炸整个任务，
   而 send 侧 `_norm_fn` 接受——两侧口径不对称；超时原因只说"收到 N 帧未匹配"，
   不透出逐帧差异，AI 无法自纠。
2. **v2 `GET /jobs/{id}?wait_seconds` 参数被静默忽略**（接口声明 0-30，AI 天然当阻塞
   等待用，实际立即返回 running）；verification_run 终态 summary 停留任务状态
   （"succeeded"），AI 只读信封会把"任务跑完"误判为"验证通过"。
3. **技能真空区**：simeter 全线（18 端点 + v2 能力 `simeter_frames.read` + scope
   `simeter:read`）在 SKILL.md/12 references/auth.md 零命中；simcon 并发抄表
   （batch_read）/recipes/archive 等新能力只在 api-contract.md，路由表指向的
   references/simcon.md 未覆盖；auth.md scope 计数 13（实际 14）。
4. **文档口径误差**（REQS-0035 自引入 2 处已当日修复）：capabilities 里 simeter
   观察示例键名 `body.sources`（应为 `observations`）；`/api/simeter/_meta` notes
   谎称默认地址 999999999999（实际出厂 000000000001）。另 KB
   `安徽集中器交互报文.md` 10H-F2 行"下行：无数据单元"与真机帧
   （起始序号 2B + 数量 1B，CS=C+用户数据=0x6B 实帧核对）不符。

## 2. 用户诉求（原话归纳）

把现在发现的问题修改了；给技能补 simeter 章节 + simcon.md 补并发抄表/recipes 路由 +
auth.md scope 表（纯文档改动，事实源在 skill-fc shared 那份）；按仓库惯例把本次审计
登记成 REQS。

## 3. 目标与交付（v1.0 已完成）

### BR-1 expect 记法归一与失败可自纠
- matcher：fn 归一 `_norm_fn`（"F2"/"F230"/2/230 等价），非法记法给出人话原因
  而非异常；afn/fn 记法口径写入 docstring（afn 字符串按 hex、数值按原始字节值）。
- runner：expect 超时 reason 透出最近未匹配原因采样（去重 ≤3 条，
  如 `AFN 不匹配: 期望0x0A, 实际0x10`），AI 可据此自纠。

### BR-2 v2 job 读取语义修正
- read_job 实现 `wait_seconds`（≤30）阻塞等待到终态（仅轮询 store 快照，
  不推进任务、无副作用）；verification_run 终态 summary 带步骤判定
  （`verification_run succeeded: fail (0 pass/1 fail)` 形式）。

### BR-3 技能补录（事实源 skill-fc shared，v2.7.0 → v2.8.0）
- SKILL.md：用途路由表新增"模拟电表调测""并发抄表/档案/recipe"两行；
  v2 速查表新增 simeter 只读观察行；frontmatter description/argument-hint 补
  模拟电表触发词；实测校准新增 expect 记法与 v2 job 阻塞读两条。
- 新增 `references/simeter.md`（_meta 优先、最小调用链、对档联调易踩点）。
- references/simcon.md：补并发抄表 batch_read / recipes / archive / report_buckets /
  batch/stats 直连段 + expect 记法说明 + 修正示例记法。
- references/auth.md：scope 表 13→14 种，补 `simeter:read`。
- references/api-contract.md：新增 §5.1 模拟电表 simeter 契约表。
- references/operation-guide.md（自包含手册）：同步 docs/16 两处修正 + simeter 速览段。

### BR-4 知识库与文档口径补正
- docs/16：§1.1 步骤 3 改为真实 wait 语义；§9.2 示例改字符串记法 + 记法说明；
  新增 §9.6 模拟电表速览。
- KB `安徽集中器交互报文.md`：10H-F2 下行补正为"起始序号(2B 小端)+数量(1B)"并注明实帧依据。

## 4. 验收

- [x] `libs/sim_concentrator/test_runner.py` 新增 3 用例（字符串记法 pass、数值 afn
      陷阱 fail+原因透出、非法 fn 人话原因）全绿。
- [x] `apps/workbench/test_ai_capability_read_job.py` 新增 4 用例（阻塞等待、终态立即返回、
      summary 判定、提前唤醒）全绿。
- [x] 真实调用链复测：expect `{"afn":"10","fn":"F2"}` 对 FakeCCO 10H-F2 回帧 pass；
      信封 summary 出现 `fail (0 pass/1 fail)`。
- [x] 技能 v2.8.0 事实源改动落盘（simeter.md 新增、simcon/auth/api-contract/SKILL 补录），
      工作区 `.agents/skills/ai-control-plane` 软链接自动生效。
- [x] 全量回归与既有基线一致（预存失败不扩大）。
