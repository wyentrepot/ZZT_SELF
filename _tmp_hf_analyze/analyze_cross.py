#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高频采集失败二次证据分析器（CCO 日志 + 侦听台日志交叉验证）。

用途：对台体日志分析出的失败表（如 020000012201），在 CCO 调试日志与侦听台
原始报文中寻找该表在采集时间窗内的踪迹，判断失败原因是：
  (a) 网络拥堵（CCO 有大量重试/排队，帧发出晚）
  (b) STA/电表一直不回（CCO 已发送但无任何回读记录/应答帧）
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

# 复用项目解析库
_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO / "libs"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from sim_concentrator.frame_codec import (
        decode_local_13762_frame,
        scan_frame,
        scan_local_frame,
    )
    _HAS_CODEC = True
except Exception:  # pragma: no cover
    _HAS_CODEC = False


# ---------------------------------------------------------------------------
# CCO 日志（UTF-8）解析
# ---------------------------------------------------------------------------
def cco_ts(l: str) -> Optional[str]:
    m = re.match(r"\[(\d{8})_(\d{2}:\d{2}:\d{2})", l)
    return m.group(2) if m else None


def analyze_cco_log(path: str | Path, target_addrs: list[str],
                    start_ts: str = "", end_ts: str = "") -> str:
    """在 CCO 日志中搜索目标表地址，返回证据文本。

    target_addrs 为 12 位表地址（如 020000012201）。
    CCO 日志中地址以小端 6 字节 hex 出现（如 022201000002 表示 020000012201）。
    """
    lines = Path(path).read_bytes().decode("utf-8", errors="replace").splitlines()
    out: list[str] = []
    out.append("=" * 70)
    out.append("  CCO 日志二次证据")
    out.append("=" * 70)
    out.append(f"日志: {path}  时间窗: {start_ts or '全量'} ~ {end_ts or '全量'}")
    out.append(f"目标表: {', '.join(target_addrs)}")

    # 地址两种编码：正序 12hex（台体侧）与小端 12hex（CCO 侧）
    # 例 020000012201 -> CCO 侧 022201000002；010000012201 -> 012201000001
    def to_cco_addr(a: str) -> str:
        # 6字节小端：a 是 6 字节大端拼接，反转字节序
        try:
            b = bytes.fromhex(a)
            return b[::-1].hex().upper()
        except ValueError:
            return a

    addr_forms = {}
    for a in target_addrs:
        addr_forms[a] = {a.upper(), to_cco_addr(a)}

    # 统计时间窗内命中
    hits_by_addr: dict[str, list[tuple[str, str]]] = defaultdict(list)
    aps_read_by_addr: Counter = Counter()  # ApsReadRecord 回读
    tx_by_addr: Counter = Counter()        # aps tx dst
    for l in lines:
        t = cco_ts(l)
        if not t:
            continue
        if start_ts and t < start_ts:
            continue
        if end_ts and t > end_ts:
            continue
        for a in target_addrs:
            for form in addr_forms[a]:
                if form in l:
                    hits_by_addr[a].append((t, l))
                    if "recv,ApsReadRecord" in l:
                        aps_read_by_addr[a] += 1
                    if "aps tx" in l and "dst" in l:
                        tx_by_addr[a] += 1
                    break

    for a in target_addrs:
        hits = hits_by_addr[a]
        out.append("")
        out.append(f"--- 表 {a} ---")
        out.append(f"  CCO 命中: {len(hits)} 条; ApsReadRecord 成功回读: {aps_read_by_addr[a]} 次; "
                   f"aps tx 发送: {tx_by_addr[a]} 次")
        if not hits:
            out.append("  (CCO 日志时间窗内无该表任何记录)")
        for t, l in hits[:30]:
            out.append(f"  [{t}] {l[:200]}")

    out.append("")
    out.append("说明: ApsReadRecord 表示电表数据成功回读；"
               "仅 aps tx 无回读 -> STA/电表未响应。")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 侦听台日志（HPLC 原始报文）解析
# ---------------------------------------------------------------------------
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


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="高频采集失败二次证据分析")
    ap.add_argument("cco_log", help="CCO 调试日志路径（UTF-8）")
    ap.add_argument("sniff_log", help="侦听台 HPLC 原始报文路径")
    ap.add_argument("addrs", nargs="+", help="目标表地址，如 020000012201")
    ap.add_argument("--start", default="", help="时间窗起点，如 16:42:00")
    ap.add_argument("--end", default="", help="时间窗终点，如 16:44:10")
    ap.add_argument("--cco-only", action="store_true", help="只分析 CCO 日志")
    ap.add_argument("--sniff-only", action="store_true", help="只分析侦听台日志")
    args = ap.parse_args()

    if not args.sniff_only:
        print(analyze_cco_log(args.cco_log, args.addrs, args.start, args.end))
    if not args.cco_only:
        if not args.sniff_only:
            print()
        print(analyze_sniff_log(args.sniff_log, args.addrs, args.start, args.end))


if __name__ == "__main__":
    main()
