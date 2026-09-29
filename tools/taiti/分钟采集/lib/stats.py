# -*- coding: utf-8 -*-
"""分钟采集分析 — 统计辅助（P50/P95 等）。"""
from __future__ import annotations

from collections import Counter


def pct(vals, q: float):
    """线性分位数（列表需可排序；q∈[0,1]）。"""
    s = sorted(vals)
    if not s:
        return None
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def dist_summary(vals, digits: int = 3) -> str:
    """min / P50 / 均值 / P95 / max 一行摘要。"""
    if not vals:
        return "n=0"
    import statistics as st
    return (f"n={len(vals)} min={min(vals):.{digits}f} p50={st.median(vals):.{digits}f} "
            f"mean={st.mean(vals):.{digits}f} p95={pct(vals, 0.95):.{digits}f} "
            f"max={max(vals):.{digits}f}")


def hist1(vals, lo=None, hi=None) -> str:
    """1 秒分箱直方图文本（含端点）。"""
    if not vals:
        return "(空)"
    h = Counter(int(x) for x in vals)
    parts = []
    for k in sorted(h):
        if lo is not None and k < lo:
            continue
        if hi is not None and k > hi:
            continue
        parts.append(f"[{k},{k+1})s:{h[k]}")
    return " ".join(parts)
