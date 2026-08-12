"""跨来源关联：模块日志 ↔ 侦听台 ↔ （预留）集中器帧。

以业务锚点（NID/MAC/TEI/冻结时刻）对齐两边事件流，
在可配置时间窗内判定"互相印证"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .engine import Event, ScanResult


@dataclass
class Correlation:
    """一条跨来源关联记录。"""

    anchor: str  # 业务锚点，如 "nid:61475d"
    matched: bool
    module_log: Optional[dict] = None
    listener: Optional[dict] = None
    concentrator: Optional[dict] = None
    time_gap_s: Optional[float] = None


# 业务锚点提取规则
_ANCHOR_PATTERNS = [
    # NID：module_log 中 NID=0x61475d / NID=61475d
    (re.compile(r"(?i)nid\s*[:=]\s*(0x)?([0-9a-f]{4,6})"), "nid:{1}"),
    # 冻结时刻：freeze_time/freeze/冻结时刻 后接日期时间
    (re.compile(r"(?i)(?:freeze|冻结)[^0-9]{0,24}([0-9]{4}[-:]?[0-9]{2}[-:]?[0-9]{2}[ T][0-9]{2}[:]?[0-9]{2}[:]?[0-9]{2})"), "freeze:{0}"),
    # 独立的日期时间（兜底，识别冻结时刻格式）
    (re.compile(r"(?<!\d)([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2})(?!\d)"), "freeze:{0}"),
    # MAC：12 位十六进制
    (re.compile(r"(?i)\b([0-9a-f]{12})\b"), "mac:{0}"),
]


def extract_anchors(text: str) -> List[str]:
    """从文本中提取业务锚点。"""
    anchors = []
    for pattern, fmt in _ANCHOR_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            if len(groups) >= 2 and fmt == "nid:{1}":
                anchors.append(f"nid:{groups[1].lower()}")
            elif fmt == "freeze:{0}":
                anchors.append(f"freeze:{groups[0]}")
            elif fmt == "mac:{0}":
                anchors.append(f"mac:{groups[0].lower()}")
    return anchors


def _parse_time(ts: str) -> Optional[float]:
    """把日志时间戳解析为秒（用于时间窗判断）。"""
    if not ts:
        return None
    # 模块日志：20260811-19:15:08:510
    m = re.match(r"(\d{8})-(\d{2}):(\d{2}):(\d{2}):(\d{3})", ts)
    if m:
        _, h, mi, s, ms = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000
    # 侦听台：19:15:09.012
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})", ts)
    if m:
        h, mi, s, ms = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000
    return None


def correlate(
    module_result: Optional[ScanResult],
    listener_result: Optional[ScanResult],
    time_window_s: float = 5.0,
) -> List[Correlation]:
    """关联两个来源的事件流。

    策略：以业务锚点对齐，同一锚点下两边事件在时间窗内出现即判定互相印证。
    """
    correlations: List[Correlation] = []
    if not module_result or not listener_result:
        return correlations

    # 索引：锚点 → 事件列表
    module_by_anchor: Dict[str, List[Event]] = {}
    listener_by_anchor: Dict[str, List[Event]] = {}

    for ev in module_result.events:
        for anchor in extract_anchors(ev.message) + extract_anchors(ev.source_line):
            module_by_anchor.setdefault(anchor, []).append(ev)

    for ev in listener_result.events:
        msg_anchors = extract_anchors(ev.message)
        # 侦听台事件可能带 SNID 字段
        if "snid" in ev.captures:
            msg_anchors.append(f"nid:{str(ev.captures['snid']).lower().lstrip('0')}")
        for anchor in msg_anchors + extract_anchors(ev.source_line):
            listener_by_anchor.setdefault(anchor, []).append(ev)

    # 匹配
    for anchor, module_events in module_by_anchor.items():
        listener_events = listener_by_anchor.get(anchor, [])
        if not listener_events:
            continue

        # 找时间最近的一对
        best_pair: Optional[Tuple[Event, Event, float]] = None
        for mev in module_events:
            mt = _parse_time(mev.time)
            for lev in listener_events:
                lt = _parse_time(lev.time)
                if mt is None or lt is None:
                    gap = 0.0  # 无法比较时间，视为匹配
                else:
                    gap = abs(mt - lt)
                if best_pair is None or gap < best_pair[2]:
                    best_pair = (mev, lev, gap)

        if best_pair:
            mev, lev, gap = best_pair
            if gap <= time_window_s:
                correlations.append(Correlation(
                    anchor=anchor,
                    matched=True,
                    module_log={
                        "type": mev.type,
                        "time": mev.time,
                        "message": mev.message,
                    },
                    listener={
                        "type": lev.type,
                        "time": lev.time,
                        "message": lev.message,
                        "snid": lev.captures.get("snid", ""),
                    },
                    time_gap_s=round(gap, 3),
                ))

    return correlations