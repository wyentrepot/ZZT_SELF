# -*- coding: utf-8 -*-
"""高频采集失败 侦听台 HPLC 报文分析（二次证据之一）。

在侦听台 HPLC 原始报文中搜索目标表地址，找出采集时间窗内该表的请求/应答帧，
判断失败原因：只有请求帧而无应答数据 -> STA/电表未回；无任何帧 -> 未抄到。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

# 复用项目解析库（把 libs 加入 sys.path）
_REPO = Path(__file__).resolve().parents[4]  # tools/taiti/高频采集/侦听台 -> 仓库根
for _p in (str(_REPO / "libs"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


SNIFF_LINE_RE = re.compile(r"\[\s*\d+\]\[(\d{2}:\d{2}:\d{2}\.\d{3})\](.*)")


def parse_sniff_line(l: str):
    m = SNIFF_LINE_RE.match(l)
    if m:
        return m.group(1), m.group(2).replace(" ", "")
    return None, None


def analyze_sniff_log(path: str | Path, target_addrs: list[str],
                      start_ts: str = "", end_ts: str = "") -> str:
    """在侦听台 HPLC 原始报文中搜索目标表地址（hex 层面 + 698 帧解嵌套）。"""
    txt = Path(path).read_bytes().decode("utf-8", errors="replace")
    lines = txt.splitlines()
    out: list[str] = []
    out.append("=" * 70)
    out.append("  侦听台 HPLC 报文二次证据")
    out.append("=" * 70)
    out.append(f"日志: {path}  时间窗: {start_ts or '全量'} ~ {end_ts or '全量'}")
    out.append(f"目标表: {', '.join(target_addrs)}")

    # hex 层面命中（12 位正序 或 小端）
    hex_forms = {}
    for a in target_addrs:
        try:
            b = bytes.fromhex(a)
            rev = b[::-1].hex().upper()
        except ValueError:
            rev = a
        hex_forms[a] = [a.upper(), rev]

    hits: list[tuple[str, str]] = []
    for l in lines:
        t, h = parse_sniff_line(l)
        if not t:
            continue
        if start_ts and t < start_ts:
            continue
        if end_ts and t > end_ts:
            continue
        for a in target_addrs:
            for form in hex_forms[a]:
                if form in (h or ""):
                    hits.append((t, h))
                    break
    out.append("")
    out.append(f"侦听台命中帧数: {len(hits)}")
    for t, h in hits[:15]:
        out.append(f"[{t}] {h[:200]}")
    out.append("")
    out.append("说明: HPLC 帧中 698 请求/应答的地址字段为小端；"
               "若只有请求帧(含 f101)而无对应应答/数据帧 -> STA/电表未回。")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="高频采集失败 侦听台报文分析")
    ap.add_argument("sniff_log", help="侦听台 HPLC 原始报文路径")
    ap.add_argument("addrs", nargs="+", help="目标表地址，如 020000012201")
    ap.add_argument("--start", default="", help="时间窗起点，如 16:42:00")
    ap.add_argument("--end", default="", help="时间窗终点，如 16:44:10")
    args = ap.parse_args(argv)
    print(analyze_sniff_log(args.sniff_log, args.addrs, args.start, args.end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
