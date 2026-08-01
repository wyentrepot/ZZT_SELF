# GW Sniffer Frame Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GwHPLCAnalysis.dll` recognize `7E FF 02 ... 7E` GW sniffer envelopes and return structured simple/full parsing results.

**Architecture:** Dispatch decoded frames by outer-envelope signature. `FF 02` frames use the documented 22-byte GW sniffer header and actual bounded payload; other frames retain the existing generic parser. Public parsing methods select one successfully parsed context instead of dereferencing both NW and GW contexts.

**Tech Stack:** C#, .NET Framework 4.8, MSBuild, Python.NET, Python unittest.

## Global Constraints

- Preserve existing non-`FF 02` behavior.
- Never copy beyond the received frame boundary.
- A declared/actual GW payload-length mismatch must be observable but must not crash the debugger.
- The supplied GW frame is the required regression sample.

---

### Task 1: Add the failing GW integration regression

**Files:**
- Create: `hplc_web/tests/fixtures.py`
- Modify: `hplc_web/tests/test_dotnet_parser.py`

**Interfaces:**
- Consumes: the supplied `7E FF 02 ... 7E` frame.
- Produces: a test requiring `parse_simple()` to return JSON whose `FrmType` is not `ERROR`.

- [ ] Add the exact supplied frame as `GW_FRAME_HEX`.
- [ ] Call `DotNetHplcParser(Path("dll/bin/Debug/GwHPLCAnalysis.dll"))`.
- [ ] Assert the current implementation fails with `FrmType == "ERROR"` before changing C#.

### Task 2: Parse the GW sniffer envelope safely

**Files:**
- Modify: `dll/src/snifferFrame.cs`

**Interfaces:**
- Consumes: PPP-decoded bytes beginning `FF 02`.
- Produces: `FrmHdrInfo_gw`, bounded payload bytes, and status `0`.

- [ ] Add a GW header parser mapping bytes 0 through 21.
- [ ] Read the declared payload length little-endian at offsets 18–19.
- [ ] Compute available payload as `decoded.Length - 22 - 4`.
- [ ] Copy `Math.Min(declared, available)` bytes and record a mismatch marker in the header data.
- [ ] Leave the existing generic branch unchanged for non-`FF 02` frames.

### Task 3: Select the successful protocol context

**Files:**
- Modify: `dll/src/intf.cs`

**Interfaces:**
- Consumes: NW and GW preprocessing status/header/payload values.
- Produces: one non-null active header and payload used for FCH/MAC parsing.

- [ ] Derive `isGw` from the `FF 02` GW preprocessing result.
- [ ] Use `Payload1` for GW and `Payload` for NW.
- [ ] Replace unsafe `HdrInfo.ProType` dereferences in public entry methods.
- [ ] Return serialized structured errors for invalid frames.

### Task 4: Rebuild and verify end to end

**Files:**
- Generated: `dll/bin/Debug/GwHPLCAnalysis.dll`

**Interfaces:**
- Consumes: modified C# source.
- Produces: validated `GW_SMAnalysis V1.0.23` assembly and live API response.

- [ ] Stop the local Python service so it releases the DLL.
- [ ] Rebuild with MSBuild.
- [ ] Run the GW regression and complete Python test suite.
- [ ] Restart the local service.
- [ ] POST the supplied frame and verify a non-error parsed response.

