# 抄表帧（0x0001/0x0002/0x0003）Python 富化对接实施计划

> 更新：2026-08-02

## 问题描述

`hplc_web/application_service.py::enrich_summary` 只把 `APP_ID` 命中 `MINUTE_TYPES`
（00E2/00E3/00E4）的摘要交给 Python `DualMode43Adapter` 富化。抄表帧
（0x0001 终端主动抄表 / 0x0002 路由主动抄表 / 0x0003 终端主动并发抄表）的
`APP_ID` 是 `"0001"/"0002"/"0003"`，不在该字典中，因此 `enrich_summary`
直接返回 DLL 原始摘要——**DLL 侧只解析了抄表报文头部，MPDU 数据区（内嵌 645/698 帧）
未在网页详情中展示**。这对应交接需求表 R-06「并发抄表帧：正确识别应用层类型、
数据单元数量、地址、业务数据和响应关系」。

## 现状（已核实）

| 层 | 状态 | 位置 |
|---|---|---|
| DLL 输出 `APP_PORT/APP_ID/APP_RAW` | 已就绪 | `dll/src/intf.cs` 46-65、602-606 行 |
| DLL 识别抄表帧 `FrmType` | 已就绪 | `dll/src/hplcFrame.cs` 6641-6655 行 |
| Python `_parse_meter_business` 抄表头+DATA 递归 | 已实现 | `parser_lib/adapters/adapter_dualmode/__init__.py` 237-307 行 |
| Python 真实并发抄表帧测试 | 已存在 | `parser_lib/adapters/adapter_dualmode/tests/test_dualmode.py` 77-130 行 |
| **`enrich_summary` 路由抄表帧** | **断点** | `hplc_web/application_service.py` 52 行 |

## 验收标准

1. `ApplicationAnalysisService.enrich_summary()` 对 `APP_ID ∈ {"0001","0002","0003"}`
   的摘要调用 `DualMode43Adapter.decode(APP_RAW)`，输出含 `application` 结构化字段，
   其中 `application.nested` 按规约类型递归解出内嵌 645/698 帧。
2. 抄表帧 `FrmType` 提升为 `"终端主动抄表"/"路由主动抄表"/"终端主动并发抄表"`，
   `BaseFrmType` 保留 DLL 原值（DLL V1.0.23 重编译后原值即为"终端主动并发抄表"）。
3. 既有分钟采集行为不回归：E2/E3/E4 提升、未知 APP_ID 原样返回、适配器失败保留
   `application_error` 且 `FrmType` 不变，全部保持。
4. 自动化验证通过：`hplc_web` 全量测试通过（原 49 项 + 新增抄表帧用例），
   `parser_lib` 全量测试通过（107 passed / 66 skipped 基线不变）。

## 阻碍项（提前说明）

1. ~~命名不一致~~ **已解决（2026-08-02 DLL 重编译）**：DLL 对 0x0003 原输出
   `FrmType="终端并发抄表"`（hplcFrame.cs 6652 行），Python `_MESSAGE_NAMES` 为
   `"终端主动并发抄表"`。已统一为协议标准名"终端主动并发抄表"（两处）并重新编译
   DLL，Python 侧无需改动。
2. ~~DLL 电能表地址拷贝 bug~~ **已解决（2026-08-02 DLL 重编译）**：
   `dll/src/hplcFrame.cs` 中 `Array.Copy(buf, start + 9, down_up.电能表地址, 0, 5)`
   只拷 5 字节，但类定义 `byte[6]` 且 `msdu_sof_s_para_extract_gw` 读 6 字节
   （第 6 字节恒为 0）。已将 6 处拷贝改为 6 字节并重新编译。
3. ~~端到端真实验收受限~~ **已解决（2026-08-02）**：用户提供
   `测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt`（303MB 真实大日志，
   含 87355 条并发抄表帧）。已流式提取 200 条受控样本（`测试文件/并发抄表-样本.txt`）
   并逐帧验收：200/200 识别为 `APP_ID=0003`、富化 `FrmType=终端主动并发抄表`、
   每帧递归出 1 个内嵌 698.45 帧，无解析异常；验收固化为
   `hplc_web/tests/test_concurrent_meter_e2e.py`（4 用例）。

## 执行任务

- [x] T1 修改 `hplc_web/application_service.py`：新增 `METER_TYPES` 映射，
      扩展 `enrich_summary` 路由，抄表帧也走 Python 适配器。
- [x] T2 扩展 `hplc_web/tests/test_application_service.py`：新增抄表帧富化用例
      （0003 真实帧 + 0001/0002 构造帧 + 失败保留原摘要）。
- [x] T2.5 DLL 修复与重编译：命名统一为"终端主动并发抄表"（两处）、电能表地址
      拷贝 5→6 字节（6 处），MSBuild Rebuild 成功。
- [x] T2.6 端到端验收：提取真实大日志 200 条受控样本，固化为
      `hplc_web/tests/test_concurrent_meter_e2e.py`（4 用例全部通过）。
- [x] T3 运行 `hplc_web` 全量测试（56 passed）+ `parser_lib` 全量测试
      （108 passed / 66 skipped），确认无回归。
- [x] T4 更新 `doc/任务交接需求与进度表.md`：问题描述、验收标准、完成状态。
