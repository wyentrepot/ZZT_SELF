# RUNBOOK — REQS-0019 Windows 解析服务 + WSL 远程解析降级

> 运行手册：把深度解析委托给 Windows（net48 DLL），WSL 侦听台三档降级
> local → remote → none。实机验证通过（2026-09-01）。

## 架构

```
WSL apps/listener ── ParserService ← 门面三档：
   1. DotNetHplcParser(net8.0 DLL)        → local
   2. RemoteHplcParser → HTTP 172.25.0.1:8700 → remote
   3. None（采集照常，解析 503）           → none
                        │ Windows 防火墙需放行 8700
Windows .build_plain ── apps/parser_service（FastAPI，net48 DLL，0.0.0.0:8700）
```

## 一、Windows 侧部署（在 `.build_plain` 明文区，规避 E-SafeNet）

0. **一键部署脚本**（推荐）：桌面 `wsl环境部署.bat`（源：`tools/scripts/`）——
   菜单含串口映射(1/2/3) 与 **解析网关(4 启动 / 5 停止)**；也支持非交互
   `powershell -File uart-map.ps1 -Action start-gateway|stop-gateway|status`。
1. **同步代码**：`.build_plain` 是本仓库的明文 git 工作树。
   - 更新：`git fetch origin && git checkout -f -B codex/0019-win-parse-service origin/codex/0019-win-parse-service`
   - ⚠️ 必须用 **WSL 侧 git** 执行（WSL 写出的文件为明文）；用 Windows `git.exe` 会把文件写回 E-SafeNet 密文。
2. **DLL**：确认 `libs\shared\dll\bin\Debug\GwHPLCAnalysis.dll`（net48）存在；
   缺失则先在明文区构建 `libs\shared\dll\DLL_NwHPLCAnalysis.csproj`。
3. **Python 环境**：复用主工作区 venv `D:\019-wy-tool\ZZT_SELF\.venv`（已验证含
   fastapi/uvicorn/httpx/pythonnet）。也可在 `.build_plain` 新建 venv。
4. **防火墙放行**（管理员 PowerShell）：
   `New-NetFirewallRule -DisplayName "HPLC_Parse_Service" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8700`
5. **启动服务**（双击 `apps\parser_service\启动解析服务.bat`，或后台脚本）：
   `PYTHONPATH=.build_plain\apps;.build_plain\libs` + `python -m parser_service.run`
   → 绑定 `0.0.0.0:8700`（`HPLC_PARSE_HOST` 可覆盖）。
6. **自检**：`http://127.0.0.1:8700/health` → `{"status":"ok","dll_available":true}`

## 二、WSL 侧

- 配置 `config/remote_parse.json`：`{"version":1,"url":"http://172.25.0.1:8700"}`
  （`172.25.0.1` 为 WSL→宿主的默认网关；WSL 重启后若子网变化需更新；
  也可用 `HPLC_REMOTE_PARSE_URL` 环境变量覆盖）。
- 侦听台启动后 `/api/version` 应显示 `parse_backend: remote`、`dll_available: true`。

## 三、验证

```bash
curl http://172.25.0.1:8700/health          # Windows 服务
curl http://127.0.0.1:8765/api/version      # WSL 侦听台 → parse_backend=remote
curl -X POST http://127.0.0.1:8765/api/parse -H 'Content-Type: application/json' \
  -d '{"hex":"7E ... 7E"}'                  # 真实帧经远程解析
```

## 四、常见故障

| 现象 | 原因 / 处理 |
|---|---|
| WSL 访问 `172.25.0.1:8700` 超时 | 防火墙未放行 8700（见上第 4 步）；或 WSL 网关 IP 变了（`ip route` 查默认网关） |
| WSL 访问 `127.0.0.1:8700` 拒绝 | 本机 WSL2 localhost 转发不可达；改用网关 IP，或切 mirrored 模式 |
| 服务启动报 `null bytes` | 读到了 E-SafeNet 密文：确认代码在 `.build_plain` 明文区且由 WSL 侧 git 写出 |
| 启动报端口占用 | 已有实例在跑；`netstat -ano | findstr 8700` 找到 PID 结束 |
| 侦听台 `parse_backend=none` | 本地无 net8.0 DLL 且远程服务不可达（启动时探测失败）——降级符合预期 |
