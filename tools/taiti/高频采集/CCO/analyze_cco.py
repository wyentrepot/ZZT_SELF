# -*- coding: utf-8 -*-
"""高频采集失败 CCO 日志分析（二次证据之一）。

在 CCO 调试日志中寻找目标表在采集时间窗内的踪迹，统计：
- CCO 命中行数、ApsReadRecord 成功回读次数、aps tx 发送次数
判断失败原因：网络拥堵（大量重试/排队） vs STA/电表未响应（已发送无回读）。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

# 复用项目解析库（把 libs 加入 sys.path）
_REPO = Path(__file__).resolve().parents[4]  # tools/taiti/高频采集/CCO -> 仓库根
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


def cco_ts(l: str) -> Optional[str]:
    m = re.match(r"\[(\d{8})_(\d{2}:\d{2}:\d{2})", l)
    return m.group(2) if m else None


def analyze_cco_log(path: str | Path, target_addrs: list[str],
                    start_ts: str = "", end_ts: str = "") -> str:
    """在 CCO 日志中搜索目标表地址，返回证据文本。

    target_addrs 为 12 位表地址（如 020000012201）。
    CCO 日志中地址以小端 6 字节 hex 出现（如 022201000002 表示 020000012201）。
    """
    lines = Path(path).read_bytes().decode("utf-8-sig", errors="replace").splitlines()
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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="高频采集失败 CCO 日志分析")
    ap.add_argument("cco_log", help="CCO 调试日志路径（UTF-8）")
    ap.add_argument("addrs", nargs="+", help="目标表地址，如 020000012201")
    ap.add_argument("--start", default="", help="时间窗起点，如 16:42:00")
    ap.add_argument("--end", default="", help="时间窗终点，如 16:44:10")
    args = ap.parse_args(argv)
    print(analyze_cco_log(args.cco_log, args.addrs, args.start, args.end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
