# 后端 API 接口总清单

> 统计时间：2026-09-02
> 代码基线：`4ed9384`（pull `origin/master`，Fast-forward 自 `4fecd6a`）
> 统计范围：**全部后端 HTTP 接口**（不只 AI 专用接口）
> 统计口径：FastAPI 路由装饰器去重后计数；同一函数挂多个路径别名只算 1 个

---

## 0. 速览：138 个接口，按层分布

| # | 层次 | 前缀 | 数量 | 鉴权 | 服务对象 |
| --- | --- | --- | --- | --- | --- |
| 1 | **AI 控制面** | `/api/ai/v1` | **34** | Bearer token + scope | **AI / Agent（专用）** |
| 2 | 验证编排 REST | `/api` | 13 | 无 | 页面 / CLI / AI 均可 |
| 3 | 协议字典 | `/api/dict` | 5 | 无 | 页面 + AI 查询 |
| 4 | 串口配置 | `/api/serial-profile`、`/api/serial-tags` | 5 | 无 | 页面 |
| 5 | workbench 平台自身 | `/`、`/api` | 3 | 无 | 运维 |
| 6 | listener 子应用 | `/api/*`（挂载后 `/api/listener/*`） | 31 | 无 | 页面 |
| 7 | module_log 子应用 | `/api/module-serial/*` | 31 | 无 | 页面 |
| 8 | simcon 子应用 | `/api/simcon/*` | 13 | 无 | 页面 |
| 9 | parser_service（新） | `/health`、`/api/*` | 3 | 无 | 跨机解析 |
| | **合计** | | **138** | | |

**一句话结论**：真正"为 AI 专门准备"的只有第 1 层 `/api/ai/v1`（34 个，带 token + scope 细粒度授权）；
其余 104 个是页面/服务间接口，AI 若要调用需用同一套 HTTP 但**无鉴权保护**。

---

## 1. AI 控制面 `/api/ai/v1`（34 个）— 核心，AI 专用

**定义文件**：`apps/workbench/ai_api.py`（`APIRouter(prefix="/api/ai/v1")`）
**只在 workbench 统一入口进程内存在**；listener / module_log 独立运行时无此层。
**鉴权**：`Authorization: Bearer <token>`；token 由管理员本地签发，按 scope + resource 双重受限。
**幂等**：写操作支持 `client_request_id` 或 `Idempotency-Key` 头。

### 1.1 授权管理（3 个，仅本机 + 管理密钥）

| 方法 | 路径 | 功能 | 鉴权方式 |
| --- | --- | --- | --- |
| GET | `/api/ai/v1/admin/grants` | 列出现有全部授权 | 仅 `127.0.0.1` + `X-Workbench-Admin-Key` |
| POST | `/api/ai/v1/admin/grants` | 签发新授权，返回明文 token（仅此一次可见） | 同上（201） |
| POST | `/api/ai/v1/admin/grants/{grant_id}/revoke` | 撤销指定授权 | 同上 |

### 1.2 状态与审计（2 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| GET | `/api/ai/v1/status` | 控制面整体状态；带 `evidence:read` 时额外返回路径信息 | `status:read` |
| GET | `/api/ai/v1/audit` | 审计条目（按授权可见的 resource 过滤） | `status:read` |

### 1.3 模块日志串口会话（3 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| POST | `/api/ai/v1/module-sessions/ensure` | **幂等确保**串口会话存在（不重复打开） | `module_session:ensure` |
| POST | `/api/ai/v1/module-sessions/{session_id}/stop` | 停止会话；会话被占用时 409，可 `force` | `module_session:stop` |
| POST | `/api/ai/v1/module-sessions/{session_id}/send` | 向模块下发指令（支持幂等键） | `module_send:execute` |

### 1.4 烧录（1 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| POST | `/api/ai/v1/flash-operations` | 异步烧录固件（202 + operation_id）。**固件路径必须落在授权白名单 `firmware_roots` 内**，否则 403 | `module_flash:execute` |

### 1.5 侦听台控制（2 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| POST | `/api/ai/v1/listener/ensure` | 确保侦听台在线（开串口开始采集），幂等 | `listener:ensure` |
| POST | `/api/ai/v1/listener/stop` | 停止侦听台 | `listener:stop` |

### 1.6 观察任务（异步 operation，4 个）

> AI 的**核心验证能力**：起一个观察任务 → 拿 operation_id → 等结果 → 取证据。

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| POST | `/api/ai/v1/observations` | 创建观察任务（202）。支持 module_log 实时日志 / 侦听台帧索引两种 target；`window.mode` 支持 `live` / `time_range` / `cursor_range` | `observation:create` |
| GET | `/api/ai/v1/operations/{operation_id}` | 查询 operation 当前状态与结果 | `evidence:read` |
| GET | `/api/ai/v1/operations/{operation_id}/wait` | 长轮询等待终态（`timeout_seconds` 0–30） | `evidence:read` |
| POST | `/api/ai/v1/operations/{operation_id}/cancel` | 取消进行中的 operation | `observation:create` |

### 1.7 证据产物（2 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| GET | `/api/ai/v1/artifacts/{artifact_id}` | 产物清单（manifest） | `evidence:read` |
| GET | `/api/ai/v1/artifacts/{artifact_id}/content` | 产物内容（正文/二进制） | `evidence:read` |

### 1.8 侦听台查询（6 个，全部 `evidence:read`）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/ai/v1/listener/schema` | 帧字段 schema（写 `match.where` 过滤前先读它） |
| GET | `/api/ai/v1/listener/indexes` | 列出帧索引库 |
| GET | `/api/ai/v1/listener/indexes/{index_id}/frames` | 分页查帧（offset/limit/query/nid/时间窗/after_id） |
| GET | `/api/ai/v1/listener/indexes/{index_id}/frames/{frame_id}` | 单帧完整详情 |
| GET | `/api/ai/v1/listener/minute-periods` | **分钟采集分桶分析**（REQS-0018 新增，按任务号 + 周期分钟数） |
| GET | `/api/ai/v1/listener/traces` | 列出通信流追踪句柄 |

### 1.9 通信流追踪（2 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| POST | `/api/ai/v1/listener/traces` | 创建追踪：`live` 注册句柄（持续更新快照）/ 回放模式直接出报告（202） | `listener:trace` |
| GET | `/api/ai/v1/listener/traces/{trace_id}` | 读取追踪当前快照 | `evidence:read` |

### 1.10 模拟集中器 simcon（11 个）

| 方法 | 路径 | 功能 | scope |
| --- | --- | --- | --- |
| POST | `/api/ai/v1/simcon/open` | 打开 simcon 串口 | `simcon:send` |
| POST | `/api/ai/v1/simcon/close` | 关闭 simcon 串口 | `simcon:send` |
| POST | `/api/ai/v1/simcon/verify` | 跑完整验证任务（202，异步 operation） | `simcon:verify` |
| POST | `/api/ai/v1/simcon/step` | 单步下发 / 等待一帧（感知 CCO 主动上报） | `simcon:send` |
| GET | `/api/ai/v1/simcon/session` | 当前 simcon 会话状态 | `simcon:read` |
| GET | `/api/ai/v1/simcon/frames` | 本次会话帧日志（按 run_id / direction / updown / afn / fn 过滤） | `simcon:read` |
| GET | `/api/ai/v1/simcon/store/events` | **06H 主动上报历史事件**（REQS-0018 新增，持久层） | `simcon:read` |
| GET | `/api/ai/v1/simcon/store/snapshots` | **查询快照列表**（REQS-0018 新增，临时层） | `simcon:read` |
| GET | `/api/ai/v1/simcon/store/snapshots/{snapshot_id}` | 快照明细行 | `simcon:read` |

> 小计 9 个端点。分层核对：1.1(3) + 1.2(2) + 1.3(3) + 1.4(1) + 1.5(2) + 1.6(4) + 1.7(2) + 1.8(6) + 1.9(2) + 1.10(9) = **34**

---

## 2. 验证编排 REST `/api`（13 个，无鉴权）

**定义文件**：`apps/workbench/api.py`
**用途**：全链路验证编排（烧录 → 监控 → 激励 → 比对 → 归因 → 报告）。页面 / CLI / AI 三端复用。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/scenarios` | 列出场景模板 |
| GET | `/api/scenarios/{scenario_id}` | 单个场景，并做模板合法性校验 |
| GET | `/api/scenarios/{scenario_id}/task` | 场景激励任务原始 JSON |
| POST | `/api/run` | 创建并异步执行验证批次（立即返回 `running`） |
| GET | `/api/run/{run_id}` | 查 Run 进度（轮询至终态） |
| POST | `/api/run/{run_id}/cancel` | 协作式取消 Run |
| GET | `/api/run/{run_id}/report` | 取报告 |
| GET | `/api/run/{run_id}/artifacts` | 产物清单 |
| GET | `/api/run/{run_id}/artifacts/{artifact_id}` | 按逻辑 ID 下载产物（路径越界防护） |
| GET | `/api/runs` | 最近 Run 列表（limit 1–200） |
| POST | `/api/compare` | 直接比对：期望流程 vs 实际事件流（不落 Run） |
| POST | `/api/feedback` | 直接归因：比对结论 + 激励结论 → 反馈（不落 Run） |
| GET | `/api/health` | 健康检查 |

---

## 3. 协议字典 `/api/dict`（5 个，无鉴权）

**定义文件**：`apps/workbench/dict_api.py`
数据直接读仓库真实字典文件，无拷贝加工 —— 改字典文件即刻生效。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/dict` | 字典清单（id / 名称 / 来源路径 / 条数） |
| GET | `/api/dict/oad` | 698.45 OAD 字典（`?q=` 模糊过滤） |
| GET | `/api/dict/di` | 645-2007 DI 字典 |
| GET | `/api/dict/afn-fn` | 1376.2 AFN/Fn 字典（含安徽扩展） |
| GET | `/api/dict/rules` | 模块日志事件识别规则（loghooks） |

---

## 4. 串口配置（5 个，无鉴权）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/serial-profile` | 读取已保存的串口 Profile（四槽） |
| PUT | `/api/serial-profile` | 只保存配置，**不操作硬件** |
| POST | `/api/serial-profile/apply` | 一键应用已保存版本到各串口服务 |
| GET | `/api/serial-tags` | 读角色↔COM 标签映射 + 在线端口列表 |
| PUT | `/api/serial-tags` | 保存标签映射（只落盘，不碰串口） |

---

## 5. workbench 平台自身（3 个）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/` | SPA 首页 |
| GET | `/api/platform-version` | 平台版本 + 子应用挂载状态（listener / module_log 是否就绪） |
| POST | `/api/shutdown` | 关闭服务（触发 uvicorn graceful 停机，进程退出不驻留） |

---

## 6. listener 子应用（31 个，无鉴权）

**定义文件**：`apps/listener/app.py`
**双部署**：独立运行时路径即下表"独立路径"；挂载进 workbench 后走"工作台路径"。
**映射规则**：代理剥掉 `/api/listener`，再补 `/api` 前缀（详见第 10 节）。

| 方法 | 独立路径 | 工作台路径 | 功能 |
| --- | --- | --- | --- |
| GET | `/` | — | 侦听台页面 |
| GET | `/api/version` | `/api/listener/version` | 版本与能力位 |
| POST | `/api/parse` | `/api/listener/parse` | 单帧 hex 解析 |
| POST | `/api/logs/open` | `/api/listener/logs/open` | 打开日志文件建索引（202） |
| GET | `/api/logs/status` | `/api/listener/logs/status` | 日志服务状态 |
| GET | `/api/logs/frames` | `/api/listener/logs/frames` | 分页取帧 |
| GET | `/api/logs/frames/{frame_id}` | `/api/listener/logs/frames/{frame_id}` | 单帧详情 |
| GET | `/api/logs/minute-analysis` | `/api/listener/logs/minute-analysis` | 分钟采集分析（周期可选 1–1440） |
| GET | `/api/logs/delete-config-details` | 同左加前缀 | 删除配置明细 |
| GET | `/api/logs/task-minute-analysis` | 同左加前缀 | 按任务号的分钟分析 |
| GET | `/api/logs/task-derived-period` | 同左加前缀 | 任务派生周期 |
| GET | `/api/logs/task-config-tasks` | 同左加前缀 | 任务配置：任务列表 |
| GET | `/api/logs/task-config-summary` | 同左加前缀 | 任务配置：汇总 |
| GET | `/api/logs/task-config-lifecycle` | 同左加前缀 | 任务配置：生命周期 |
| GET | `/api/network/assessment` | `/api/listener/network/assessment` | 网络承载评估（周期明细 + 汇总） |
| GET | `/api/network/status` | `/api/listener/network/status` | 网络评估轻量快照 |
| GET | `/api/fs/roots` | `/api/listener/fs/roots` | 盘符列表 |
| GET | `/api/fs/list` | `/api/listener/fs/list` | 列目录 |
| GET | `/api/fs/last` | `/api/listener/fs/last` | 上次打开的目录 |
| GET | `/api/fs/pick` | `/api/listener/fs/pick` | 原生文件选择器（仅 Windows） |
| GET | `/api/indexes` | `/api/listener/indexes` | 帧索引列表 |
| GET | `/api/indexes/{index_id}/frames` | `/api/listener/indexes/{index_id}/frames` | 索引内分页取帧 |
| GET | `/api/indexes/{index_id}/frames/{frame_id}` | 同左加前缀 | 索引内单帧详情 |
| GET | `/api/listener/indexes` | `/api/listener/listener/indexes` | 索引列表（`listener` 命名空间别名） |
| GET | `/api/listener/indexes/{index_id}/frames` | `/api/listener/listener/indexes/{id}/frames` | 同上，分页取帧（别名） |
| GET | `/api/listener/indexes/{index_id}/frames/{frame_id}` | 同左加前缀 | 同上，单帧详情（别名） |
| POST | `/api/listener/traces` | `/api/listener/listener/traces` | 创建通信流追踪（live=201 / 回放=200） |
| GET | `/api/listener/traces` | 同左加前缀 | 列出追踪 |
| GET | `/api/listener/traces/{trace_id}` | 同左加前缀 | 读追踪快照 |
| DELETE | `/api/listener/traces/{trace_id}` | 同左加前缀 | 停止并删除追踪 |
| GET | `/api/serial/ports` | `/api/listener/serial/ports` | 串口列表（含角色标签） |
| GET | `/api/serial/status` | `/api/listener/serial/status` | 串口采集状态 |
| POST | `/api/serial/start` | `/api/listener/serial/start` | 开始串口采集（202） |
| POST | `/api/serial/stop` | `/api/listener/serial/stop` | 停止串口采集 |

> 去重后计数 31（含首页）；上表列出 35 行是因为把"别名路径"也摊开了，便于对照。
> **AI 提示**：工作台下追踪请优先用 `/api/ai/v1/listener/traces`（带鉴权、带幂等），不要走这里的别名路径。

---

## 7. module_log 子应用（31 个，无鉴权）

**定义文件**：`apps/module_log/app.py`
**挂载方式**：`sub_root` 透传，独立路径与工作台路径**完全一致**。

### 7.1 多会话模式（12 个）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/module-serial/sessions` | 会话列表 |
| POST | `/api/module-serial/sessions` | 新建会话（201） |
| GET | `/api/module-serial/sessions/{session_id}` | 会话详情 |
| PATCH | `/api/module-serial/sessions/{session_id}` | 修改会话参数 |
| DELETE | `/api/module-serial/sessions/{session_id}` | 删除会话 |
| POST | `/api/module-serial/sessions/{session_id}/start` | 启动会话（202） |
| POST | `/api/module-serial/sessions/{session_id}/stop` | 停止会话 |
| POST | `/api/module-serial/sessions/{session_id}/write` | 下发 hex 数据 |
| POST | `/api/module-serial/sessions/{session_id}/write-text` | 下发文本（自动补换行） |
| POST | `/api/module-serial/sessions/{session_id}/baudrate` | 切换波特率 |
| POST | `/api/module-serial/sessions/{session_id}/flash` | 烧录固件 |
| GET | `/api/module-serial/sessions/{session_id}/logs` | 增量拉会话日志（`?after=`） |

### 7.2 单通道便捷模式（9 个）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/module-serial/version` | 版本信息 |
| GET | `/api/module-serial/ports` | 串口列表（含角色标签） |
| GET | `/api/module-serial/status` | 串口状态 |
| POST | `/api/module-serial/start` | 打开串口（202，cco/sta 双通道） |
| POST | `/api/module-serial/stop` | 关闭串口 |
| POST | `/api/module-serial/write` | 下发 hex |
| POST | `/api/module-serial/write_text` | 下发文本 |
| POST | `/api/module-serial/baudrate` | 切波特率 |
| POST | `/api/module-serial/flash` | 烧录（bin_path + slot + baud_plan） |

### 7.3 日志与文件系统（10 个）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/version` | 应用版本（不带 `module-serial` 前缀） |
| GET | `/module-serial` | 模块日志页面 |
| GET | `/api/module-serial/logs` | 拉模块日志（`?after=&channel=cco\|sta`） |
| POST | `/api/module-serial/upload` | 上传文件（name + base64，10 MB 上限） |
| GET | `/api/module-serial/fs/roots` | 盘符列表 |
| GET | `/api/module-serial/fs/list` | 列目录 |
| GET | `/api/module-serial/fs/pick` | 原生文件选择器 |
| GET | `/api/module-serial/loghooks/scan` | 扫描日志文件识别事件 |
| GET | `/api/module-serial/loghooks/realtime` | 实时日志事件流 |
| GET | `/api/loghooks/sources` | 可扫描日志文件清单（按模块分组） |

> 小计 10 个。`/api/fs/*`、`/api/loghooks/*` 另有不带 `module-serial` 前缀的别名，指向同一实现。
> 7.1(12) + 7.2(9) + 7.3(10) = **31**

---

## 8. simcon 子应用（13 个，无鉴权）

**定义文件**：`libs/sim_concentrator/api.py`
**挂载**：module_log 内挂 `/api/simcon`（`prefix=""`）；workbench 透传，路径一致。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/simcon/status` | 串口与会话状态 |
| GET | `/api/simcon/ports` | 串口列表 |
| GET | `/api/simcon/responders` | 内置应答规则清单 |
| POST | `/api/simcon/open` | 打开串口 |
| POST | `/api/simcon/close` | 关闭串口 |
| POST | `/api/simcon/verify` | 执行验证任务，返回逐步判定 + 汇总结论 |
| POST | `/api/simcon/step` | 单步语义执行：下发指定 afn/fn 或等待一帧 |
| POST | `/api/simcon/build` | **语义化构帧预览**（只算字节不触串口） |
| GET | `/api/simcon/frames` | 会话帧日志（按方向/afn/fn/run_id 过滤） |
| GET | `/api/simcon/session` | 当前/最近会话 |
| GET | `/api/simcon/store/snapshots` | 查询快照列表（临时层） |
| GET | `/api/simcon/store/snapshots/{snapshot_id}` | 快照明细行 |
| GET | `/api/simcon/store/events` | 06H 主动上报事件（持久层） |

---

## 9. parser_service（3 个，无鉴权）— 本次 pull 新增

**定义文件**：`apps/parser_service/app.py`
**定位**：Windows 侧纯解析服务，加载 net48 `GwHPLCAnalysis.dll`，只做裸解析（不 enrich、不采集、不碰串口）。
**降级**：DLL 缺失时 `/health` 返回 `dll_available=false`，`/api/parse` 返回 503。
**约束**：必须在明文区运行以规避 E-SafeNet 透明加密。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/health` | 健康 + DLL 可用性 |
| GET | `/api/version` | 解析库版本 |
| POST | `/api/parse` | 解析单帧 hex → `{simple, full}` |

---

## 10. 路径映射规则（双部署形态，别踩坑）

workbench 用 `_PrefixProxy` 剥前缀重写 `scope["path"]`，使子应用看到的路径与独立运行一致。

| 子应用 | 挂载前缀 | `sub_root` | 转换规则 | 示例 |
| --- | --- | --- | --- | --- |
| listener | `/api/listener` | `/api` | 剥 `/api/listener` → 补 `/api` | 外部 `/api/listener/logs/status` → 子应用 `/api/logs/status` |
| module_log | `/api/module-serial` | `/api/module-serial` | 透传（不变） | 外部与内部一致 |
| module_log(fs) | `/api/fs` | `/api/fs` | 透传 | 一致 |
| module_log(loghooks) | `/api/loghooks` | `/api/loghooks` | 透传 | 一致 |
| module_log(simcon) | `/api/simcon` | `/api/simcon` | 透传 | 一致 |

**为什么 listener 里同一函数挂两个路径**（如 `/api/indexes` 与 `/api/listener/indexes`）？
因为挂载后外部 `/api/listener/indexes` 会被重写成 `/api/indexes`，而**独立运行时**前端直接用 `/api/indexes`；
同时 AI/其他调用方可能按 `/api/listener/indexes` 直连。**两个别名是为兼容两种访问姿势**，不是冗余副本。

---

## 11. 给 AI 的推荐调用路径

```
1. 管理员本地签发 token
   POST /api/ai/v1/admin/grants  {scopes: [...], resources: [...], ttl_seconds: N}

2. 探活
   GET  /api/ai/v1/status            （scope: status:read）

3. 拉起设备
   POST /api/ai/v1/listener/ensure            （侦听台采集）
   POST /api/ai/v1/module-sessions/ensure     （模块日志串口）
   POST /api/ai/v1/simcon/open                （模拟集中器）

4. 下发 / 验证
   POST /api/ai/v1/module-sessions/{id}/send
   POST /api/ai/v1/simcon/step  或  /simcon/verify

5. 取证（三种取法，按场景选）
   a) 观察任务   POST /api/ai/v1/observations → GET /operations/{id}/wait
   b) 直接查询   GET  /api/ai/v1/listener/indexes/{id}/frames
                 GET  /api/ai/v1/simcon/frames
                 GET  /api/ai/v1/simcon/store/events
   c) 追踪链     POST /api/ai/v1/listener/traces → GET /listener/traces/{id}

6. 查字段 / 查字典（免鉴权，随时可查）
   GET  /api/ai/v1/listener/schema
   GET  /api/dict/afn-fn?fn=F230
```

---

## 12. 本次 pull 带来的接口变化

| 变化 | 内容 | 来源 |
| --- | --- | --- |
| 新增 3 个 AI 接口 | `/listener/minute-periods`、`/simcon/store/events`、`/simcon/store/snapshots`(+明细) | REQS-0018 |
| 新增模块 | `apps/parser_service`（3 个接口） | REQS-0019 |
| 新增串口配置接口 | `/api/serial-tags`（GET/PUT） | 本次 pull |
| 新增需求文档 | REQS-0017（AI 排查方法论）、0018（接收库只读查询）、0019（Windows 解析服务）、0020（真机测试） | 本次 pull |
| 新增 AI 技能 | `.agents/skills/ai-control-plane/references/`（cco-log、listener、offline-analysis、simcon） | 本次 pull |
