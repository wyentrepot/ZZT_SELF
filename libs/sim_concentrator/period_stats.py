# -*- coding: utf-8 -*-
"""并发抄表周期统计（REQS-0030）。

把"抄读尝试"按时间桶聚合，输出四项指标：
- 最大并发数：桶内同时在飞（未结清）数的峰值
- 成功数 / 成功率：status == success 占下发总数
- 平均耗时：已结清尝试的下发→应答（或超时）平均时长 ms
- 重复下发次数：同一表在上一次尝试未结清前再次下发的次数

周期长度是 API 参数（秒），默认 900 = 15 分钟；AI 控制面与前端页面共用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

DEFAULT_PERIOD_SECONDS = 900  # 15 分钟

_PERIOD_RE = __import__("re").compile(r"^(\d+)([smh]?)$")


def parse_period(value: Any) -> int:
    """'15m'/'30s'/'1h'/'900' → 秒；非法值回退默认 15 分钟。"""
    if value is None or value == "":
        return DEFAULT_PERIOD_SECONDS
    s = str(value).strip().lower()
    m = _PERIOD_RE.match(s)
    if not m:
        return DEFAULT_PERIOD_SECONDS
    n, unit = int(m.group(1)), m.group(2)
    return n * {"": 1, "s": 1, "m": 60, "h": 3600}[unit]


def _bucket_start(epoch: float, period: int) -> int:
    return int(epoch // period) * period


def aggregate(attempts: Iterable[Dict[str, Any]], *,
              period: Any = DEFAULT_PERIOD_SECONDS) -> Dict[str, Any]:
    """聚合抄读尝试列表。

    attempts 每项：{meter, start_epoch, end_epoch, status}
    （end_epoch 为空表示尚未结清；status: success/deny/timeout/error）
    """
    period = int(period) if isinstance(period, (int, float)) else parse_period(period)
    atts: List[Dict[str, Any]] = []
    for a in attempts:
        try:
            start = float(a["start_epoch"])
            end = a.get("end_epoch")
            atts.append({
                "meter": str(a.get("meter", "")),
                "start": start,
                "end": float(end) if end is not None else None,
                "status": str(a.get("status", "")),
                "duplicate": bool(a.get("duplicate")),
            })
        except (KeyError, TypeError, ValueError):
            continue

    buckets: Dict[int, Dict[str, Any]] = {}

    def bucket_of(start: float) -> Dict[str, Any]:
        key = _bucket_start(start, period)
        if key not in buckets:
            buckets[key] = {
                "period_start": datetime.fromtimestamp(
                    key, tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
                "period_seconds": period,
                "dispatch_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "duplicate_count": 0,
                "avg_duration_ms": None,
                "max_concurrent": 0,
            }
        return buckets[key]

    # 每表未结清链：判定重复下发（上一次未结清又发同一表）。
    # attempts 可带显式 duplicate 标记（listener 侧 collect_attempts 在重发结清
    # 时刻已把旧尝试关闭，open_until 条件对其永不成立，须靠标记计数）；
    # simcon 下发侧无标记，沿用 open_until 时间线判定，行为不变。
    open_until: Dict[str, float] = {}
    for a in sorted(atts, key=lambda x: x["start"]):
        b = bucket_of(a["start"])
        b["dispatch_count"] += 1
        if a["status"] == "success":
            b["success_count"] += 1
        else:
            b["failed_count"] += 1
        if a.get("duplicate") or (
                a["end"] is not None and open_until.get(a["meter"], 0) > a["start"]):
            b["duplicate_count"] += 1
        if a["end"] is not None:
            open_until[a["meter"]] = a["end"]

    # 最大并发 + 平均耗时（按桶内尝试计算）
    for key, b in buckets.items():
        lo, hi = key, key + period
        # 在飞峰值：边界扫掠（dispatch +1，settle -1）
        marks: List[tuple] = []
        durs = []
        for a in atts:
            if not (lo <= a["start"] < hi):
                continue
            marks.append((a["start"], 1))
            if a["end"] is not None:
                marks.append((a["end"], -1))
                durs.append((a["end"] - a["start"]) * 1000)
        cur = peak = 0
        for _, d in sorted(marks, key=lambda x: (x[0], x[1])):  # 先结算再开新，避免峰值虚高
            cur += d
            peak = max(peak, cur)
        b["max_concurrent"] = peak
        b["avg_duration_ms"] = int(sum(durs) / len(durs)) if durs else None

    return {
        "period_seconds": period,
        "buckets": [buckets[k] for k in sorted(buckets)],
        "attempts_total": len(atts),
    }
