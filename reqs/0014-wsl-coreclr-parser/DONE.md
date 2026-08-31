# DONE — REQS-0014 国网 HPLC 解析库双目标构建

> 完成记录只追加，最新记录置顶。当前尚无完成的实施阶段。

## 2026-08-31｜阶段 2-4 完成

- WSL/Linux 不再因平台被直接禁用：Python.NET 在导入 clr 前选择 CoreCLR，
  并自动定位 bin/Debug/net8.0/GwHPLCAnalysis.dll；Windows 保持原 net48
  DLL 与默认 CLR 宿主。
- 修正 JSON 契约：net8 的 System.Text.Json 将 byte[] 显式输出为数值数组，
  与 net48 JavaScriptSerializer 一致，不再输出 Base64。
- 新增跨进程 golden 对比工具和单测。Windows net48 与 WSL net8 对 6 类
  报文的 simple/full JSON 严格相等；唯一白名单为 version.date。
- 实测 Windows、WSL 的 listener：GET /api/version 返回 dll_available=true，
  POST /api/parse 返回 200，解析结果 ProType=GW。
- 交付 WSL/CoreCLR 与 Windows/net48 构建运行手册：RUNBOOK.md。

### 验证证据

| 项 | 结果 |
|---|---|
| Python 目标测试 | 23 passed |
| JSON/差分工具单测 | 5 passed |
| Windows net48 构建 | 成功；仅既有缺失 ConcurrencyRules.ruleset 警告 |
| Windows/WSL net8 构建 | 成功；仅既有 sniffer 小写命名警告 |
| Windows/WSL listener API | 均为 version 200、parse 200、ProType=GW |
| 跨环境 golden diff | equal，6 cases，白名单 version.date |
| 全量 pytest | 1268 passed，68 skipped，4 failed，耗时 48.50s |

### 独立风险（不归因于 REQS-0014）

全量回归的 4 项失败均为静态 UI 基线失配，改动文件不在本需求 allowlist：

1. apps/listener/test_ui_layout.py 的 3 项布局断言，期望旧 operation-panel /
   bounded viewport 结构，但当前页面已是新版 frames-pro 结构。
2. apps/workbench/test_app.py::test_trace_dict_pages_no_cache，断言 trace.js?v=trace-v1，
   当前静态页引用 trace.js?v=trace-v2。

阶段 0 曾单独复现的 concurrent_meter nested_698 失败在本次完整回归中未再次出现；
原风险记录保留，不将其状态变化归因于本需求。
