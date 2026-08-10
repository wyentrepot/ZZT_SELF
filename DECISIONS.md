# 决策记录（DECISIONS.md）

本文件采用 ADR（Architecture Decision Record）精简版模式：**决策只追加、不覆盖**。已有记录永不修改、不删除；被取代的只把活动决策表里状态改为「❌ 已取代」，正文不动。

## 活动决策表

| # | 标题 | 状态 |
|---|------|------|
| 1 | 项目拆分为 listener / module_log 双应用 + shared 共享库 | ✅ 生效 |

---

## ADR-1 项目拆分为 listener / module_log 双应用 + shared 共享库

- **日期**：2026-08-11
- **状态**：✅ 生效
- **决定**：将原 `hplc_web/` 单包拆分为三个平级顶层目录——`listener/`（侦听台，端口 8765）、`module_log/`（模块日志/烧录，端口 8766）、`shared/`（共享基础设施与解析链路）。`parser_lib/` 保留在仓库根作为独立共享解析库。
- **理由**：
  - 原 `hplc_web/` 单包同时承载侦听台与模块串口两套路由，二者只是进程/端口独立，代码仍耦合（`listener_app` 与 `module_serial_app` 互相通过 `from hplc_web import app` 共享）。
  - 用户希望两个应用完全解耦，仅靠一键启动脚本（`启动工具.bat`）统一拉起。
  - `parser_lib` 未来会被其他项目调用，故保留为仓库根独立共享库；`dll` 与解析链路归入 `shared/`。
- **影响**：
  - `listener/` 与 `module_log/` 各自拥有独立 `app.py`（create_app 工厂）与 `run.py`（uvicorn 入口），互不 import。
  - `shared/infra.py` 抽取通用工具（文件选择/盘符/目录列举），两个应用复用。
  - 启动脚本：根目录 `启动工具.bat`（菜单 1/2/3）+ 各项目独立启动 bat。
  - 测试保持在各项目内（`listener/test_*.py`、`module_log/test_*.py`、`shared/test_*.py`、`parser_lib/.../tests/`），全量 `pytest listener module_log shared parser_lib` 通过（290 passed / 66 skipped）。
- **被取代**：无（首次记录）。
