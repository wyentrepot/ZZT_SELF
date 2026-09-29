# -*- coding: utf-8 -*-
"""分钟采集分析 — 帧/日志基础解析原语。

全部踩坑记录见《分析规则.md》§3。要点：
- 7E 报文 hex 带空格/大小写不统一 → hexc() 归一化；
- 冻结时间 6B BCD（秒分时日月年）必须 BCD 解码，不能当 hex；
- 帧内 STA 地址是显示地址 66000050xxxx 的反转（XXXX 50 00 00 66）；
- 11E4 冻结偏移 = STA 后第 2 字节起；11E3 = STA 后第 1 字节起；06F230 = `0200` 后 5B（无秒）。
"""
from __future__ import annotations

import re
from typing import Optional

HEX_RE = re.compile(r"[^0-9a-fA-F]")
# 显示地址 66000050xxxx 在帧内为 XXXX 50 00 00 66（字节反转）
STA_RE = re.compile(r"([0-9a-f]{4})50000066")


def hexc(s: str) -> str:
    """去掉字符串中所有非 hex 字符（空格/分隔符），统一小写。"""
    return HEX_RE.sub("", s).lower()


def bcd2(s: str) -> int:
    """1 字节 BCD（2 hex 字符）→ 十进制。注意不是 int(s, 16)。"""
    return int(s[0], 16) * 10 + int(s[1], 16)


def fz6b(b: str) -> Optional[str]:
    """6 字节 BCD（秒分时日月年）→ 'YYYY-MM-DD HH:MM:SS'；非法返回 None。"""
    if len(b) != 12:
        return None
    sec, minu, hour, day, mon, year = (bcd2(b[k:k + 2]) for k in (0, 2, 4, 6, 8, 10))
    if 0 <= sec < 60 and 0 <= minu < 60 and 0 <= hour < 24 and 1 <= day <= 31 and 1 <= mon <= 12:
        return f"20{year:02d}-{mon:02d}-{day:02d} {hour:02d}:{minu:02d}:{sec:02d}"
    return None


def fz5b(b: str) -> Optional[str]:
    """5 字节 BCD（分时日月年，06F230 用，无秒）→ 'YYYY-MM-DD HH:MM:00'。"""
    if len(b) != 10:
        return None
    minu, hour, day, mon, year = (bcd2(b[k:k + 2]) for k in (0, 2, 4, 6, 8))
    if 0 <= minu < 60 and 0 <= hour < 24 and 1 <= day <= 31 and 1 <= mon <= 12:
        return f"20{year:02d}-{mon:02d}-{day:02d} {hour:02d}:{minu:02d}:00"
    return None


def rev_sta(h: str, start: int = 0, window: int = 80) -> Optional[str]:
    """从帧内提取 STA 显示地址（66000050xxxx）。

    帧内 STA 恒为 XXXX 50 00 00 66；取 `50000066` 前 4 个 hex 字符为反转后的
    末 2 字节，反拼回显示地址。
    """
    m = STA_RE.search(h[start:start + window])
    if not m:
        return None
    x = m.group(1)
    return "66000050" + x[2:4] + x[0:2]


def sta_rev_hex(sta: str) -> str:
    """显示地址 → 帧内字节序 hex（供 find 定位）。"""
    return "".join(sta[i:i + 2] for i in range(10, -1, -2))


def tsec(t: str) -> float:
    """'HH:MM:SS[.mmm]' → 当日秒数（float）。"""
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def freeze_boundary_sec(freeze_ts: str, period_min: int = 15) -> float:
    """冻结时刻对应的周期结束边界（当日秒数）。

    口径（与 STA/模型一致）：STA 在冻结周期结束边界上报上一周期数据，
    即 freeze=X 的上报窗口中心在 X+period。如 freeze=10:15 → 边界 10:30:00。
    """
    hh = int(freeze_ts[11:13])
    mm = int(freeze_ts[14:16])
    return (hh * 60 + mm + period_min) * 60.0


def freeze_key(freeze_ts: Optional[str]) -> str:
    """冻结时刻 → 周期键（'YYYY-MM-DD HH:MM'，用于分周期统计）。"""
    return freeze_ts[:16] if freeze_ts else "?"
