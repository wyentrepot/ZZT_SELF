# -*- coding: utf-8 -*-
"""分钟采集分析 — CCO 调试串口日志解析。

行格式：`[20260920-09:16:15:603]49440413 | info | ...`（括号时间为串口宿主打印时刻）。
解析内容：
- 11E4（STA→CCO 主动上报）：(时刻, STA, 冻结)；
- 11E3（双向）：请求（CCO→STA，上下文 `aps tx`/`nwk_tx`）与响应（STA→CCO，
  上下文 `pAps_rxlen`/`recv aps data`/`mclt data rejected`），按回显序号配对；
- 06F230（CCO→集中器上送，`06201c01` 标记，仅打印子集）；
- 告警行：drop stale / mclt data rejected / mclt data result fail / drop duplicate。

踩坑：CCO 日志行被截断（约前 30B=60 hex），存在性可作正证据、缺失不可作反证据；
每帧常被打印 2 次，统计前必须去重（本模块按 (秒, STA, 冻结, 序号) 连续去重）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterator, Optional

from .frame import fz5b, fz6b, hexc, rev_sta, sta_rev_hex

LINE_RE = re.compile(r"^\[(\d{8})-(\d\d:\d\d:\d\d):(\d\d\d)\](.*)$")

TX_MARK = ("nwk_tx", "aps tx", "tx dst", "aps_txlen", "txlen")
RX_MARK = ("paps_rxlen", "paps_rx", "recv aps data", "aps parse",
           "mclt data rejected", "mclt data result fail", "_mclt_task_data_read")

WARN_KINDS = {
    "stale": "drop stale mclt report",
    "rejected": "mclt data rejected",
    "resultfail": "mclt data result fail",
    "duplicate": "drop duplicate mclt report",
}


def cco_ts_ms(t_str: str, ms: str) -> float:
    """'HH:MM:SS' + 'mmm' → 当日秒（float）。"""
    h, m, s = t_str.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _rev_sta_checked(h: str) -> Optional[str]:
    return rev_sta(h, 4)


def parse_lines(path: str):
    """产出 (t_float, ts_str, content_lower)。"""
    with open(path, errors="ignore") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            t = cco_ts_ms(m.group(2), m.group(3))
            yield t, m.group(2), m.group(4).strip().lower()


def classify_11e3(recs, i: int, win: int = 6) -> str:
    """按上下文把 11e3 行分为 TX（CCO 下发）或 RX（STA 响应）。"""
    lo = max(0, i - win)
    hi = min(len(recs), i + win)
    ctx = " ".join(recs[j][2] for j in range(lo, hi))
    rx = sum(1 for k in RX_MARK if k in ctx)
    tx = sum(1 for k in TX_MARK if k in ctx)
    if rx > 0 and tx == 0:
        return "RX"
    if tx > 0 and rx == 0:
        return "TX"
    if rx > 0 and tx > 0 and i > 0:
        prev = recs[i - 1][2]
        return "RX" if ("rxlen" in prev or "msdu_type" in prev or "recv aps" in prev) else "TX"
    return "TX"


def parse_cco(path: str):
    """返回 dict：e4/e3tx/e3rx/f230/warn（均为去重后列表）。"""
    recs = list(parse_lines(path))
    e4, e3tx, e3rx, f230, warn = [], [], [], [], []

    for i, (t, ts, h) in enumerate(recs):
        if h.startswith("11e4"):
            sta = _rev_sta_checked(h)
            if sta:
                p = h.find(sta_rev_hex(sta), 4)
                fr = fz6b(h[p + 16:p + 28]) if p >= 0 else None
                e4.append((t, ts, sta, fr, h))
        elif h.startswith("11e3"):
            sta = _rev_sta_checked(h)
            if not sta:
                continue
            p = h.find(sta_rev_hex(sta), 4)
            fr = fz6b(h[p + 14:p + 26]) if p >= 0 else None
            seq = h[12:16] if len(h) >= 16 else None
            rec = (t, ts, sta, fr, seq, h)
            (e3rx if classify_11e3(recs, i) == "RX" else e3tx).append(rec)
        elif "06201c01" in h and h.startswith("68"):
            i0 = h.find("06201c01") + 8
            sta = rev_sta(h, i0)
            if sta:
                mf = re.match(r"0200([0-9a-f]{10})", h[i0 + 12:])
                fr = fz5b(mf.group(1)) if mf else None
                f230.append((t, ts, sta, fr))
        else:
            for kind, kw in WARN_KINDS.items():
                if kw in h:
                    warn.append((t, ts, kind))
                    break

    def dedupe(lst):
        out, prev = [], None
        for rec in lst:
            key = tuple(rec[1:5])
            if prev == key:
                continue
            prev = key
            out.append(rec)
        return out

    return {
        "e4": dedupe(e4),
        "e3tx": dedupe(e3tx),
        "e3rx": dedupe(e3rx),
        "f230": f230,
        "warn": warn,
    }


def pair_11e3_delays(parsed) -> list[tuple[float, float, str, str, str]]:
    """按 (STA, 冻结, 序号) 回显配对 11E3 请求→响应。

    返回 [(请求时刻, 响应时刻, STA, 冻结, 序号)]；仅保留 0<=延迟<=15s 的配对。
    """
    tx_by_k: dict[tuple, float] = {}
    for t, ts, sta, fr, seq, h in parsed["e3tx"]:
        if seq:
            tx_by_k.setdefault((sta, fr, seq), t)
    out = []
    for t, ts, sta, fr, seq, h in parsed["e3rx"]:
        if not seq:
            continue
        k = (sta, fr, seq)
        if k in tx_by_k:
            d = t - tx_by_k[k]
            if 0 <= d <= 15:
                out.append((tx_by_k[k], t, sta, fr, seq))
    return out


def warn_histogram(parsed, bucket: str = "minute") -> dict[str, dict[str, int]]:
    """告警直方图：bucket='minute' 时键为 'HH:MM'。"""
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t, ts, kind in parsed["warn"]:
        key = ts[:5] if bucket == "minute" else ts[:8]
        out[key][kind] += 1
    return {k: dict(v) for k, v in sorted(out.items())}
