---
name: ai-control-plane
description: Control real hardware (HPLC meter-reading workbench) over HTTP as an AI. Covers obtaining an authorization token via the human-admin key, then driving serial sessions (cco/sta modules), firmware flashing, observation/evidence capture, and listener-frame queries through the workbench AI control plane at /api/ai/v1. Use when an AI agent needs to operate the 侦听台改造 workbench (serial ports, flash, log observation, evidence retrieval) programmatically, e.g. "用 AI 控制台向 cco/sta 发串口指令"、"AI 烧录固件并等结果"、"AI 观察日志并取证".
argument-hint: "[what to do, e.g. 向 cco 发送一串字符]"
metadata:
  author: reasonix
  version: "1.0.0"
  applies-to: D:/2-侦听台改造
---

# AI 控制面使用 Skill（ai-control-plane）

让 AI 通过 HTTP 操作真机（HPLC 侦听台改造工作台）的完整 playbook。
底层事实以 `docs/16-AI操作指南.md` 与代码为准；本文是**执行步骤**，实测于 2026-08-20。

## 适用场景

- 向 cco / sta 模块串口（COM1 / COM2）发送字符串或十六进制
- 烧录固件并等待结果
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
  -d '{"scopes":["status:read","module_session:ensure","module_session:stop","module_send:execute","module_flash:execute","listener:ensure","listener:stop","observation:create","evidence:read"],"resources":["*"],"ttl_seconds":3600,"reason":"<用途>"}'
```

- 响应里 `token` 只返回一次，之后只存 SHA-256 摘要。**务必保存 token。**
- `ttl_seconds`：1–86400；`resources:["*"]`=全部；只给本次要用的 scope 即可（最小权限）。
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
  -d '{"module":"cco","port":"COM1","serial":{"baudrate":115200,"bytesize":8,"parity":"N","stopbits":1},"title":"<可选>"}'
```

- 也可用 `mapping_id`（`cco-main`/`sta-main`）代替 `port`，但**本机真实串口是 COM1/COM2**，直接 `port` 最稳。
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

### 建侦听台帧观察

```bash
curl -X POST http://127.0.0.1:8790/api/ai/v1/observations \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"source":"listener","target":{"mapping_id":"listener-main","capture":"current"}}'
```

### 轮询 + 取证

```bash
curl "http://127.0.0.1:8790/api/ai/v1/operations/<operation_id>/wait?timeout_seconds=30" \
  -H "Authorization: Bearer <token>"
# 命中 → state=matched，result.log.artifact_id = art-xxx
curl "http://127.0.0.1:8790/api/ai/v1/artifacts/<artifact_id>/content" \
  -H "Authorization: Bearer <token>"
```

- `matched` 的 `result.snippet` 含命中上下文行，`result.log` 含行号区间与日志路径——这就是给 AI 的证据。
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
curl "http://127.0.0.1:8790/api/ai/v1/listener/indexes/<index_id>/frames?limit=100" -H "Authorization: Bearer <token>"
curl "http://127.0.0.1:8790/api/ai/v1/listener/indexes/<index_id>/frames/<frame_id>" -H "Authorization: Bearer <token>"
```

## 推荐调用顺序（AI 侧 checklist）

1. 人/密钥发 grant → 保存 token（一次性）。
2. `GET /status` 确认后端与串口就绪。
3. `POST /module-sessions/ensure` 开会话。
4. （可选）`POST /flash-operations` 烧录 → `wait` 到 succeeded。
5. `POST /observations` 建观察 → **再制造目标事件** → `wait` 到 matched。
6. `GET /artifacts/<id>/content` 取证据正文，用于验证结论。
7. 收尾：`stop` 会话、`listener/stop`，释放串口。

## 与前端的关系（重要）

- AI 与前端**不互斥**，共享同一套后端服务与串口资源。
- **同一物理串口同一时刻只能一个持有者**：AI 开着 cco(COM1)，前端想用同一 COM1 会失败；但前端可继续用其他口、看页面、操作未被占用的串口。AI 用完 stop 即释放。
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
