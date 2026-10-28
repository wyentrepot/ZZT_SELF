# HPLC 抄表工作台（工装）· 项目档案卡（PROJECT-CARD.md）

> 项目事实落点；岗位卡不得包含本卡内容。建卡：Chief 2026-10-14，只追加。

## 1. 定位

- 仓库根 `<WORKBENCH_ROOT>` = `/01-workfile-ai/01-zzt/ZZT_SELF`
- 路径解析顺序：`WORKBENCH_ROOT` 环境变量 → `~/.config/workbench/repo-root`（本机已配置）→ 从 cwd 向上探测 `apps/workbench`
- 服务：`http://127.0.0.1:8790`（`python3 <WORKBENCH_ROOT>/apps/workbench/run.py`）
- 启动必带：`WORKBENCH_LOCAL_FULL_ACCESS=1`；无图形环境加 `HPLC_OPEN_WORKBENCH=0`；沙箱里必须用受管后台任务常驻（禁 nohup）

## 2. 两条路径

- **离线轻量档（优先，不起工作台）**：`python3 <WORKBENCH_ROOT>/tools/scripts/appframe.py parse|build|verify`，覆盖 1376.2（内嵌 698/645 递归）、698.45、645-2007；库级 API `libs/parser_lib/facade.py`
- **在线全功能档**：v2 门面 `/api/ai/v2/*`（capabilities → 任务 → jobs → evidence L1/L2/L3）；v1 专家路径见技能 references

## 3. 专项

- 分钟采集（MCLT）漏报/11E4/11E3/时钟偏移 → 用 `mclt-collect-analysis` 技能
- 侦听台窄带解析依赖 Windows 侧解析网关（仅 Windows、由人执行）

## 4. 现场约定

- 证据优先离线：手头有帧/日志就不默认起工作台
- 串口独占、烧录只认 `iap_*.bin`、烧录与 verify 不可取消、授权只由人签发
- 停止工作台：先 `pgrep -af "[w]orkbench.run"` 拿 PID 再 kill（禁用 `pkill -f` 自匹配）

## 5. 相关归档

- 台体问题现场日志与侦听台原始报文：`/01-issuse/台体issuse/`（9p 只读，读用）
- 重庆台体日志：`/mnt/d/001-日志文件/09-重庆物联网表/台体问题/`（9p 只读）
