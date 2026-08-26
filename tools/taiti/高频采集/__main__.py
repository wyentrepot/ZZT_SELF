# -*- coding: utf-8 -*-
"""高频采集失败分析 CLI 入口。

用法（在 tools/taiti/高频采集 目录下）：
    python -m 高频采集 taish <台体日志路径>
    python -m 高频采集 cco <CCO日志> <表地址>... [--start HH:MM:SS] [--end HH:MM:SS]
    python -m 高频采集 sniff <侦听台报文> <表地址>... [--start HH:MM:SS] [--end HH:MM:SS]
    python -m 高频采集 cross <CCO日志> <侦听台报文> <表地址>... [--start HH:MM:SS] [--end HH:MM:SS] [--cco-only|--sniff-only]
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把子目录加入 sys.path 以便 import 台体/CCO/侦听台 模块
_DIR = Path(__file__).resolve().parent
for _sub in ("台体", "CCO", "侦听台"):
    _p = str(_DIR / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CCO.analyze_cco import analyze_cco_log  # noqa: E402
from 台体.analyze_taish import analyze_taish_log, format_report  # noqa: E402
from 侦听台.analyze_sniff import analyze_sniff_log  # noqa: E402


def run_cross(cco_log, sniff_log, addrs, start_ts="", end_ts="",
              cco_only=False, sniff_only=False) -> str:
    """两段二次证据分析拼成完整报告。"""
    parts: list[str] = []
    if not sniff_only:
        parts.append(analyze_cco_log(cco_log, addrs, start_ts, end_ts))
    if not cco_only:
        if not sniff_only and parts:
            parts.append("")
        parts.append(analyze_sniff_log(sniff_log, addrs, start_ts, end_ts))
    return "\n".join(parts)


def _parse_cross_args(argv):
    import argparse
    ap = argparse.ArgumentParser(description="高频采集失败二次证据分析（cross）")
    ap.add_argument("cco_log", help="CCO 调试日志路径（UTF-8）")
    ap.add_argument("sniff_log", help="侦听台 HPLC 原始报文路径")
    ap.add_argument("addrs", nargs="+", help="目标表地址，如 020000012201")
    ap.add_argument("--start", default="", help="时间窗起点，如 16:42:00")
    ap.add_argument("--end", default="", help="时间窗终点，如 16:44:10")
    ap.add_argument("--cco-only", action="store_true", help="只分析 CCO 日志")
    ap.add_argument("--sniff-only", action="store_true", help="只分析侦听台日志")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]

    if cmd == "taish":
        if len(rest) < 1:
            print("用法: python -m 高频采集 taish <台体日志路径>", file=sys.stderr)
            return 2
        res = analyze_taish_log(rest[0])
        print(format_report(res))
        return 0

    if cmd == "cco":
        if len(rest) < 2:
            print("用法: python -m 高频采集 cco <CCO日志> <表地址>... [--start .. --end ..]", file=sys.stderr)
            return 2
        cco_log = rest[0]
        addrs = [a for a in rest[1:] if not a.startswith("--")]
        start, end = "", ""
        if "--start" in rest:
            start = rest[rest.index("--start") + 1]
        if "--end" in rest:
            end = rest[rest.index("--end") + 1]
        print(analyze_cco_log(cco_log, addrs, start, end))
        return 0

    if cmd == "sniff":
        if len(rest) < 2:
            print("用法: python -m 高频采集 sniff <侦听台报文> <表地址>... [--start .. --end ..]", file=sys.stderr)
            return 2
        sniff_log = rest[0]
        addrs = [a for a in rest[1:] if not a.startswith("--")]
        start, end = "", ""
        if "--start" in rest:
            start = rest[rest.index("--start") + 1]
        if "--end" in rest:
            end = rest[rest.index("--end") + 1]
        print(analyze_sniff_log(sniff_log, addrs, start, end))
        return 0

    if cmd == "cross":
        ns = _parse_cross_args(rest)
        print(run_cross(ns.cco_log, ns.sniff_log, ns.addrs, ns.start, ns.end,
                        ns.cco_only, ns.sniff_only))
        return 0

    print(f"未知命令: {cmd!r}（可用: taish | cco | sniff | cross）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
