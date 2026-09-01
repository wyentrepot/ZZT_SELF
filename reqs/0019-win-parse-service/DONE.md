# DONE — REQS-0019 Windows 解析服务 + WSL 远程解析降级

> 完成记录只追加，最新记录置顶。

## 2026-09-01 ｜ 全部完成，合入 master

### 交付

- **Windows 解析服务** `apps/parser_service`：FastAPI 纯解析（net48 DLL，`0.0.0.0:8700`），
  `/health`、`/api/version`、`/api/parse`（raw simple/full）。
- **WSL 远程客户端** `libs/shared/remote_parser.py`：`RemoteHplcParser` 实现 `DllParser`
  协议（单请求缓存），三档门面 local → remote → none（`apps/listener/app.py`，
  `/api/version` 增 `parse_backend` 字段）。
- **配置**：`config/remote_parse.json`（`http://172.25.0.1:8700`）+ `HPLC_REMOTE_PARSE_URL`。
- **部署工具**：`tools/scripts/wsl环境部署.bat` + `uart-map.ps1`（串口映射 1/2/3 +
  解析网关 4/5，支持 `-Action` 子命令）；`tools/README.md`、`tools/scripts/README.md`。
- **全局技能**：ai-control-plane v2.1.0（`wyentrepot/skills.git` main `62e3b6d`）——
  解析后端 local/remote/none 说明 + Windows 网关操作；`docs/16-AI操作指南.md` 同步。

### 验收证据

| 项 | 结果 |
|---|---|
| 25 项新单测 | 全绿；listener/shared 回归仅剩 13 项既有环境失败（master 复现，与本次无关） |
| Windows 服务 | `/health` ok、`/api/version` GW_SMAnalysis V1.0.23 |
| golden 一致性 | 远程解析 vs net48 直解 simple/full 完全一致（真实 SOF 帧） |
| 三档降级 | 服务在 → `remote`；停 → `none` + 503，侦听台存活 |
| 网络 | 防火墙放行 8700（用户管理员），WSL 经 `172.25.0.1:8700` 访问 |

### 关键技术处置

- E-SafeNet 透明加密：`git.exe` 写出即密文；**WSL 侧 git / bash 写出才是明文**，故
  `.build_plain` 部署一律用 WSL git 强制覆盖，服务在明文区直跑源码。
- WSL2 localhost 转发本机不可达、防火墙默认全拦（仅 3389 放行）；按用户拍板放行 8700
  后经网关 IP 打通；服务默认绑 `0.0.0.0`（`HPLC_PARSE_HOST` 可覆盖）。
