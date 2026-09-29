# -*- coding: utf-8 -*-
"""分钟采集（MCLT，15 分钟冻结周期）离线分析统一入口。

用法（在 tools/taiti/分钟采集 目录下）：
    python run.py sniff <侦听台7E报文> [--freeze HH:MM[:SS]] [--period 15]
        11E4 主动上报 jitter 分布（相对周期结束边界）+ 每表上报次数（重传率）
    python run.py cco <CCO日志>
        11E4/11E3/06F230 三通道统计 + stale/rejected 告警直方图 + 11E3 请求→响应延迟
    python run.py page <页面xlsx>
        页面实收（冻结周期 → 表集合）
    python run.py pair <CCO日志> <侦听台7E报文>
        两端时钟偏移（同一 11E4 两端时刻差）+ 补抄延迟交叉验证
    python run.py miss <CCO日志> <侦听台7E报文> <页面xlsx> [--freeze HH:MM]
        页面缺失表三端对账（空中11E4 × CCO收/丢 × 补抄 × 06F230 → 漏报根因）
    python run.py utcalib <CCO日志> <侦听台7E报文> [--freeze HH:MM[:SS]]
        一键输出 mclt_ut 校准参数（jitter/重传/补抄延迟/时钟偏移/ssn）

时间基准一律以侦听台（纯硬件抓包）为准；详见 README.md 与 分析规则.md。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from lib.analyze import (  # noqa: E402
    analyze_cco, analyze_miss, analyze_pair, analyze_sniff, analyze_utcalib,
)
from lib.parse_page import page_stats, read_page_xlsx  # noqa: E402


def cmd_sniff(a) -> int:
    res = analyze_sniff(a.sniff, a.period)
    print(f"7E 报文: {a.sniff}   周期: {a.period}min")
    print(f"11E4 解析 (表,冻结) 对: {res['total_pairs']}   重传率: {res['retx_rate']*100:.1f}%")
    print(f"每对上报次数分布: {res['report_count_dist']}")
    for fk, st in res["per_freeze"].items():
        print(f"\n冻结周期 {fk}（边界=冻结+{a.period}min）: 表数 {st['n_meters']}")
        print(f"  首达 jitter: {st['first']}")
        print(f"  全部(含重传): {st['all']}")
        print(f"  ±3s 内: {st['within_pm3s']}   提前到达(负): {st['early']}")
        print(f"  直方图(1s): {st['hist']}")
    return 0


def cmd_cco(a) -> int:
    res = analyze_cco(a.cco)
    print(f"CCO 日志: {a.cco}")
    print(f"11E4×{res['e4']}  11E3请求×{res['e3tx']}  11E3响应×{res['e3rx']}  06F230×{res['f230']}")
    print(f"告警总计×{res['warn']}: {res['warn_kinds']}")
    print("\n11E3 请求→响应延迟（按回显序号配对）:")
    from lib.stats import dist_summary
    print("  " + dist_summary(res["clt_delays"]))
    print("\n告警按分钟直方图:")
    for k, v in res["warn_hist"].items():
        print(f"  {k}: {v}")
    return 0


def cmd_page(a) -> int:
    page = read_page_xlsx(a.page)
    print(f"页面 xlsx: {a.page}")
    for k, n in page_stats(page):
        print(f"  冻结周期 {k}: 实收 {n} 块")
    return 0


def cmd_pair(a) -> int:
    from lib.stats import dist_summary
    res = analyze_pair(a.cco, a.sniff, a.period)
    print(f"CCO: {a.cco}\n7E : {a.sniff}")
    print(f"\n两端时钟偏移（CCO 日志 − 侦听台捕获，n={res['offset_n']}）:")
    print("  " + res["offset"])
    print(f"  直方图(1s): {res['offset_hist']}")
    print(f"\n补抄请求→响应延迟交叉验证:")
    print(f"  CCO 视角: {res['cco_clt_delay']}")
    print(f"  空中(7E)视角 n={res['air_clt_n']}: {res['air_clt_delay']}")
    return 0


def cmd_miss(a) -> int:
    try:
        res = analyze_miss(a.cco, a.sniff, a.page, a.freeze, a.period)
    except RuntimeError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2
    print(f"CCO: {a.cco}\n7E : {a.sniff}\n页面: {a.page}")
    for fk, r in res.items():
        print(f"\n=== 冻结周期 {fk}: 页面实收 {r['page_n']}/{r['universe']}，缺失 {r['missing']} 块 ===")
        for row in r["rows"]:
            print(f"  {row['sta']}: 空中11E4={row['air_11e4']}次(jitter {row['jitter']}) "
                  f"CCO收={row['cco_rx']} stale丢={row['stale']} 补抄={row['clt_req']} 06F230={row['f230']}")
    return 0


def cmd_utcalib(a) -> int:
    print(analyze_utcalib(a.cco, a.sniff, a.freeze, a.period))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sniff", help="11E4 主动上报 jitter + 重传率")
    p.add_argument("sniff")
    p.add_argument("--freeze", default="")
    p.add_argument("--period", type=int, default=15)
    p.set_defaults(func=cmd_sniff)

    p = sub.add_parser("cco", help="CCO 日志三通道 + 告警 + 补抄延迟")
    p.add_argument("cco")
    p.set_defaults(func=cmd_cco)

    p = sub.add_parser("page", help="页面 xlsx 实收")
    p.add_argument("page")
    p.set_defaults(func=cmd_page)

    p = sub.add_parser("pair", help="两端时钟偏移 + 补抄延迟交叉")
    p.add_argument("cco")
    p.add_argument("sniff")
    p.add_argument("--period", type=int, default=15)
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("miss", help="页面缺失表三端对账")
    p.add_argument("cco")
    p.add_argument("sniff")
    p.add_argument("page")
    p.add_argument("--freeze", default="")
    p.add_argument("--period", type=int, default=15)
    p.set_defaults(func=cmd_miss)

    p = sub.add_parser("utcalib", help="一键输出 mclt_ut 校准参数")
    p.add_argument("cco")
    p.add_argument("sniff")
    p.add_argument("--freeze", default="")
    p.add_argument("--period", type=int, default=15)
    p.set_defaults(func=cmd_utcalib)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
