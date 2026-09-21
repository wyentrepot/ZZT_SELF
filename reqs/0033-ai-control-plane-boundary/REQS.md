# REQS-0033 — ai-control-plane 技能使用边界优化 + v2 capabilities 自描述最小调用链

> 状态：🚧 进行中（P0 文档类边界优化已完成；P1 capabilities 自描述待实现）｜ 当前基线：v1.0（2026-09-21）
> 创建：2026-09-21
> 关联：ai-control-plane 技能（全局库 /home/02-skill-fc/skills，v2.7.0）、REQS-0032（技能范围瘦身/渐进加载）、
> REQS-0021（AI 任务门面 v2）、REQS-0018（AI 只读查询接口）、mclt-collect-analysis 专项技能（分钟采集离线分析）

## 1. 背景与问题

2026-09-21 对 AI 实际使用工作台/技能的反馔核查（详见会话核查结论），确认三类使用边界缺口：

1. **启动/常驻方式易踩**：`run.py` 需 `WORKBENCH_LOCAL_FULL_ACCESS=1`（否则 v2 capabilities 401）；
   headless 需 `HPLC_OPEN_WORKBENCH=0`；**agent 沙箱里 nohup 起的进程随外层 shell 结束被回收**
   （实测：bwrap `--die-with-parent` + 每调用独立 tmpfs，nohup 子进程不存活、/tmp 日志不跨调用），
   必须用受管后台任务方式常驻；另 `pkill -f "workbench.run"` 会匹配自身命令行误杀（使用经验 #3）。
2. **v2 门面文档 vs 实测**：investigation 信封恒为 `queued`、需 `GET /jobs/{id}` 读终态；
   L3 ref 格式 `listener:<index_id>:<frame_id>`。这些已在技能「实测校准」段校准，但
   **capabilities 响应本身不带最小调用链示例**——AI 不加载技能时没有"一键小抄"，要翻文档/源码找入口。
3. **离线排查 90% 是文件解析**：问题归档只有原始日志（7E/CCO/xlsx），**索引库（idx-*.sqlite3）与
   simcon 库在 Windows 打包侧、未随问题文件导出**，AI 只能从原始文件重新解析/重建索引，token 与时间成本高。

维护方式约束（用户明确要求）：**不得以增加「使用经验」条例的方式维护技能**——凡是能固化为
边界条款的内容（启动姿势、红线、路由判定、数据源清单）一律直接写进 SKILL.md / references 正文。

## 2. 用户诉求（原话归纳）

1. skill 维护一下：**不得增加以使用经验增加条例的方式，要将使用 skill 的边界优化好**。
2. 开始设计方案，建立 req，把每一项的业务需求与业务逻辑说明，然后再写 plan。

## 3. 目标（当前生效基线 v1.0）

### 3.1 技能使用边界优化（P0，文档类，2026-09-21 已完成）

#### BR-1 在线/离线判定序（先判路，不默认起工作台）

- **业务需求**：AI 拿到任务先判定"离线能解还是必须在线"，避免为解析一帧/查一个日志就起工作台。
- **业务逻辑**：
  - SKILL.md 顶部新增三档判定序：① 手头只有帧/文件 → **离线路径优先**（应用层帧走「日常轻量档」；
    分钟采集走 mclt-collect-analysis 专项；漏点定位走 offline-analysis 三件套）；② 需驱动工作台
    （串口/烧录/观察取证/simcon/组网/在线索引）→ v2 最小路径；③ 拿不准后端能力 → 先 capabilities。
  - 用途路由表补「分钟采集离线分析 → mclt-collect-analysis 专项技能」行，明确边界归属，避免重复实现。

#### BR-2 启动/常驻双环境姿势

- **业务需求**：任何环境启动工作台都一次成功、能常驻、能干净停止；不再因漏环境变量 401、
  nohup 被回收、pkill 自杀而白费启动轮次。
- **业务逻辑**：
  - 启动必带 `WORKBENCH_LOCAL_FULL_ACCESS=1`（v2 免 token 前提，漏掉 capabilities 即 401）；
    headless 加 `HPLC_OPEN_WORKBENCH=0`。
  - 常驻按环境二选一：**agent 沙箱/容器 → 受管后台任务**（nohup 子进程随外层 shell 结束被回收，
    实测验证）；**桌面/WSL 人机 → nohup**。
  - 停止禁用 `pkill -f "workbench.run"`（自匹配误杀），先 pgrep 拿 PID 或终止受管后台任务。

#### BR-3 行为红线固化（pkill 自匹配、nohup 边界）

- **业务需求**：把已发生过的"自杀式 pkill""nohup 常驻失效"固化为红线条款，而非追加使用经验条例。
- **业务逻辑**：红线新增 7（禁用 pkill/pgrep -f 匹配自身命令行）与 8（agent 沙箱禁用 nohup 常驻）。

#### BR-4 使用经验定位边界（防条例膨胀、防双份漂移）

- **业务需求**：技能维护不再靠"追加使用经验条例"；可固化的内容进正文，使用经验只留跨环境一次性事实。
- **业务逻辑**：SKILL.md「路径根解析」段补「使用经验/ 定位」条款——只放跨环境一次性事实与过程复盘；
  能固化为边界条款的一律直接写进 SKILL.md / references 正文。

#### BR-5 离线排查数据源归档清单

- **业务需求**：问题归档时应带结构化库（索引库 + simcon 库等），AI 离线排查直接查权威源，不必重解析。
- **业务逻辑**：offline-analysis.md 新增 §0「问题归档导出清单」：必带 索引库（idx-*.sqlite3 +
  catalog.json）/ 侦听台原始日志 / 模拟集中器库（listener_13762.sqlite + wal/shm）/ simcon 会话帧
  （sc-*.jsonl）/ CCO 模块日志 / 页面 xlsx；标注缺位后果与最少三件套（索引库 + CCO 日志 + 原始日志）。

#### BR-6 离线日志索引入口文档化

- **业务需求**：AI 对任意路径原始日志建索引的入口可查（使用经验 #5 缺口闭环），不翻源码。
- **业务逻辑**：listener.md 新增「离线日志索引」节：`POST /api/listener/logs/open`（8790 网关路径；
  `/api/logs/open` 是 8765 独立版内部路径、经网关 404）→ `GET /api/listener/logs/status` 轮询 →
  `indexes/{id}/frames` 查帧；串口采集中 409，先 listener/stop。

### 3.2 v2 capabilities 自描述最小调用链（P1，代码类，待实现）

#### BR-7 capabilities 响应自带最小调用链示例

- **业务需求**：AI **不加载技能**时，仅凭 `GET /api/ai/v2/capabilities` 响应即可拿到每项能力的
  最小 HTTP 调用链（"一键小抄"），消灭"找入口"环节，减少翻文档/源码的 token 浪费。
- **业务逻辑**：
  - `CapabilitySnapshot` 增加 `call_examples` 字段：每项 capability 的最小调用链数组
    （HTTP 方法与路径，`{id}` 占位），与 SKILL.md「任务 → 最小路径速查」表口径一致。
  - `capability_snapshot()` 静态填充（不改动服务端逻辑；能力是否 allowed 不变）。
  - 至少覆盖：investigations、jobs、evidence（L1/L2/L3）、module-actions、verification-runs、
    flash-jobs、jobs/cancel。
  - 契约兼容：新增字段为可选（默认空数组），旧客户端不破坏；`test_ai_v2_api.py` 增加断言
    （local_full 与 lan_scoped 两种 access 下响应均含 call_examples 且格式正确）。
- **验收**：GET /api/ai/v2/capabilities 响应中每项 capability 带 `call_examples`，示例与
  SKILL.md 速查表一致；单测全绿。

### 3.3 技能同步与登记（P2）

- SKILL.md 版本 v2.6.0 → v2.7.0（已在 P0 完成并提交前的编辑中体现）；全局库 git 提交留痕。
- 工作区 reqs/0033 登记 REQS-INDEX.md。
- 技能校验 `verify_api_inventory.py` 通过（不启服务）。

## 4. 边界

- **只改边界与自描述，不改后端行为**：P1 仅给 capabilities 响应加调用链示例字段，不动
  investigation/module-action/verification/flash 的服务端语义与 v1 兼容。
- **技能维护不回填使用经验条例**：本需求的技能改动全部落在 SKILL.md / references 正文；
  `使用经验/工作台使用不便记录.md` 只保留历史记录，不新增条目。
- **离线优先不替代在线**：离线路径仅用于"文件在手"场景；真实下发/串口/烧录/取证仍走 v2/v1。
- **mclt 专项归属**：分钟采集离线分析归 mclt-collect-analysis 技能；ai-control-plane 只做路由指向。

## 5. 验收

- [x] SKILL.md 顶部有「在线/离线判定序」，路由表含 mclt 专项行（BR-1）。
- [x] SKILL.md 启动段为双环境姿势 + 环境变量前置 + 停止姿势；红线 7/8 存在（BR-2/BR-3）。
- [x] SKILL.md「路径根解析」含使用经验定位条款（BR-4）。
- [x] offline-analysis.md §0 归档导出清单存在（BR-5）。
- [x] listener.md「离线日志索引」节存在，含 8790 网关路径与 409 互斥说明（BR-6）。
- [x] capabilities 响应每项带 call_examples，与 SKILL.md 速查表一致（BR-7，2026-09-21 实现：local_full/lan_scoped 双用例断言通过）。
- [x] 单测全绿（test_ai_v2_api.py 27 passed；全量回归 344 passed / 7 failed 全为存量——store 查询 5 + profile_loading 2，均经 stash 基线验证与 BR-7 无关）。
- [ ] 全局库 git 提交留痕；REQS-INDEX.md 0033 登记（登记已完成）；技能校验脚本通过（EXIT=0 已验，2026-09-21）。

## 6. 变更记录

### 变更 1 ｜ 2026-09-21 ｜ 用户
- **改成什么**: 需求建立（基线 v1.0）：① ai-control-plane 技能使用边界优化（判定序/启动双环境/
  红线固化/使用经验定位/归档清单/离线日志索引，P0 文档类）；② v2 capabilities 自描述最小调用链
  （P1 代码类）。
- **为什么**: 用户要求维护技能边界（不得以增加使用经验条例的方式）+ 设计方案、建 req、逐项说明
  业务需求与业务逻辑、再写 plan；核查确认 capabilities 响应缺"一键小抄"、归档缺结构化库。
- **影响**: 新建 reqs/0033-ai-control-plane-boundary/ 并登记 REQS-INDEX.md；
  ai-control-plane 全局库 v2.7.0；apps/workbench ai_contracts/ai_v2_api/test_ai_v2_api。
- **变更前基线**: 无（新建）
- **变更后基线**: v1.0
- **被取代**: 无。PLAN.md 动工前由 writing-plans 生成。
