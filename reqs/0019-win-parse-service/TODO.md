# REQS-0019 TODO

> 变更记录只追加不覆盖。

## P0 — 设计定稿与登记

- [x] REQS.md / TODO.md / DONE.md 建立；REQS-INDEX.md 登记 0019
- [x] 架构切法确认：串口在 WSL，Windows 纯解析服务，三档降级 local→remote→none

## P1 — 共享远程解析客户端（WSL 侧 libs）

- [x] `libs/shared/remote_parser.py`：`RemoteHplcParser` 实现 `DllParser` 协议
      （parse_simple / parse_full 单请求缓存、version、`/health` 探测）
- [x] 配置读取：`HPLC_REMOTE_PARSE_URL` 环境变量 + `config/remote_parse.json` 兜底
- [x] 单测：httpx MockTransport 假服务（raw simple/full）、422/503 映射、缓存命中、
      `/health` 探测失败语义

## P2 — Windows 解析服务（apps/parser_service）

- [x] `app.py`：FastAPI 纯解析（`DotNetHplcParser` net48 + `normalize_hex_frame` + 串行锁，
      不做 enrich）；`/health`、`/api/version`、`/api/parse`
- [x] `run.py`（uvicorn 8700，默认 0.0.0.0，`HPLC_PARSE_HOST` 可覆盖）+ `requirements.txt`
      + `启动解析服务.bat`（`.build_plain` 明文区启动、校验 DLL、首次建 venv）
- [x] 单测：TestClient + fake parser（协议类）；422/503 映射；version/health

## P3 — WSL 侦听台门面三档降级

- [x] `apps/listener/app.py`：`_build_parser_service` 本地 → 远程 → None；
      `/api/version` 增加 `parse_backend`（local/remote/none）
- [x] 单测：三档降级矩阵 + `parse_backend` 字段 + 503 语义

## P4 — Windows 部署与联调（真机）

- [x] `.build_plain` 同步代码（git 强制覆盖，WSL git 写出明文规避 E-SafeNet）；
      复用主工作区 venv（fastapi/uvicorn/httpx/pythonnet 已具备）；服务已启动并验证
- [x] WSL→Windows 远程解析联调：`/health`、`/api/version`、`/api/parse` 可用；
      golden 帧远程与 net48 直解 **simple/full 完全一致**
- [x] 降级验证：停服务 → `parse_backend=none`、`/api/parse` 503，侦听台存活
- [x] 网络打通：Windows 防火墙放行 8700（用户管理员执行），WSL 经 `172.25.0.1:8700` 访问
- [x] 部署脚本：桌面 `wsl环境部署.bat` + `tools/scripts/uart-map.ps1`（菜单含
      串口映射 1/2/3 + 解析网关 4 启动 / 5 停止；支持 `-Action start-gateway|stop-gateway|status`）

## P5 — 文档与收口

- [x] RUNBOOK：Windows 部署 `.build_plain` + WSL 联调 + 常见故障（本目录 RUNBOOK.md）
- [x] AI 操作指南补「解析后端 local/remote/none」说明（docs/16-AI操作指南.md + 全局技能
      ai-control-plane v2.1.0：SKILL.md 通用约定 + references/listener.md 解析后端节）
- [x] 回归（apps/listener、libs/shared）+ REQS-INDEX 状态更新

## 验收门

- [x] Windows 服务 `/health`、`/api/version`、`/api/parse` 可用（net48 DLL）
- [x] WSL 三档降级正确（local / remote / none），golden 帧 remote 与 net48 直解一致
- [x] 停服务后采集与日志不受影响，`/api/parse` 返回 503 且诊断清晰
- [x] 全程源码直跑在 `.build_plain` 明文区（规避 E-SafeNet）

## 日志

- 2026-09-01 需求建立：分支 `codex/0019-win-parse-service`；P0 完成。
- 2026-09-01 P1-P3 实施：25 项单测全绿；listener/shared 回归仅剩 13 项既有环境失败。
- 2026-09-01 P4 真机联调完成：WSL→Windows 链路打通（防火墙放行 8700），golden 一致，
  三档降级验证通过；服务在 `.build_plain` 明文区运行。
