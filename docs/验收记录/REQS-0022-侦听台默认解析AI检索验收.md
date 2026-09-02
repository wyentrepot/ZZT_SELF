# REQS-0022 侦听台默认解析 AI 检索验收

> 生成时间：2026-09-03 01:22
> 解析后端：`dotnet`（GwHPLCAnalysis.dll）
> 执行：`python tools/scripts/test_listener_ai_existing_parser_sample.py --max-lines 50000 --p95-ms 500`
> 只读验收：样本仅作输入，临时索引落在 `.tmp/reqs-0022-listener-sample/`，未改样本、未建运行时索引。

## 结论

| 项 | 结果 |
| --- | --- |
| 默认解析字段可复用 | ✅ pass |
| trace 能力被 AI 复用（trace_query） | ✅ pass |
| 分钟采集权威不变（minute_periods） | ✅ pass |
| 证据分层（L3 完整帧 JSON） | ✅ pass |
| 性能门（温热 app_id+NID+时间窗 P95 ≤ 500ms） | ✅ pass（8.0ms） |

## 1. trace_query（并发抄表）

- 源：`测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt`（303MB，只读前 50000 帧）
- 读取字节 SHA-256：`566d48f42be2424f0883d52bca74bee056c966a16fef3fb0ed04f1c5dec98018`
- `app_id=0003` 宽查询结果：**1154 个通信流 / 10743 帧**
- 方向分布：downlink 9952 / uplink 249 / ack 542
- NID：`00947F69`
- L3 等价验证：`get_index_frame` 返回 `raw_hex` / `summary` / `analysis` 均存在
- 性能：温热收窄查询（`app_id=0003` + NID + 时间窗）P95 = **8.0ms**

结论：并发抄表的多跳通信流、上下行、NID、源/目的与帧级完整 JSON 均被既有 `TraceService` 正确表达，AI 只需透传特征即可复用。

## 2. minute_periods（分钟采集）

- 源：`测试文件/模块快日志/侦听台 - 副本/`（4 个 COM4_*.txt，共 46296 帧）
- 读取字节 SHA-256：`3f303fe23f19f2d3a31dc7634e24703bff6421c49b2c6b99aee8a702f2b62ef8`
- 分钟采集记录：**37 条**（APP_ID=00E4），task_no=1、cco_tei=001
- 周期：由 00E2 任务配置推导 `derived_period_minutes=2`
- L2 关键字段验证：`freeze_time`（如 `2026-08-12 17:04:00`）、`response_result`、`frame_id` ref 均存在
- 配置周期内 report 1 条；配置周期外上报 36 条（`unconfigured_reports`，同样含 freeze_time）

结论：分钟采集复用既有 `list_task_minute_periods`，业务归属以 `freeze_time` 为准，未通过 raw HEX 猜测。

## 3. 缺失项登记

| 项 | 状态 |
| --- | --- |
| 303MB 原始报文全量索引 | `coverage_missing`（本次只读前 50000 帧；全量建索引耗时过长，超出验收必要范围） |
| 全量样本 SHA-256 | `coverage_missing`（记录的是「已读字节」SHA-256，非整个 303MB 文件） |

## 4. 覆盖说明

本次验收按计划 Phase 4 Step 1–3 执行；样本缺失/未覆盖项一律记 `coverage_missing`，不做虚假通过（REQS-0022 全局约束 8）。
