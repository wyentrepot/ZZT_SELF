# -*- coding: utf-8 -*-
"""分钟采集分析 — 侦听台 7E 空中报文解析。

行格式：`[     1][09:15:57.280]7E FF 02 ... 7E`（一帧一行）。
解析内容：
- 11E4 主动上报（STA→CCO）：(捕获时刻, STA, 冻结)；
- 11E3 补抄（双向）：请求（CCO→STA，bytes4-5=`0105`）与响应（STA→CCO，bytes4-5=`c115`），
  按帧内序号（bytes6-7）可精确配对（该 CCO 固件回显请求序号）。
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from .frame import fz6b, hexc, rev_sta, sta_rev_hex, tsec

LINE_RE = re.compile(r"^\[\s*\d+\]\[([\d:.]+)\](.*)$")

# 11E3 方向判别：bytes4-5（应用帧 `11e3` 后）
E3_REQ_MARK = "0105"   # CCO→STA 补抄请求
E3_RSP_MARK = "c115"   # STA→CCO 补抄响应


def _parse_11e4(h: str, t: float):
    """返回 (sta, freeze) 或 None。冻结偏移 = STA 后第 2 字节起。"""
    for mm in re.finditer(r"11e4", h):
        sta = rev_sta(h, mm.end())
        if not sta:
            continue
        p = h.find(sta_rev_hex(sta), mm.end())
        if p < 0:
            continue
        fr = fz6b(h[p + 16:p + 28])
        if fr:
            return sta, fr
    return None


def _parse_11e3(h: str, t: float):
    """返回 (sta, freeze, seq, d4, dirn) 或 None。冻结偏移 = STA 后第 1 字节起。"""
    for mm in re.finditer(r"11e3", h):
        sta = rev_sta(h, mm.end())
        if not sta:
            continue
        p = h.find(sta_rev_hex(sta), mm.end())
        if p < 0:
            continue
        fr = fz6b(h[p + 14:p + 26])
        d4 = h[mm.start() + 8:mm.start() + 12]      # bytes 4-5
        seq = h[mm.start() + 12:mm.start() + 16] if len(h) >= mm.start() + 16 else None  # bytes 6-7
        if fr:
            return sta, fr, seq, d4
    return None


def iter_11e4(path: str) -> Iterator[tuple[float, str, str]]:
    """逐条产出 (捕获时刻秒, STA, 冻结)。"""
    with open(path, errors="ignore") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            t = tsec(m.group(1))
            h = hexc(m.group(2))
            r = _parse_11e4(h, t)
            if r:
                yield t, r[0], r[1]


def iter_11e3(path: str):
    """逐条产出 (捕获时刻秒, STA, 冻结, seq, dirn)。dirn='req'|'rsp'。"""
    with open(path, errors="ignore") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            t = tsec(m.group(1))
            h = hexc(m.group(2))
            r = _parse_11e3(h, t)
            if r:
                sta, fr, seq, d4 = r
                dirn = "rsp" if d4 == E3_RSP_MARK else "req"
                yield t, sta, fr, seq, dirn
