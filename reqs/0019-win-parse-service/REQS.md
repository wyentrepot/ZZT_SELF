# REQS-0019 — Windows 解析服务 + WSL 远程解析降级

> 状态：🚧 进行中
> 创建：2026-09-01
> 分支：`codex/0019-win-parse-service`
> 关联：REQS-0014（双目标解析库）、REQS-0007（parser facade/ports）、
>       REQS-0003（Windows 串口网关——已屏蔽，本需求不复活它）

## 1. 背景与问题

WSL 侧没有 net8.0 解析库**构建产物**（git 忽略，需本地 `dotnet build` 重建），
导致侦听台深度解析在 WSL 不可用；Windows 侧 `.build_plain`（明文区）已有
net48 `GwHPLCAnalysis.dll`，且解析链路验证可用。

同时 Windows 主工作区（`D:\019-wy-tool\ZZT_SELF`）受 **E-SafeNet 透明加密**：
非白名单进程读 `.py` 源文件得到密文（`SyntaxError: source code string cannot
contain null bytes`），源码直跑失败；`.build_plain` 是明文构建区，作为
Windows 侧运行根（已核实其文件头为明文，主工作区文件头为 `E-SafeNet...LOCK`）。

与用户确认的架构切法（此前只讨论的结论）：
- **串口留在 WSL**（usbipd 直通 `/dev/ttyUSB`），采集 / 日志 / 证据链不变；
- **Windows 起纯解析服务**（net48 DLL，复用 `ParserService`/`DotNetHplcParser`），
  不碰串口、不采集；
- **WSL 解析门面三档降级**：本地 net8.0 DLL → 远程 Windows 服务 → 无
  （采集/日志/证据照常，仅深度解析降级，接口给明确诊断）。

## 2. 目标

| 项 | 说明 |
|---|---|
| Windows 解析服务 | 新增 `apps/parser_service`：FastAPI 纯解析服务，端口 **8700**，暴露 `/health`、`/api/version`、`/api/parse`（raw simple/full），从 `.build_plain` 明文区启动，bat 启动器 |
| 共享远程解析客户端 | `libs/shared/remote_parser.py`：`RemoteHplcParser` 实现 `DllParser` 协议（parse_simple/parse_full/version），**每帧一次 HTTP 往返** |
| WSL 侦听台门面 | `_build_parser_service` 三档：本地 → 远程 → None；`/api/version` 增加 `parse_backend: local/remote/none` 诊断字段 |
| 配置 | `HPLC_REMOTE_PARSE_URL` 环境变量 + `config/remote_parse.json` 兜底 |
| 测试 | WSL 单测（RemoteHplcParser + 门面降级矩阵）、Windows 服务单测（TestClient + fake parser）、golden 帧一致 |
| 文档 | AI 操作指南补「解析后端 local/remote/none」说明 + RUNBOOK（Windows 部署 .build_plain + WSL 联调） |

## 3. 边界（不纳入）

1. 不改串口归属、串口采集状态机、烧录流程；**不复活 REQS-0003（Windows 串口网关）**。
2. 不重写 / 不改 C# 解析库；不提交 DLL 与构建产物（沿用 REQS-0014 资产规则）。
3. 不把整个侦听台迁到 Windows；Windows 服务**纯解析**：不打开串口、不落日志、不做采集。
4. 不做 Windows 服务自启 / 守护 / 安装为 Windows Service（本次手动 + bat 启动即可）。
5. 远程解析只覆盖深度解析；帧采集、落库、证据链仍在 WSL 完成。

## 4. 接口契约（Windows 服务 ↔ WSL 客户端）

| 端点 | 请求 | 响应 |
|---|---|---|
| `GET /health` | — | `{"status":"ok","dll_available":bool}` |
| `GET /api/version` | — | `{"name","version","date"}`（与 `DotNetHplcParser.version()` 同形） |
| `POST /api/parse` | `{"hex":"<hex>"}` | 200 `{"simple":"<json str>","full":"<json str>"}`（**raw**，不做 enrich）；422 帧校验失败；503 DLL 不可用 |

> 为什么 raw：enrich（`ApplicationAnalysisService`）留在 WSL 侧 `ParserService`
> 做，避免双重 enrich；契约保持「远程 = 裸解析后端」，与 `DotNetHplcParser` 对齐。

## 5. 降级矩阵（WSL 侦听台）

| 档位 | 条件 | 行为 | `parse_backend` |
|---|---|---|---|
| 1 | WSL 有 net8.0 DLL | 本地解析 | `local` |
| 2 | 无 DLL，但 Windows 服务可达 | 远程解析（每帧 1 次 HTTP，localhost 往返 ~1ms 级） | `remote` |
| 3 | 无 DLL 且服务不可达 | 采集 / 日志 / 证据照常，深度解析 `/api/parse` 503 | `none` |

## 6. 风险

| 项 | 处理 |
|---|---|
| E-SafeNet 加密 | 服务必须在 `.build_plain` 明文区运行；bat 启动前校验 DLL 存在性 |
| 每帧 HTTP 往返 | localhost ~1ms 级；批量 / 回放可接受；高吞吐场景再评估批处理 |
| 运行中远端掉线 | 启动探测 + 请求异常按 503 / 降级处理；采集与落库不受影响 |
| WSL2 localhost 转发 | Windows 服务绑定 127.0.0.1，WSL 经 `127.0.0.1:8700` 可达；不达时用宿主 IP / mirror 模式 |

## 7. 变更记录

### 变更 1 ｜ 2026-09-01 ｜ 用户
- **改成什么**：新增 Windows 解析服务 + WSL 远程解析降级（local → remote → none）。
- **为什么**：WSL 无 net8.0 构建产物且暂无重建计划；Windows 侧 DLL 与解析链路已可用。
- **影响**：新增 `apps/parser_service`、`libs/shared/remote_parser.py`；
  `apps/listener/app.py` 解析门面与 `/api/version`；`config/`；AI 操作指南与 RUNBOOK。
- **被取代**：无（新需求）。
