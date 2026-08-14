"""输出格式化：摘要 JSON / 表格。

将 ScanResult 转换为最终输出格式（含 summary 分类统计、事件列表、漂移清单）。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .correlate import Correlation
from .engine import DriftInfo, Event, ScanResult


def build_summary(result: ScanResult) -> dict:
    """构建摘要统计（按 category 分类）。"""
    summary: Dict[str, Any] = {}
    by_category = defaultdict(list)
    for ev in result.events:
        by_category[ev.category].append(ev)

    for cat, events in by_category.items():
        samples = []
        for ev in events[:3]:  # 最多 3 个样本
            samples.append({
                "time": ev.time,
                "type": ev.type,
                "message": ev.message,
            })
        entry = {
            "count": len(events),
            "samples": samples,
        }
        # 对于 join 类，附加 last.node_count
        last = events[-1] if events else None
        if last and "node_count" in last.captures:
            entry["last"] = {"time": last.time, "node_count": last.captures["node_count"]}
        summary[cat] = entry

    return summary


def build_drift_list(result: ScanResult) -> List[dict]:
    """构建漂移清单。"""
    return [
        {
            "rule_id": d.rule_id,
            "file": d.file,
            "expected_line": d.expected_line,
            "actual_line": d.actual_line,
            "tolerance": d.tolerance,
        }
        for d in result.drifts
    ]


def format_json(
    result: ScanResult,
    correlations: Optional[List[Correlation]] = None,
    detected_provinces: Optional[List[dict]] = None,
) -> str:
    """输出摘要 JSON。"""
    output: Dict[str, Any] = {
        "source": result.source,
        "files": result.files,
        "total_lines": result.total_lines,
        "unmatched_lines": result.unmatched,
        "event_count": len(result.events),
        "summary": build_summary(result),
        "events": [
            {
                "type": ev.type,
                "label": ev.label,
                "message": ev.message,
                "level": ev.level,
                "time": ev.time,
                "rule_id": ev.rule_id,
                "category": ev.category,
                "source_line": ev.source_line,
                "line_drift": ev.line_drift,
            }
            for ev in result.events
        ],
        "rule_drifts": build_drift_list(result),
    }

    if detected_provinces:
        output["detected_provinces"] = detected_provinces

    if correlations:
        output["correlations"] = [
            {
                "anchor": c.anchor,
                "matched": c.matched,
                "module_log": c.module_log,
                "listener": c.listener,
                "time_gap_s": c.time_gap_s,
            }
            for c in correlations
        ]

    return json.dumps(output, ensure_ascii=False, indent=2)


def format_table(result: ScanResult) -> str:
    """输出精简表格。"""
    lines = []
    lines.append(f"来源: {result.source}")
    lines.append(f"文件: {', '.join(result.files) if result.files else '(未指定)'}")
    lines.append(f"总行数: {result.total_lines} | 未匹配: {result.unmatched} | 事件: {len(result.events)}")
    lines.append("")

    # 按 category 分组
    by_cat = defaultdict(list)
    for ev in result.events:
        by_cat[ev.category].append(ev)

    for cat, events in sorted(by_cat.items()):
        lines.append(f"[{cat}] ({len(events)} 条)")
        for ev in events[:5]:  # 最多 5 条
            lines.append(f"  {ev.time} | {ev.type} | {ev.message}")
        if len(events) > 5:
            lines.append(f"  ... 还有 {len(events)-5} 条")
        lines.append("")

    # 漂移
    if result.drifts:
        lines.append("[行号漂移]")
        for d in result.drifts:
            lines.append(f"  {d.rule_id}: 期望行 {d.expected_line} → 实际行 {d.actual_line} (容差 {d.tolerance})")
        lines.append("")

    return "\n".join(lines)