# Minute Collection Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correctly identify dual-mode application messages `0x00E2`, `0x00E3`, and `0x00E4`, then provide a new web page that counts deduplicated minute-collection reports received by CCO TEI `001` in aligned time periods.

**Architecture:** Keep `GwHPLCAnalysis.dll` authoritative for the sniffer envelope, FCH, MAC, TEI, and exact APS byte boundary. Expose the bounded APS bytes in the DLL summary, pass only those bytes to the Python `DualMode43Adapter`, and merge its structured application result into the existing summary JSON. During log indexing, persist recognized `0x00E4` reports into a dedicated SQLite table so period statistics do not rescan or reparse the source log.

**Tech Stack:** C#/.NET Framework 4.8, Python 3.13, Python.NET, FastAPI, SQLite, vanilla JavaScript/CSS, unittest/pytest.

## 第一阶段验收基准（必须精确满足）

- 唯一的第一阶段验收输入文件是 `测试文件/测试文本.txt`。不得以“大日志”、300 行抽样文件或此前引用的副本替代此文件。
- 文件必须读取并建立索引共 **50 条**日志记录；不允许因为应用层解析失败而丢行或中断建索引。
- 必须识别出 **23 条** `APP_PORT=11、APP_ID=00E4` 的分钟采集主动上报；本文件中 `00E2=0`、`00E3=0`。
- 其余 **27 条**是反例，必须保持原有帧类型，不能仅因报文其他位置出现相似字节而误判为分钟采集。
- 23 条 E4 都必须满足：主动上报头长度 8、转发报文长度 94、任务号 7、协议类型 2、响应结果 0、上报数量 1、数据长度 76。
- 每条 E4 的 76 字节数据区必须完整消费，并解析出 **4 条**内嵌 645 帧；合计 **92 条**，不得停在第一条内嵌帧，也不得越过 E4 边界。
- 23 条 E4 的目标均须判定为 CCO TEI `001`。默认站点键优先使用转发内容中的源 MAC。
- 按默认 15 分钟、时钟边界对齐、CCO `001`、开启去重查询时，本文件只产生 **1 个周期行**；该行必须为：原始上报 23、唯一 STA 18、去重展示数 18、重复数 5、成功 23、失败 0、解析异常 0。
- 汇总区必须与周期行一致：周期数 1、原始上报 23、STA-周期去重数 18、重复数 5、成功 23、失败 0、解析异常 0。
- 周期详情必须能回溯到 23 个原始 frame ID/日志行；重复站点仍保留全部证据帧，不能因去重丢失明细。
- 验收采用自动化断言和 API 返回值；页面仅作为展示验收，不能用人工目测替代上述数值断言。

## Global Constraints

- Use `测试文件/分钟采集应用帧格式介绍.md` as the protocol source for `0x00E2/0x00E3/0x00E4`.
- Never classify a frame by searching for the byte string `11 E4`; classification requires the bounded APS header to contain port `0x11` and little-endian message ID `0x00E4`.
- A minute report is received by the CCO only when `FINL_D == "001"`, falling back to `DST == "001"` when `FINL_D` is absent.
- Default period length is 15 minutes and periods align to clock boundaries.
- Default deduplication counts one report per station per period; station identity is source MAC, falling back to `ORI_S`, then `SRC`.
- Preserve raw report count, duplicate count, parse-error count, and the original frame ID even when deduplicating.
- Preserve existing non-minute frame parsing and the current frame-browser page.
- Do not infer the undocumented byte immediately before the sample's source MAC. Expose it as `前导字段` until another verified protocol statement names it.
- The workspace is currently not a Git repository; replace commit steps with explicit verification checkpoints.

---

### Task 1: Expose the exact application payload from the DLL

**Files:**
- Modify: `dll/src/inrfDesc.cs`
- Modify: `dll/src/hplcFrame.cs`
- Modify: `dll/src/intf.cs`
- Test: `hplc_web/tests/test_dotnet_parser.py`

**Interfaces:**
- Consumes: a complete `7E FF 02 ... 7E` sniffer frame.
- Produces: simple JSON fields `APP_PORT`, `APP_ID`, and `APP_RAW`; `APP_RAW` contains exactly the bounded 4-3 message beginning with the port byte.

- [ ] **Step 1: Add a failing integration test using the supplied `0x00E4` frame**

Read the first `0x00E4` record from `测试文件/测试文本.txt`, call `parse_simple`, and assert:

```python
self.assertEqual(result["APP_PORT"], "11")
self.assertEqual(result["APP_ID"], "00E4")
self.assertTrue(result["APP_RAW"].startswith("11E400000132"))
self.assertEqual(len(bytes.fromhex(result["APP_RAW"])), 106)
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m unittest hplc_web.tests.test_dotnet_parser.DotNetHplcParserIntegrationTests.test_exposes_bounded_e4_application_payload
```

Expected: failure because the three application fields are absent.

- [ ] **Step 3: Add bounded application fields**

Add nullable string fields to `intfDesc`:

```csharp
public string APP_PORT;
public string APP_ID;
public string APP_RAW;
```

In the GW standard-frame and GW single-hop application branches, copy the application bytes using the decoded MAC `MSDU长度` and the already-calculated APS start offset. Reject negative or out-of-range boundaries; leave the fields null rather than copying physical-block padding. Format `APP_PORT` as two uppercase hex digits, `APP_ID` as four uppercase hex digits, and `APP_RAW` as compact uppercase hex.

- [ ] **Step 4: Rebuild and run the test**

Run:

```powershell
& 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe' 'dll\DLL_NwHPLCAnalysis.csproj' /t:Build /p:Configuration=Debug /nologo /v:minimal
python -m unittest hplc_web.tests.test_dotnet_parser.DotNetHplcParserIntegrationTests.test_exposes_bounded_e4_application_payload
```

Expected: build succeeds and the integration test passes with an application length of 106 bytes.

- [ ] **Step 5: Verification checkpoint**

Run the existing DLL integration tests and confirm non-APS summaries retain null application fields.

---

### Task 2: Extend `DualMode43Adapter` for minute-collection messages

**Files:**
- Modify: `parser_lib/adapters/adapter_dualmode/__init__.py`
- Create: `parser_lib/adapters/adapter_dualmode/tests/test_minute_collection.py`

**Interfaces:**
- Consumes: exact APS bytes from `APP_RAW`.
- Produces: `ProtocolFrame` with structure `双模4-3`, message names for `0x00E2/0x00E3/0x00E4`, and structured minute fields.

- [ ] **Step 1: Add failing tests for the real `0x00E4` application bytes**

Construct the APS fixture from Task 1 and assert:

```python
frame = DualMode43Adapter().decode(bytes.fromhex(E4_APP_HEX))
assert _field(frame, "报文ID").raw == 0x00E4
assert _field(frame, "分钟采集类型").value == "主动上报"
assert _field(frame, "协议版本号").raw == 1
assert _field(frame, "报文头长度").raw == 8
assert _field(frame, "方向").value == "上行"
assert _field(frame, "启动位").raw == 1
assert _field(frame, "报文序号").raw == 0x000000C4
assert _field(frame, "转发报文长度").raw == 94
assert _field(frame, "任务号").raw == 7
assert _field(frame, "协议类型").raw == 2
assert _field(frame, "响应结果").raw == 0
assert _field(frame, "数据长度").raw == 76
assert len(frame.nested) == 4
assert all(item.structure == "645" for item in frame.nested)
```

Also add constructed `0x00E2` and `0x00E3` cases from the local protocol document, covering header lengths 16 and 20.

- [ ] **Step 2: Run the tests and observe the missing-ID failure**

Run:

```powershell
python -m pytest parser_lib/adapters/adapter_dualmode/tests/test_minute_collection.py -q
```

Expected: the adapter reports an unknown message ID and lacks minute fields.

- [ ] **Step 3: Register the message IDs and dispatch parsers**

Add:

```python
_MESSAGE_NAMES.update({
    0x00E2: "采集任务配置",
    0x00E3: "采集任务数据读取",
    0x00E4: "采集任务数据上报",
})
```

Dispatch `decode` to `_parse_minute_config`, `_parse_minute_read`, or `_parse_minute_report`. Use the documented bit layout and little-endian integer fields. Bounds-check before every variable-length read and append warnings instead of raising on truncated data.

- [ ] **Step 4: Parse active `0x00E4` reports without using nested-frame boundaries**

For active reports, calculate:

```python
header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
direction = (business[1] >> 4) & 0x01
start_flag = (business[1] >> 5) & 0x01
sequence = int.from_bytes(business[2:6], "little")
forward_len = int.from_bytes(business[6:8], "little")
forwarded = business[header_len:header_len + forward_len]
```

Treat the real sample's first forwarded byte as `前导字段`, then parse source MAC, task number, packed protocol/meter/result byte, freeze time, report count, data length, and data bytes. Run the existing 645/698 recursive scanners only inside the bounded data bytes.

- [ ] **Step 5: Add complete-frame extraction rules**

Update `try_extract` for `0x00E4` active reports to consume `4 + header_len + forward_len`, not the end of the first nested 645/698 frame. Keep existing extraction behavior for older IDs.

- [ ] **Step 6: Run adapter regression tests**

Run:

```powershell
python -m pytest parser_lib/adapters/adapter_dualmode/tests -q
python -m pytest parser_lib/adapters/adapter_645/tests parser_lib/adapters/adapter_698/tests -q
```

Expected: all tests pass and the real sample yields four nested 645 frames.

---

### Task 3: Merge Python application parsing into the current parser service

**Files:**
- Create: `hplc_web/application_service.py`
- Modify: `hplc_web/parser_service.py`
- Modify: `hplc_web/app.py`
- Create: `hplc_web/tests/test_application_service.py`
- Modify: `hplc_web/tests/test_parser_service.py`

**Interfaces:**
- Consumes: decoded DLL summary containing `APP_RAW`.
- Produces: `simple.application` dictionary and a promoted `FrmType` for known minute messages.

- [ ] **Step 1: Add failing enrichment tests**

Define the public interface:

```python
class ApplicationAnalysisService:
    def decode(self, app_hex: str) -> dict: ...
    def enrich_summary(self, simple: dict) -> dict: ...
```

Assert that `APP_ID == "00E4"` produces `FrmType == "分钟采集数据上报"` and preserves the original DLL type in `BaseFrmType == "APS"`.

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m unittest hplc_web.tests.test_application_service hplc_web.tests.test_parser_service
```

Expected: import or assertion failure because the service does not exist.

- [ ] **Step 3: Implement serialization and enrichment**

Convert `ProtocolFrame`, `DataField`, warnings, and nested frames into JSON-safe dictionaries. Promote types using this exact mapping:

```python
MINUTE_TYPES = {
    "00E2": "分钟采集任务配置",
    "00E3": "分钟采集数据读取",
    "00E4": "分钟采集数据上报",
}
```

Do not alter summaries without a known `APP_ID`. On adapter failure, retain `FrmType="APS"` and add `application_error` so indexing continues.

- [ ] **Step 4: Wire enrichment into both parse paths**

Call `enrich_summary` after DLL JSON decoding in both `parse_summary` and `parse`. Reuse one `DualMode43Adapter` instance; keep DLL calls under the existing lock and perform pure-Python enrichment after the DLL call.

- [ ] **Step 5: Run service and API tests**

Run:

```powershell
python -m unittest discover -s hplc_web/tests -p 'test_*.py'
```

Expected: all tests pass and `/api/parse` returns `分钟采集数据上报` for the supplied frame.

---

### Task 4: Persist minute reports during indexing

**Files:**
- Modify: `hplc_web/log_service.py`
- Modify: `hplc_web/tests/test_log_service.py`

**Interfaces:**
- Consumes: enriched summaries from Task 3.
- Produces: SQLite table `minute_reports` keyed by `frame_id` and query method `list_minute_periods(period_minutes, cco_tei, deduplicate)`.

- [ ] **Step 1: Add failing database tests**

Require the table columns:

```text
frame_id, log_time, time_seconds, cco_tei, station_key, source_mac,
source_tei, task_no, protocol_type, meter_type, response_result,
freeze_time, report_count, data_length, application_error
```

Index duplicate reports from the same station and assert raw count 2, unique station count 1, duplicate count 1.

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
python -m unittest hplc_web.tests.test_log_service
```

Expected: failure because `minute_reports` and period queries do not exist.

- [ ] **Step 3: Create and populate `minute_reports` transactionally**

Create/drop it together with `frames`. Insert only recognized `APP_ID == "00E4"` reports. Store reports not addressed to `001` as well, because the API accepts a configurable CCO TEI.

- [ ] **Step 4: Implement time normalization and aligned buckets**

Parse `HH:mm:ss.fff` into milliseconds. When time moves backward by more than 12 hours, increment a day offset. Compute aligned period start with:

```python
period_ms = period_minutes * 60_000
period_start_ms = absolute_ms - (absolute_ms % period_ms)
```

Validate `period_minutes` in `1..1440` and `cco_tei` as three uppercase hexadecimal characters.

- [ ] **Step 5: Implement aggregation**

Return per-period dictionaries containing:

```text
period_start, period_end, raw_report_count, unique_station_count,
duplicate_count, success_count, failure_count, parse_error_count,
station_keys, frame_ids, description
```

When `deduplicate=true`, the headline `report_count` equals `unique_station_count`; otherwise it equals `raw_report_count`.

- [ ] **Step 6: Run log-service tests**

Run:

```powershell
python -m unittest hplc_web.tests.test_log_service
```

Expected: all period, midnight rollover, deduplication, destination-filter, and malformed-report tests pass.

---

### Task 5: Add the minute-analysis API and page

**Files:**
- Modify: `hplc_web/app.py`
- Modify: `hplc_web/static/index.html`
- Modify: `hplc_web/static/app.js`
- Modify: `hplc_web/static/styles.css`
- Modify: `hplc_web/tests/test_app.py`
- Modify: `hplc_web/tests/test_ui_layout.py`

**Interfaces:**
- Consumes: `LogFileService.list_minute_periods`.
- Produces: `GET /api/logs/minute-analysis` and a `分钟采集分析` tab.

- [ ] **Step 1: Add failing API tests**

Require:

```http
GET /api/logs/minute-analysis?period_minutes=15&cco_tei=001&deduplicate=true
```

The response must contain `periods`, `summary`, and `filters`. Reject invalid periods and invalid TEIs with HTTP 422.

- [ ] **Step 2: Add failing UI structure tests**

Require tabs `帧浏览` and `分钟采集分析`, period selector, CCO TEI input defaulting to `001`, deduplication checkbox enabled by default, summary cards, period table, and expandable period details.

- [ ] **Step 3: Implement the endpoint**

Pass validated query parameters to `list_minute_periods`. The summary contains total periods, raw reports, unique station-period occurrences, duplicates, successful reports, failed reports, and parse errors. Do not claim a STA is missing because no expected-STA roster exists in this phase.

- [ ] **Step 4: Implement the page**

Render one row per period with:

```text
周期 | 去重上报STA数 | 原始帧数 | 重复数 | 成功 | 失败 | 解析异常 | 简介
```

Clicking a row shows station keys and links to the existing frame-detail endpoint using stored frame IDs.

- [ ] **Step 5: Run API and UI tests**

Run:

```powershell
python -m unittest hplc_web.tests.test_app hplc_web.tests.test_ui_layout
```

Expected: all tests pass.

---

### Task 6: End-to-end verification on the supplied acceptance file

**Files:**
- Modify: `doc/任务交接需求与进度表.md`
- Test data: `测试文件/测试文本.txt`

**Interfaces:**
- Consumes: the rebuilt DLL and completed Web application.
- Produces: verified minute-analysis output and updated handoff documentation.

- [ ] **Step 1: Run all automated tests**

Run:

```powershell
python -m pytest parser_lib -q
python -m unittest discover -s hplc_web/tests -p 'test_*.py'
```

Expected: both suites pass.

- [ ] **Step 2: Rebuild and restart**

Rebuild `GwHPLCAnalysis.dll`, close any existing launcher process so the old assembly is unloaded, and start `启动解析工具-测试模式.bat`.

- [ ] **Step 3: Verify the supplied frame**

POST the exact user frame to `/api/parse` and verify:

```text
FrmType = 分钟采集数据上报
APP_ID = 00E4
ORI_S = 009
FINL_D = 001
分钟采集类型 = 主动上报
任务号 = 7
协议类型 = 2
响应结果 = 0
数据长度 = 76
内嵌645帧 = 4
```

- [ ] **Step 4: Verify indexing and period statistics**

Index exactly `测试文件/测试文本.txt` and assert the fixed baseline: 50 indexed records, 23 E4 reports, 27 negative records, one 15-minute period, 18 unique stations, 5 duplicates, 23 successes, 0 failures, 0 parse errors, and 92 nested 645 frames. Confirm every aggregate links back to its evidence frame IDs.

- [ ] **Step 5: Update handoff documentation**

Document the `E2/E3/E4` recognition rules, the unresolved `前导字段`, aggregation semantics, API parameters, and the fact that missing-STA analysis requires a future expected-station roster.
