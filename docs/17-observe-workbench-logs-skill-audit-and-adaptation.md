# `observe-workbench-logs` 技能审核与适配设计

## 需求

将项目内 `skills/observe-workbench-logs/` 与当前 workbench 的 `/api/ai/v1`
实现对齐。技能只能显式调用，默认 dry-run，且不得启动、停止、发送、烧录或直接
打开真实串口。它允许创建有界、可审计的 observation operation，因此必须描述为
“硬件非侵入”，而不是服务端无状态的“只读”。

本次不安装技能、不启动 workbench、不使用 `--execute`，更不进行硬件操作。

## 设计

1. 固定六个 allowlisted 命令与 `allow_implicit_invocation: false`；observe 固定
   `ensure_source_running=false` 和 `on_finish=leave_running`。
2. 元数据改为本工程 WSL 路径；说明最小 scope、operation/audit 副作用，以及
   Artifact 仅取 manifest、不读取 `/content`。
3. 客户端按 source 构造当前后端请求：module 使用文本匹配；listener 使用
   `parsed_frame` 或 `frame_query`、`frame_kind`、selector；cursor range 使用
   `index_id + start_frame_id + end_frame_id`。
4. `--base-url` 与 `--execute` 无论在子命令前后均有效；`--client-request-id`
   仅作为 POST 的 `Idempotency-Key`，供人工显式重试复用。
5. 输出序列化后总会替换实际环境 Token；base URL 仅允许本机 loopback，防止
   授权 Token 被发往局域网主机。

## 验证与非目标

- RED：公共参数位置、listener 请求 shape、幂等 header、Token 回显保护。
- GREEN：仅修改项目内技能源码、说明与 mock 测试；运行技能单测与后端 AI 回归。
- 不改 AI 后端、授权模型、串口 mapping 或真实硬件状态。
