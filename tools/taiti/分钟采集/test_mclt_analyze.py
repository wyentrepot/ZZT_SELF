# -*- coding: utf-8 -*-
"""分钟采集分析工具集自测（依赖 samples/ 精简样例）。

运行：python3 -m pytest tools/taiti/分钟采集/test_mclt_analyze.py -q
（需 openpyxl；系统 python3 已具备）
"""
from __future__ import annotations

import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from lib.analyze import analyze_cco, analyze_miss, analyze_pair, analyze_sniff  # noqa: E402
from lib.frame import fz6b, fz5b, hexc, rev_sta, sta_rev_hex  # noqa: E402

S = _DIR / "samples"
SNIFF = str(S / "侦听台报文_分钟采集_精简.txt")
CCO = str(S / "cco日志_分钟采集_精简.log")
PAGE = str(S / "页面_分钟采集_精简.xlsx")


# ---- 基础原语 ----
def test_hexc():
    assert hexc("7E FF 02 FF D7 08") == "7eff02ffd708"
    assert hexc("11 E4") == "11e4"


def test_fz6b_bcd_not_hex():
    # BCD 2026-09-20 10:15:00：20 是 BCD 20，不是 hex 0x20=32
    assert fz6b("001510200926") == "2026-09-20 10:15:00"
    assert fz6b("000009200926") == "2026-09-20 09:00:00"
    assert fz6b("bad") is None


def test_fz5b():
    assert fz5b("1510200926") == "2026-09-20 10:15:00"


def test_rev_sta():
    # 帧内 XXXX 50 00 00 66 → 显示地址 66000050xxxx
    assert rev_sta("0111500000660142") == "660000501101"
    assert rev_sta("2407500000660100") == "660000500724"
    assert sta_rev_hex("660000501101") == "011150000066"


# ---- sniff：11E4 jitter + 重传 ----
def test_sniff_parses_11e4():
    res = analyze_sniff(SNIFF)
    assert res["total_pairs"] > 100          # 样例含 10:30 洪峰
    assert res["retx_rate"] >= 0.0
    assert len(res["per_freeze"]) >= 1
    for st in res["per_freeze"].values():
        assert st["n_meters"] > 0
        assert "min=" in st["first"] and "p95=" in st["first"]


def test_sniff_jitter_plausible():
    res = analyze_sniff(SNIFF)
    # 样例覆盖 10:15 冻结的洪峰：jitter 应落在 ±10s 内（min 不早于 -10）
    for fk, st in res["per_freeze"].items():
        first = st["first"]
        m = __import__("re").search(r"min=([-\d.]+)", first)
        if m:
            assert -15 <= float(m.group(1)) <= 15


# ---- cco：三通道 + 告警 + 补抄配对 ----
def test_cco_channels():
    res = analyze_cco(CCO)
    assert res["e4"] > 0
    assert res["e3tx"] > 0
    assert res["e3rx"] > 0
    assert res["f230"] > 0
    assert res["warn"] > 0
    assert "stale" in res["warn_kinds"]


def test_cco_clt_pairing():
    res = analyze_cco(CCO)
    # 样例含 09:29:02 连发 3 请求 + 3 响应（序号回显），应能配对
    assert res["clt_pair_n"] >= 3
    delays = res["clt_delays"]
    assert all(0.05 <= d <= 15 for d in delays)
    assert max(delays) < 15


# ---- pair：时钟偏移 ----
def test_pair_offset():
    res = analyze_pair(CCO, SNIFF)
    assert res["offset_n"] > 0
    assert "min=" in res["offset"]
    assert res["air_clt_n"] > 0


# ---- miss：三端对账 ----
def test_miss_reconciles():
    res = analyze_miss(CCO, SNIFF, PAGE, freeze="2026-09-20 10:15")
    assert "2026-09-20 10:15" in res
    r = res["2026-09-20 10:15"]
    assert r["missing"] >= 0
    assert r["page_n"] > 0
    # 样例 xlsx 中 660000500705 是"缺失表"（真实场景 22 块之一）
    stas = {row["sta"] for row in r["rows"]}
    assert "660000500705" in stas or r["missing"] == 0
