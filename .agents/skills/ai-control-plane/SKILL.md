---
name: ai-control-plane
description: Control real hardware (HPLC meter-reading workbench) over HTTP as an AI. Covers obtaining an authorization token via the human-admin key, then driving serial sessions (cco/sta modules), firmware flashing, observation/evidence capture, and listener-frame queries through the workbench AI control plane at /api/ai/v1. Use when an AI agent needs to operate the 侦听台改造 workbench (serial ports, flash, log observation, evidence retrieval) programmatically, e.g. "用 AI 控制台向 cco/sta 发串口指令"、"AI 烧录固件并等结果"、"AI 观察日志并取证".
argument-hint: "[what to do, e.g. 向 cco 发送一串字符]"
metadata:
  author: reasonix
  version: "1.3.0"
  applies-to: D:/2-侦听台改造
---

# AI 控制面使用 Skill（ai-control-plane）

让 AI 通过 HTTP 操作真机（HPLC 侦听台改造工作台）的完整 playbook。
底层事实以 `docs/16-AI操作指南.md` 与代码为准；本文是**执行步骤**，实测于 2026-08-20，2026-08-29 对齐当前实现（cursor_range 观察、regex/sequence 匹配器、帧过滤参数、串口映射 JSON）。

## 适用场景

- 向 cco / sta 模块串口发送字符串或十六进制
- 烧录固件并等待结果
- 驱动模拟集中器：跑验证用例、单步下发指定 afn、感知 CCO 主动上报、查本次运行的帧
- 建观察任务：盯 module_log 实时日志或侦听台帧，命中后取证
- 查询侦听台已解析帧、整体状态、审计流水

## 前置条件（先检查）

1. workbench 在 8790 运行（`curl http://127.0.0.1:8790/api/health` → 200）。
2. 人工授权密钥已配置（`tools/scripts/一键生成AI密钥.bat` 一键装好，或环境变量 `WORKBENCH_AI_ADMIN_KEY`）。
3. 拿到一个**有效 token**（第 1 步）。AI 全程只用 token，不再用密钥。

## 第一步：拿授权 token（必须由人/密钥完成）

```bash
# 在 workbench 所在机器本机执行（/admin/grants 仅限 127.0.0.1 + 密钥）
curl -X POST http://127.0.0.1:8790/api/ai/v1/admin/grants \
  -H "X-Workbench-Admin-Key: <WORKBENCH_AI_ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"scopes":["status:read","module_session:ensure","module_session:stop","module_send:execute","module_flash:execute","listener:ensure","listener:stop","listener:trace","observation:create","evidence:read","simcon:verify","simcon:send","simcon:read"],"resources":["*"],"ttl_seconds":3600,"firmware_roots":["D:/firmware"],"reason":"<用途>"}'
```

- 响应里 `token` 只返回一次，之后只存 SHA-256 摘要。**务必保存 token。**
- `ttl_seconds`：1–86400；`resources:["*"]`=全部；只给本次要用的 scope 即可（最小权限）。
- `firmware_roots`：允许烧录的目录白名单，**要烧录就必须给**，否则 flash 返回 403「当前授权未配置允许烧录目录」。`max_operation_seconds` 可选（默认 1800）。
- 授权管理辅助：`GET /admin/grants` 列出授权、`POST /admin/grants/{grant_id}/revoke` 撤销（均 admin key）；`GET /audit` 审计流水（token，需 `status:read`）。
- 若返回 `503 未配置人工授权管理密钥`：密钥没配/没重启，先让人类跑一键脚本并重启 8790。
- 若返回 `403`：密钥错，或不是从 127.0.0.1 发起。

## 通用调用约定

- Base：`http://127.0.0.1:8790/api/ai/v1`（局域网则换成 workbench 主机 IP）。
- 请求头：`Authorization: Bearer <token>`，`Content-Type: application/json`。
- 每个接口校验 scope + resource：缺 token → 401；scope/resource 越权 → 403；串口占用 → 409；后端不可用 → 503；请求体非法 → 422。
- 长任务（烧录/观察）返回 `operation_id`，用 `wait` 轮询到终态。

## 第二步：查状态（可选）

```bash
curl http://127.0.0.1:8790/api/ai/v1/status -H "Authorization: Bearer <token>"
```

返回 workbench / listener / module_sessions / 活跃 operations / 串口句柄快照。

## 第三步：串口会话

### ensure（幂等，创建或复用并打开串口）

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/module-sessions/ensure \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"module":"cco","mapping_id":"cco-main"}'
```

- **优先用 `mapping_id`**（`cco-main`/`sta-main`，见 `config/serial_ports.json`），波特率等串口参数自动按映射配置走。也可直接 `port`，但本机实际接线会变（当前配置：cco-main=COM9、sta-main=COM8、listener=COM4、simcon=COM19），**别硬编码 COM 号**。
- 返回 `session_id`（形如 `ms-xxxx`），state=running 表示串口已开。
- 409 = 串口被占用（前端或另一会话占着），等释放或改口。

### 发送

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/module-sessions/<session_id>/send \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"text":"你的字符串","append_newline":true,"client_request_id":"<幂等ID>"}'
# 或十六进制：{"data_hex":"68 00 ..."}
```

- 200 + state=succeeded 即成功；`result.sent` 是字节数。

### 停止

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/module-sessions/<session_id>/stop \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"force": false}'
```

- 409 + 需 `"force":true`：会话有活跃依赖（如观察任务）时普通 stop 拒绝。

## 第四步：烧录固件（异步）

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/flash-operations \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"session_id":"<session_id>","bin_path":"D:/firmware/app.bin","slot":0,"client_request_id":"<id>"}'
```

- 授权里的 `firmware_roots` 必须包含 `bin_path`，否则 403「未配置允许烧录目录」。
- 返回 202 + `operation_id`；轮询：
  `GET /operations/<operation_id>/wait?timeout_seconds=30` → `succeeded`（含 flash 结果）/ `error` / `timed_out`。

## 第五步：观察任务 + 取证（核心验证能力）

**时序关键：先建观察，再制造目标事件。** `module_log` 观察只盯「创建时刻之后」的新日志。

### 建 module_log 观察

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/observations \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
    "source":"module_log",
    "target":{"session_id":"<session_id>"},
    "window":{"mode":"live","start":"now","timeout_seconds":180},
    "match":{"kind":"literal","value":"<要找的字符串>","case_sensitive":false},
    "context":{"before":20,"after":30},
    "client_request_id":"<id>"
  }'
```

- 幂等 ID 也可用请求头 `Idempotency-Key` 传，`client_request_id` 缺省时取它。
- `match`（module_log）三种叶子：`literal`（非空 ≤512 字符）、`regex`（≤256 字符）、`loghook_rule`（`{"kind":"loghook_rule","rule_id":"..."}`，须为该模块的 module_log 规则）；两种复合：`{"kind":"sequence","steps":[<叶子>…1–16 个],"max_interval_ms":<1–3600000>}`（按顺序出现）、`{"kind":"not_seen","matcher":<叶子>}`（窗口内**未**出现即成功）。
- `window.mode`：`live`（只盯创建之后的新日志，`timeout_seconds` 1–3600）/ `time_range`（ISO 8601 `start`/`end`，须落在当前内存日志边界内）/ `cursor_range`（`start_seq`/`end_seq` 回放既有区间，跨度 ≤10000 行且区间须已闭合）。

### 建侦听台帧观察

`match` **必填**（缺省直接 422），kind 仅 `parsed_frame` / `frame_query`：

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/observations \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
    "source":"listener",
    "target":{"mapping_id":"listener","capture":"current"},
    "window":{"mode":"live","timeout_seconds":180},
    "match":{"kind":"parsed_frame","frame_kind":"central_beacon","selector":"first"}
  }'
```

- 过滤三件套：`frame_kind`（当前仅 `central_beacon`，留空=任意）、`where`（数组，每项 `{"path":"analysis.full.<字段>","op":"eq","value":...}`，op 目前仅 eq）、`selector`（`first`/`last`/`all`/`first_per_minute`/`nth`）。
- `mapping_id` 用 `listener`（不是 listener-main）；字段路径先 `GET /listener/schema` 确认。
- `window.mode` 同样支持 `live` / `time_range`（`start`/`end`）/ `cursor_range`（须给 `index_id` + `start_frame_id`/`end_frame_id`，跨度 ≤500 帧）。

### 轮询 + 取证

```bash
curl "http://127.0.0.1:8790/api/ai/v1/operations/<operation_id>/wait?timeout_seconds=30" \
  -H "Authorization: Bearer <token>"
# 命中 → state=matched，result.log.artifact_id = art-xxx
curl "http://127.0.0.1:8790/api/ai/v1/artifacts/<artifact_id>/content" \
  -H "Authorization: Bearer <token>"
```

- `matched` 的 `result.snippet` 含命中上下文行，`result.log` 含行号区间与日志路径——这就是给 AI 的证据。
- Artifact 元数据：`GET /artifacts/<artifact_id>`；正文：`GET /artifacts/<artifact_id>/content`。
- 取消：`POST /operations/<id>/cancel`（烧录不可取消）。

## 第六步：侦听台控制与帧查询

```bash
# 控制
curl -X POST http://127.0.0.1:8790/api/ai/v1/listener/ensure \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"mapping_id":"listener"}'
curl -X POST http://127.0.0.1:8790/api/ai/v1/listener/stop \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"force":false}'

# 查询
curl http://127.0.0.1:8790/api/ai/v1/listener/schema -H "Authorization: Bearer <token>"
curl http://127.0.0.1:8790/api/ai/v1/listener/indexes -H "Authorization: Bearer <token>"
curl "http://127.0.0.1:8790/api/ai/v1/listener/indexes/<index_id>/frames?offset=0&limit=100" -H "Authorization: Bearer <token>"
curl "http://127.0.0.1:8790/api/ai/v1/listener/indexes/<index_id>/frames/<frame_id>" -H "Authorization: Bearer <token>"
```

- 帧分页参数：`offset`、`limit`（1–500）、`query`（关键字）、`nid`、`start_time`/`end_time`（HH:MM:SS 或 HH:MM:SS.mmm）、`after_id`（游标翻页，取上一页最后一条 frame_id）。
- `listener:stop` 校验的 resource：侦听台在线时为其当前 mapping_id（`listener`），离线时回退 `listener-main`；窄授权（`resources` 非 `*`）需两者都包含。

## 第七步：侦听台通信流追踪（三段证据链）

以「一次发送的特征」追踪一轮业务（如并发抄表 0x0003）的三段判定：
S1 发出（下行帧捕获）→ S2a ACK（链路确认）→ S2b 响应（同报文序号上行）→
S3 接收（0x0020 显式或簇内无重传推断），输出「断在哪一跳」而非二值 pass/fail。

```bash
# 回放：特征+时间窗 → 202 → wait → result.report（完整报告）
curl -X POST http://127.0.0.1:8790/api/ai/v1/listener/traces   -H "Authorization: Bearer <token>" -H "Content-Type: application/json"   -d '{"scope":"round","window":{"mode":"time_range","start_time":"10:00:00","end_time":"10:10:00"},"feature":{"app_id":"0003"}}'

# live：只盯注册之后的新帧 → wait → result.trace.trace_id
curl -X POST http://127.0.0.1:8790/api/ai/v1/listener/traces   -H "Authorization: Bearer <token>" -H "Content-Type: application/json"   -d '{"scope":"round","window":{"mode":"live"},"feature":{"app_id":"0003"}}'

# live 快照读取 / 句柄列表（scope evidence:read）
curl http://127.0.0.1:8790/api/ai/v1/listener/traces/<trace_id> -H "Authorization: Bearer <token>"
curl http://127.0.0.1:8790/api/ai/v1/listener/traces -H "Authorization: Bearer <token>"
```

- `feature`：`app_id` 必填（0003 并发抄表 / 0001 单表 / 00A1 / 0020 / 0008）；`msg_seq` 留空=聚合；
  `frm_type`/`dst_tei`/`nid`/`app_raw_contains` 可选。scope 粒度：flow（须给 msg_seq）/ round / campaign。
- 报告：`summary` + `rounds[]`（`flows[]` 状态机链每阶段挂 `frame_id`，可回第六步帧详情钻取；
  `meter_table` 表地址三分类 ok/denied/missing）+ `proxy_graph` + `bad_frames`（坏帧只计数）。
- 单帧详情响应的 `feature_hint` 就是可反推的特征草稿，改一改即可 POST /traces。

## 第八步：模拟集中器——验证任务 / 单步下发 / 帧日志

模拟集中器（simcon，串口映射 `simcon`=COM19/9600/E）的 AI 接口在
`/api/ai/v1/simcon/*`，resource 固定 `simcon`；每次收发的 1376.2 帧都会进
**会话帧日志**并持久化到 `data/logs/simcon/sc-*.jsonl`。

### 运行验证任务（异步）

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/simcon/verify \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"id":"t1","profile":"anhui","steps":[{"send":{"afn":"00","fn":1,"params":{}}}],"client_request_id":"v-001"}'
# 202 + operation_id → GET /operations/<id>/wait 到 succeeded
# result 含 steps/summary/run_id/frames_seq（本次运行的帧 seq 区间）
```

- `send` 只写 `afn/fn + params`（ADR-5，`raw` 报错）；profile 在 `apps/workbench/scenarios/profiles/`。
- 并发 verify 返回 409；不可取消。

### 单步下发 / 感知主动上报（同步）

```bash
# 下发指定 afn/fn（串口未开时自动按 simcon 映射打开）
curl -X POST http://127.0.0.1:8790/api/ai/v1/simcon/step \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"send":{"afn":"06","fn":"F230","params":{}},"client_request_id":"s-001"}'

# 只等一帧：感知 CCO 主动上报
curl -X POST http://127.0.0.1:8790/api/ai/v1/simcon/step \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"recv_only":true,"expect":{"afn":6,"fn":230},"expect_timeout":30}'
```

### 查询本次运行的帧

```bash
# 本次下发过什么帧
curl "http://127.0.0.1:8790/api/ai/v1/simcon/frames?run_id=<run_id>&direction=tx" -H "Authorization: Bearer <token>"
# CCO 主动上报过什么帧 / 有无某类 afn 上行帧
curl "http://127.0.0.1:8790/api/ai/v1/simcon/frames?updown=up&afn=06" -H "Authorization: Bearer <token>"
```

- 过滤：`direction`(tx/rx)、`updown`(up/down)、`afn`、`fn`、`kind`(step_send/manual_send/auto_reply)、
  `run_id`、`session_id`、`after_seq`+`limit`(≤500) 游标翻页；每帧含 `frame_hex`/`parsed` 解析结果。
- `GET /simcon/session` 查会话信息；`POST /simcon/open`、`POST /simcon/close` 显式管理（close 释放串口，日志保留）。

## 推荐调用顺序（AI 侧 checklist）

1. 人/密钥发 grant → 保存 token（一次性）。
2. `GET /status` 确认后端与串口就绪。
3. `POST /module-sessions/ensure` 开会话。
4. （可选）`POST /flash-operations` 烧录 → `wait` 到 succeeded。
5. `POST /observations` 建观察 → **再制造目标事件** → `wait` 到 matched。
6. `GET /artifacts/<id>/content` 取证据正文，用于验证结论。
7. 侦听台侧：`POST /listener/traces` 追踪一轮业务的三段证据链（回放或 live，见第七步）；
   帧详情 `feature_hint` 直接作特征草稿。
8. 模拟集中器侧：`POST /simcon/verify` 跑用例 / `POST /simcon/step` 单步 → `GET /simcon/frames` 查帧。
9. 收尾：`stop` 会话、`listener/stop`、`POST /simcon/close`，释放串口。

## 与前端的关系（重要）

- AI 与前端**不互斥**，共享同一套后端服务与串口资源。
- **同一物理串口同一时刻只能一个持有者**：AI 开着 cco 所在串口，前端想用同一串口会失败；但前端可继续用其他口、看页面、操作未被占用的串口。AI 用完 stop 即释放。
- 别把「串口被占用」当成 bug——这是物理独占规则。

## 错误码速查

| 状态码 | 含义 |
|---|---|
| 401 | 缺 token / token 无效过期撤销 |
| 403 | scope 或 resource 越权 / 固件路径不在授权目录 / 非本机发授权 |
| 404 | 会话 / operation / Artifact / 索引不存在 |
| 409 | 串口被占用 / 会话冲突 / 停止需 force |
| 422 | 请求体校验失败 |
| 503 | 后端服务不可用 / 未配置 admin key / 串口打不开 |

## 参考

- 完整说明：`docs/16-AI操作指南.md`
- 实现：`apps/workbench/ai_api.py`、`ai_operations.py`、`ai_auth.py`、`ai_store.py`
- 一键密钥：`tools/scripts/一键生成AI密钥.bat`
- 决策：DECISIONS.md ADR-28（开放 0.0.0.0 局域网监听）
