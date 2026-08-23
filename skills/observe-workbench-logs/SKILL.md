---
name: observe-workbench-logs
description: 'Query HPLC meter-reading workbench log observation state and evidence over HTTP as an AI, using the /api/ai/v1 observation and evidence API. Read-only observation: check workbench status, create a bounded observation on an EXISTING module session or listener index, wait to a terminal state, read registered artifacts, query listener frame schema, and fetch frame detail by index_id+frame_id composite key. Use when an AI agent needs to watch HPLC module logs or listener frames for a pattern and retrieve evidence, e.g. "观察 CCO 日志等待出现 central beacon"、"查侦听台某帧详情". Never ensure/starts/stops/flashes serial; this skill is observation-only.'
metadata:
  author: reasonix
  version: "1.0.0"
  applies-to: D:/2-侦听台改造
---

# 侦听台 AI 观察技能（observe-workbench-logs）

项目内、**显式调用**、**默认 dry-run** 的 AI 日志观察技能。底层事实以
`docs/16-AI操作指南.md` 与代码为准；本文件是**执行步骤**，只读观察、不碰硬件写操作。

## 边界（先读）

- 本技能**只观察**：不提供、不隐藏 `ensure/start/stop/send/flash/烧录/串口打开/文件扫描`。
- 只能通过 `$observe-workbench-logs` 显式调用；`allow_implicit_invocation: false`。
- 目标只能是**既有** module session 或**既有** listener index/capture；不新建、不启动来源。
- 默认 dry-run：只输出脱敏计划，零 HTTP、零 operation。只有 `--execute` 才发请求。
- Token 唯一来自环境变量 `WORKBENCH_AI_TOKEN`；不得出现在参数、输出、异常或 URL。
- 单次服务端 wait ≤ 30 秒；终态即停止，绝不发 cancel。

## 前置条件

1. workbench 在 8790 运行（`GET /api/health` → 200）。
2. 已有人工授权并持有 Token，且环境变量 `WORKBENCH_AI_TOKEN` 已设置（只读 scope 即可：
   `status:read`、`observation:create`、`evidence:read`）。
3. 目标串口会话或侦听台索引**已存在**（`status` 可查）。

## 六命令

| 命令 | 端点 | 说明 |
|---|---|---|
| `status` | `GET /api/ai/v1/status` | 查工作台/会话/观察任务状态 |
| `observe` | `POST /api/ai/v1/observations` | 对既有 session/index 创建有界观察 |
| `wait` | `GET /api/ai/v1/operations/{id}/wait` | 有界轮询到终态，不 cancel |
| `artifact` | `GET /api/ai/v1/artifacts/{id}` | 读服务端登记的 Artifact |
| `listener-schema` | `GET /api/ai/v1/listener/schema` | 查侦听台帧语义与字段选择器 |
| `frame-detail` | `GET /api/ai/v1/listener/indexes/{index_id}/frames/{frame_id}` | 复合深链读帧 |

## 第一步：先查状态（只读）

```bash
cd skills/observe-workbench-logs/scripts
python workbench_ai_client.py status            # dry-run：打印计划，零 HTTP
python workbench_ai_client.py status --execute  # 真查状态，需 WORKBENCH_AI_TOKEN
```

- 无 Token 时 `status --execute` 安全失败并提示设置 `WORKBENCH_AI_TOKEN`。
- 输出是稳定 JSON：会话列表、运行状态、活动观察任务。

## 第二步：创建观察（默认 dry-run）

把自然语言转成结构化参数；**关键参数缺失时停止并请求补充**：

```bash
# 默认 dry-run：只打印脱敏计划
python workbench_ai_client.py observe \
  --source module_log --session-id ms-1234 \
  --kind literal --value "central beacon first-of-minute flag=1" \
  --mode live --timeout-seconds 120

# 确认无误后 --execute 才真正 POST
python workbench_ai_client.py observe \
  --source module_log --session-id ms-1234 \
  --kind literal --value "central beacon first-of-minute flag=1" \
  --mode live --timeout-seconds 120 --execute
```

- `--source module_log` 必须给 `--session-id`（既有后端会话）；`--source listener` 必须给 `--index-id`（既有索引）。
- `--kind`：`literal` / `regex` / `loghook_rule` / `sequence` / `not_seen`；`--value` 为 literal/regex 文本或 rule_id。
- `--mode`：`live`（等新数据）或 `cursor_range`（`--start-seq/--end-seq` 精确复跑，模块日志）。
- `lifecycle.ensure_source_running` 固定为 `false`：本技能**不会**帮你启动串口来源。

## 第三步：等待终态

```bash
python workbench_ai_client.py wait --operation-id op-log-xxx --timeout-seconds 30 --execute
```

- 返回 `matched / timed_out / cancelled / error / interrupted / source_stopped` 终态。
- 单次 ≤ 30 秒；非终态（`waiting/created`）可再次 wait，客户端到终态即停，不发 cancel。
- `matched` 时结果含 `log.artifact_id`（module）或复合 `index_id + frame_id`（listener）。

## 第四步：取证据

```bash
python workbench_ai_client.py artifact --artifact-id op-log-xxx-raw --execute
python workbench_ai_client.py frame-detail --index-id idx-7 --frame-id 42 --execute
python workbench_ai_client.py listener-schema --execute
```

- `artifact`：服务端登记的逻辑 Artifact ID，返回可下载证据与命中位置。
- `frame-detail`：必须保留 `index_id + frame_id` 复合深链，返回解析 JSON 与详情 URL。
- `listener-schema`：可查询的帧字段与语义选择器。

## 错误处理与安全

- 服务端非 2xx、Token 缺失/非法、base URL 含 userinfo、缺 session/index → **安全失败**，
  脱敏错误 + 非零退出码，绝不回显 Token。
- 不自动扩大权限、不强制停止来源、不重试破坏性操作。
- 断线/超时/技能退出均不关闭后端串口（`leave_running`）。
