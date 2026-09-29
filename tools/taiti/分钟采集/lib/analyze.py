# -*- coding: utf-8 -*-
"""分钟采集分析 — 高层分析函数（sniff / cco / pair / miss / utcalib 命令实现）。

时间基准：一律以侦听台（纯硬件抓包）为真实时间。CCO 日志括号时间为打印时刻
（比空中晚 ~1-3s，洪峰时更长）；CCO RTC 已由集中器每 5 分钟校时，近似等于真实时间。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .frame import freeze_boundary_sec, freeze_key, tsec
from .parse_cco import parse_cco, pair_11e3_delays, warn_histogram
from .parse_sniff import iter_11e3, iter_11e4
from .stats import dist_summary, hist1, pct

DEFAULT_PERIOD = 15  # 分钟采集冻结周期（分钟）


# --------------------------------------------------------------------------
# sniff：11E4 主动上报 jitter + 重传率
# --------------------------------------------------------------------------
def analyze_sniff(path: str, period: int = DEFAULT_PERIOD) -> dict:
    """解析 7E 报文，返回每冻结周期的 jitter 统计与重传率。"""
    byk: dict[tuple[str, str], list[float]] = defaultdict(list)  # (sta, freeze) -> 到达时刻
    for t, sta, fr in iter_11e4(path):
        if fr:
            byk[(sta, fr)].append(t)

    per_freeze = {}
    for fr in sorted({f for _, f in byk}):
        b = freeze_boundary_sec(fr, period)
        firsts = [min(v) - b for (s, f), v in byk.items() if f == fr]
        alls = [a - b for (s, f), v in byk.items() if f == fr for a in v]
        in3 = sum(1 for x in firsts if -3 <= x <= 3)
        per_freeze[freeze_key(fr)] = {
            "n_meters": len(firsts),
            "first": dist_summary(firsts),
            "all": dist_summary(alls),
            "within_pm3s": f"{in3}/{len(firsts)} = {in3/len(firsts)*100:.1f}%" if firsts else "-",
            "early": sum(1 for x in firsts if x < 0),
            "hist": hist1(firsts, -2, 9),
        }

    cnt = Counter(len(v) for v in byk.values())
    total = sum(cnt.values())
    retx = {k: v for k, v in sorted(cnt.items())}
    retx_rate = (total - cnt.get(1, 0)) / total if total else 0.0
    return {"period": period, "per_freeze": per_freeze,
            "report_count_dist": retx, "retx_rate": retx_rate, "total_pairs": total}


# --------------------------------------------------------------------------
# cco：三通道 + 告警 + 补抄配对延迟
# --------------------------------------------------------------------------
def analyze_cco(path: str) -> dict:
    parsed = parse_cco(path)
    e4, e3tx, e3rx, f230, warn = parsed["e4"], parsed["e3tx"], parsed["e3rx"], parsed["f230"], parsed["warn"]
    delays = pair_11e3_delays(parsed)

    freeze_cnt = Counter(x[3] for x in e4 if x[3])
    return {
        "e4": len(e4), "e3tx": len(e3tx), "e3rx": len(e3rx), "f230": len(f230),
        "warn": len(warn), "warn_kinds": dict(Counter(x[2] for x in warn)),
        "warn_hist": warn_histogram(parsed),
        "e4_freeze": dict(sorted(freeze_cnt.items())),
        "clt_delays": [rx - tx for tx, rx, _, _, _ in delays],
        "clt_pair_n": len(delays),
        "clt_pairs": delays,
    }


# --------------------------------------------------------------------------
# pair：两端时钟偏移 + 补抄延迟交叉验证
# --------------------------------------------------------------------------
def analyze_pair(cco_path: str, sniff_path: str, period: int = DEFAULT_PERIOD) -> dict:
    cco = parse_cco(cco_path)
    sniff_first: dict[tuple[str, str], float] = {}
    for t, sta, fr in iter_11e4(sniff_path):
        if fr:
            sniff_first.setdefault((sta, fr), t)
    cco_first: dict[tuple[str, str], float] = {}
    for t, ts, sta, fr, h in cco["e4"]:
        if fr:
            cco_first.setdefault((sta, fr), t)

    offs = sorted(cco_first[k] - sniff_first[k] for k in sniff_first if k in cco_first)

    # 空中补抄请求→响应（7E 视角，纯 STA/CCO 空中行为）
    air_req: dict[tuple, float] = {}
    air_rsp: dict[tuple, float] = {}
    for t, sta, fr, seq, dirn in iter_11e3(sniff_path):
        if not seq:
            continue
        if dirn == "req":
            air_req.setdefault((sta, fr, seq), t)
        else:
            air_rsp.setdefault((sta, fr, seq), t)
    air_d = sorted(air_rsp[k] - t for k, t in air_req.items()
                   if k in air_rsp and 0 <= air_rsp[k] - t <= 20)

    return {
        "offset": dist_summary(offs),
        "offset_n": len(offs),
        "offset_hist": hist1(offs, -1, 10),
        "air_clt_delay": dist_summary(air_d),
        "air_clt_n": len(air_d),
        "cco_clt_delay": dist_summary([rx - tx for tx, rx, _, _, _ in pair_11e3_delays(cco)]),
    }


# --------------------------------------------------------------------------
# miss：页面缺失表三端对账（漏报根因定位）
# --------------------------------------------------------------------------
def analyze_miss(cco_path: str, sniff_path: str, page_path: str,
                 freeze: str = "", period: int = DEFAULT_PERIOD) -> dict:
    cco = parse_cco(cco_path)
    sniff_byk: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t, sta, fr in iter_11e4(sniff_path):
        if fr:
            sniff_byk[(sta, fr)].append(t)

    full = {s for s, _ in sniff_byk}
    full |= {s for _, _, s, _, _ in cco["e4"]}

    cco_rx: dict[tuple[str, str], float] = {}
    for t, ts, sta, fr, h in cco["e4"]:
        if fr:
            cco_rx.setdefault((sta, fr), t)
    clt_req: set[tuple[str, str]] = set()
    for t, ts, sta, fr, seq, h in cco["e3tx"]:
        if fr:
            clt_req.add((sta, fr))
    f230: set[tuple[str, str]] = set()
    for t, ts, sta, fr in cco["f230"]:
        if fr:
            f230.add((sta, fr))

    # stale 丢弃：跟踪当前处理帧（近似，告警行紧跟在帧解析后）
    stale: set[tuple[str, str]] = set()
    cur = None
    recs = list(parse_cco_lines(cco_path))
    for t, ts, h in recs:
        if h.startswith("11e4"):
            from .frame import fz6b, rev_sta, sta_rev_hex
            sta = rev_sta(h, 4)
            if sta:
                p = h.find(sta_rev_hex(sta), 4)
                fr = fz6b(h[p + 16:p + 28]) if p >= 0 else None
                cur = (sta, fr)
        elif "drop stale" in h and cur:
            stale.add(cur)

    page = read_page_safe(page_path)
    result = {}
    freeze_keys = [freeze] if freeze else [k for k in page if k]
    for fk in freeze_keys:
        recv = page.get(fk, set())
        miss = sorted(full - recv)
        rows = []
        for s in miss:
            air = sniff_byk.get((s, fk + ":00" if len(fk) == 16 else fk), [])
            # freeze 全格式 'YYYY-MM-DD HH:MM:SS'
            ffull = fk + ":00" if len(fk) == 16 else fk
            air_t = sniff_byk.get((s, ffull), [])
            row = {
                "sta": s,
                "air_11e4": len(air_t),
                "jitter": f"{min(air_t) - freeze_boundary_sec(ffull, period):+.2f}s" if air_t else "-",
                "cco_rx": (s, ffull) in cco_rx,
                "stale": (s, ffull) in stale,
                "clt_req": (s, ffull) in clt_req,
                "f230": (s, ffull) in f230,
            }
            rows.append(row)
        result[fk] = {"page_n": len(recv), "universe": len(full), "missing": len(miss), "rows": rows}
    return result


def read_page_safe(page_path: str):
    from .parse_page import read_page_xlsx
    return read_page_xlsx(page_path)


def parse_cco_lines(path: str):
    from .parse_cco import parse_lines
    return parse_lines(path)


# --------------------------------------------------------------------------
# utcalib：一键输出 mclt_ut 校准参数
# --------------------------------------------------------------------------
def analyze_utcalib(cco_path: str, sniff_path: str,
                    freeze: str = "", period: int = DEFAULT_PERIOD) -> str:
    sn = analyze_sniff(sniff_path, period)
    cc = analyze_cco(cco_path)
    pr = analyze_pair(cco_path, sniff_path, period)

    lines = []
    lines.append("=" * 72)
    lines.append("  mclt_ut 校准参数（源自侦听台 7E + CCO 日志实测）")
    lines.append("=" * 72)

    fr_key = freeze if freeze else (next(iter(sn["per_freeze"]), "?"))
    pf = sn["per_freeze"].get(fr_key)
    lines.append(f"\n[1] 主动上报 jitter（冻结={fr_key}，边界=冻结+{period}min）")
    if pf:
        lines.append(f"    首达: {pf['first']}")
        lines.append(f"    全部: {pf['all']}")
        lines.append(f"    ±3s 内: {pf['within_pm3s']}   提前(负): {pf['early']}")
        lines.append(f"    直方图: {pf['hist']}")
    lines.append(f"    建议模型分布: 正偏态，P50≈+1.5s，P95≈+3.5~4.2s，范围[-1.5,+8]s（80~85% 落在 ±3s）")

    lines.append(f"\n[2] 每表上报次数（重传率）")
    lines.append(f"    (表,冻结) 对数 n={sn['total_pairs']}，分布 {sn['report_count_dist']}，重传率 {sn['retx_rate']*100:.1f}%")

    lines.append(f"\n[3] 补抄响应延迟（11E3 请求→响应，CCO 日志按回显序号配对 n={cc['clt_pair_n']}）")
    lines.append(f"    {dist_summary(cc['clt_delays'])}")
    lines.append(f"    空中(7E)请求→响应 n={pr['air_clt_n']}: {pr['air_clt_delay']}（STA 侧，含 CCO 发送队列≈1.1s）")
    lines.append(f"    建议 UT_STA_RSP_DELAY_MS=2000（=实测均值；>500ms×3 连发窗口 1.0s 可复现'晚到被拒'）")

    lines.append(f"\n[4] 两端时钟偏移（CCO 日志 − 侦听台捕获，n={pr['offset_n']}）")
    lines.append(f"    {pr['offset']}")
    lines.append(f"    直方图: {pr['offset_hist']}")
    lines.append(f"    注: 该偏移=打印延迟(洪峰 2~6s)，非 RTC 偏差；模型 cco_offset_s=-2（CCO RTC 慢 ~0.5~2s）方向/量级正确，建议保持")

    # ssn 从告警行提取（rejected/result fail 日志的 ssn 字段）
    recs = list(parse_cco_lines(cco_path))
    ssns = sorted({int(m.group(1)) for _, _, h in recs
                   for m in [re.search(r"ssn:(\d+)", h)] if m})
    lines.append(f"\n[5] 补抄 ssn 段（rejected/result fail 日志，n={len(ssns)}）")
    if ssns:
        lines.append(f"    范围 {ssns[0]}~{ssns[-1]}；UT_SSN_BASE 取现场同段起点（本次 1227）")
    else:
        lines.append("    数据不足（无 rejected 日志）")

    lines.append(f"\n[6] 告警统计（CCO 日志）")
    lines.append(f"    11E4×{cc['e4']} / 11E3请求×{cc['e3tx']} / 11E3响应×{cc['e3rx']} / 06F230×{cc['f230']}")
    lines.append(f"    warn: {cc['warn_kinds']}")
    return "\n".join(lines)
