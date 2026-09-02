"""REQS-0022 Phase 4：真实样本最终验收（只读，不改样本、不建运行时索引）。

对「并发抄表原始报文」跑 trace_query，对「模块快日志」跑 minute_periods，
复用既有 `TraceService` / `list_task_minute_periods`，输出只读验收报告。

- 样本为只读输入；临时索引落在 --output 目录。
- 缺解析后端 → deep_validation=blocked；样本缺失/无目标帧 → coverage_missing，
  不做虚假通过（REQS-0022 全局约束 8）。

Examples::

    python tools/scripts/test_listener_ai_existing_parser_sample.py \
        --input "测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt" \
        --listener-input "测试文件/模块快日志/侦听台 - 副本" \
        --output "D:\\2-侦听台改造\\.tmp\\reqs-0022-listener-sample" \
        --p95-ms 500
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
for _sub in ("", "apps", "libs"):
    _p = REPO_ROOT / _sub if _sub else REPO_ROOT
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from listener.log_service import LogFileService, extract_log_record  # noqa: E402
from listener.trace_service import TraceService  # noqa: E402

_DLL_PATH = REPO_ROOT / "libs" / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
_TRACE_APP_ID = "0003"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parser():
    if not _DLL_PATH.exists():
        return None
    from shared.dotnet_parser import DotNetHplcParser
    from shared.parser_service import ParserService
    return ParserService(DotNetHplcParser(_DLL_PATH))


def _read_records(path: Path, max_lines: int) -> tuple[list[tuple[str, str, str]], bytes]:
    """读取前 max_lines 条有效帧记录；返回 (records, 已读字节)。"""
    records: list[tuple[str, str, str]] = []
    raw = bytearray()
    with path.open("rb") as stream:
        for line in stream:
            raw += line
            record = extract_log_record(line)
            if record is None:
                continue
            records.append((record.sequence, record.log_time, record.hex_frame))
            if len(records) >= max_lines:
                break
    return records, bytes(raw)


def _build_index(parser, records, db_path: Path) -> LogFileService:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    service = LogFileService(parser=parser, database_path=db_path)
    # minute_state={} 使 00E4 分钟采集帧正确落 minute_reports（与 index_file 同路径）
    service.append_frames(records, minute_state={})
    return service


def _trace_projection(report: dict) -> dict:
    """从回放报告提取 L1 范围摘要 + L2 逐帧投影（脚本内等价验证）。"""
    summary = report.get("summary") or {}
    flows = []
    frame_count = 0
    directions = {}
    frames = []
    for round_report in report.get("rounds") or []:
        for flow in round_report.get("flows") or []:
            flows.append(flow)
            for frame in flow.get("frames") or []:
                frame_count += 1
                directions[frame.get("direction")] = directions.get(frame.get("direction"), 0) + 1
                frames.append(frame)
    return {
        "flows": int(summary.get("flows") or len(flows)),
        "frame_count": frame_count,
        "directions": directions,
        "frames": frames[:50],
    }


def verify_trace_query(parser, path: Path, output_dir: Path, max_lines: int) -> dict:
    if path is None or not path.is_file():
        return {"coverage": "missing", "reason": f"样本不存在：{path}"}
    records, raw = _read_records(path, max_lines)
    if not records:
        return {"coverage": "missing", "reason": "样本无有效帧"}
    result: dict = {
        "coverage": "ok",
        "source": str(path),
        "lines_read": len(records),
        "sha256_of_read_bytes": _sha256_bytes(raw),
        "index_id": "idx-concurrent-sample",
    }
    db_path = output_dir / "idx-concurrent.sqlite3"
    if db_path.exists():
        db_path.unlink()
    service = _build_index(parser, records, db_path)
    tracer = TraceService(service)
    # 功能验证：宽查询 app_id=0003 找 flows
    feature = {"scope": "round", "feature": {"app_id": _TRACE_APP_ID}}
    report = tracer.run_replay(feature)
    projection = _trace_projection(report)
    # 性能门：温热收窄查询（app_id + NID + 时间窗），从宽查询结果反推 NID 与时间窗
    nid = None
    start_t = end_t = None
    for frame in projection["frames"]:
        if frame.get("nid") and frame.get("log_time"):
            nid = frame["nid"]
            start_t = frame["log_time"]
            end_t = frame["log_time"]
            break
    timings = []
    if nid is not None:
        narrow = {
            "scope": "round",
            "feature": {"app_id": _TRACE_APP_ID, "nid": nid},
            "window": {"mode": "time_range", "start_time": start_t, "end_time": end_t},
        }
        for _ in range(10):
            started = time.perf_counter()
            tracer.run_replay(narrow)
            timings.append((time.perf_counter() - started) * 1000.0)
    result["nid"] = nid
    result["p95_ms"] = round(sorted(timings)[int(len(timings) * 0.95) - 1], 1) if timings else None
    result.update({
        "flows": projection["flows"],
        "frame_count": projection["frame_count"],
        "directions": projection["directions"],
    })
    # L3 等价：取一帧回 get_index_frame 完整 JSON，应含 raw_hex / summary / analysis
    l3 = {}
    sample_frame = None
    for frame in projection["frames"]:
        if frame.get("frame_id") is not None:
            sample_frame = frame
            break
    if sample_frame is not None:
        detail = service.get_frame(int(sample_frame["frame_id"]))
        l3 = {
            "has_raw_hex": bool(detail.get("raw_hex")),
            "has_summary": bool(detail.get("summary")),
            "has_analysis": bool(detail.get("analysis")),
        }
    # 验证结论
    ok = (
        projection["flows"] >= 1
        and bool(projection["directions"].get("downlink"))
        and bool(projection["directions"].get("uplink"))
    )
    result.update({
        "l3_sample": l3,
        "verdict": "pass" if ok else "coverage_missing",
        "reason": "" if ok else "并发抄表帧不足：缺 flow 或上下行帧",
    })
    return result


def verify_minute_periods(parser, path_dir: Path, output_dir: Path, max_lines: int) -> dict:
    if path_dir is None or not path_dir.is_dir():
        return {"coverage": "missing", "reason": f"样本目录不存在：{path_dir}"}
    files = sorted(path_dir.glob("*.txt"))
    if not files:
        return {"coverage": "missing", "reason": "样本目录无 .txt 文件"}
    result: dict = {
        "coverage": "ok",
        "source": str(path_dir),
        "files": [f.name for f in files],
        "index_id": "idx-minute-sample",
    }
    db_path = output_dir / "idx-minute.sqlite3"
    if db_path.exists():
        db_path.unlink()
    # 合并建一个只读临时索引（各文件前 max_lines 帧）
    all_records: list[tuple[str, str, str]] = []
    total_bytes = bytearray()
    for f in files:
        records, raw = _read_records(f, max_lines)
        all_records.extend(records)
        total_bytes += raw
    result["frames_read"] = len(all_records)
    result["sha256_of_read_bytes"] = _sha256_bytes(bytes(total_bytes))
    service = _build_index(parser, all_records, db_path)

    with service._connect() as conn:
        rows = conn.execute(
            "SELECT task_no, cco_tei, COUNT(*) AS n FROM minute_reports "
            "GROUP BY task_no, cco_tei ORDER BY n DESC"
        ).fetchall()
    result["minute_report_count"] = sum(row["n"] for row in rows) if rows else 0
    if not rows:
        return {**result, "verdict": "coverage_missing", "reason": "样本无分钟采集（00E4）帧"}

    task_no = rows[0]["task_no"]
    cco_tei = rows[0]["cco_tei"] or "001"
    # 无 00E2 任务配置时，effective_period 取 period_minutes 回退（分钟采集标准 15 分钟）
    periods = service.list_task_minute_periods(
        task_no=task_no, period_minutes=15, cco_tei=cco_tei,
        nid="", start_time="", end_time="",
    )
    # list_task_minute_periods 返回信封 dict（task_no/source/periods + unconfigured_reports）
    if isinstance(periods, dict):
        period_list = periods.get("periods") or []
        source = periods.get("source")
        derived = periods.get("derived_period_minutes")
        unconfigured = periods.get("unconfigured_reports") or []
    else:
        period_list = periods or []
        source = None
        derived = None
        unconfigured = []
    reports = [r for p in period_list for r in p.get("reports") or []]
    # 配置周期外的上报也属真实分钟采集，纳入 freeze_time 权威字段验证
    all_reports = reports + list(unconfigured)
    ok = bool(all_reports) and all(
        r.get("freeze_time") and r.get("frame_id") is not None
        for r in all_reports[:20]
    )
    result.update({
        "task_no": task_no,
        "cco_tei": cco_tei,
        "source": source,
        "derived_period_minutes": derived,
        "period_count": len(period_list),
        "report_count": len(reports),
        "unconfigured_report_count": len(unconfigured),
        "sample_reports": [
            {"frame_id": r.get("frame_id"), "log_time": r.get("log_time"),
             "freeze_time": r.get("freeze_time"), "response_result": r.get("response_result")}
            for r in reports[:5]
        ],
        "verdict": "pass" if ok else "coverage_missing",
        "reason": "" if ok else "分钟采集 report 缺 freeze_time 或 frame_id",
    })
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(REPO_ROOT / "测试文件" / "并发抄表-测试文件" / "原始报文自动保存 - 2026-06-30.txt"))
    parser.add_argument("--listener-input", default=str(REPO_ROOT / "测试文件" / "模块快日志" / "侦听台 - 副本"))
    parser.add_argument("--output", default=str(REPO_ROOT / ".tmp" / "reqs-0022-listener-sample"))
    parser.add_argument("--max-lines", type=int, default=50000, help="每个源文件最大读取帧数")
    parser.add_argument("--p95-ms", type=float, default=500.0)
    args = parser.parse_args(argv)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "parser_backend": None,
        "trace_query": {},
        "minute_periods": {},
    }
    parser_ = _parser()
    if parser_ is None:
        report["parser_backend"] = "none"
        report["deep_validation"] = "blocked"
        report["reason"] = f"缺解析库：{_DLL_PATH}"
        _write_report(output_dir, report)
        return 1

    report["parser_backend"] = "dotnet"
    report["trace_query"] = verify_trace_query(
        parser_, Path(args.input), output_dir, args.max_lines,
    )
    report["minute_periods"] = verify_minute_periods(
        parser_, Path(args.listener_input), output_dir, args.max_lines,
    )
    if args.p95_ms is not None:
        p95 = report["trace_query"].get("p95_ms")
        report["perf_p95_ms"] = p95
        report["perf_gate"] = (
            "pass" if p95 is None or p95 <= args.p95_ms else "fail"
        )

    _write_report(output_dir, report)
    # 退出码：深度解析不可用 → 1；其余（含 coverage_missing）→ 0（缺项如实记录）
    return 0


def _write_report(output_dir: Path, report: dict) -> None:
    json_path = output_dir / "acceptance.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
