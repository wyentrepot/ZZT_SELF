# -*- coding: utf-8 -*-
"""高频采集台体日志分析器（复用项目 parser_lib / sim_concentrator.frame_codec）。

输入：台体高频采集日志（GBK 编码），形如：
    2026-08-20 16:42:37:272 MTC@admin-PC: "send cmd to cco:'6851004304...630198900000...F101...'O"
    2026-08-20 16:42:41:640 MTC@admin-PC: "ReadMeter Success, mac addr:'040000011201'O"
    2026-08-20 16:43:59:934 MTC@admin-PC: "read cycle reach max(3)"
    2026-08-20 16:44:00:012 MTC@admin-PC: "read fail(4)"

分析内容：
- 采集帧（send cmd to cco）目标表地址、seq 序号、发送时间
- ReadMeter 成功/失败
- 每表抄读次数（找补抄超过 3 次的表）
- 建档案帧（11H/F1）中的档案表列表
- 最终判定（read fail / read cycle reach max）
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 复用项目解析库（把 libs 加入 sys.path）
_REPO = Path(__file__).resolve().parents[4]  # tools/taiti/高频采集/台体 -> 仓库根
for _p in (str(_REPO / "libs"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
# 采集帧：send cmd to cco:'...6301989000 00 <6B地址> F101...'（CCO 本地协议 afn=0x63）
# 注意日志中地址为 6 字节小端，如 04 00 00 01 12 01 -> 显示 040000011201
# 已验证：前缀为 630198900000（4 个 0）+ 12位hex地址 + F101
READ_FRAME_RE = re.compile(r"630198900000([0-9A-Fa-f]{12})F101")
# ReadMeter 结果
READ_METER_RE = re.compile(r"ReadMeter\s+(Success|Fail),\s*mac\s+addr:'([0-9A-Fa-f]{12})'")
# Recieved F101（CCO 对并发抄表的应答）
RECEIVED_F101_RE = re.compile(r"Recieved\s+F101", re.I)
# 忙/满
BUSY_RE = re.compile(r"Meter reading busy|maximum allowable", re.I)
# 建档案帧：send cmd 中 afn=0x11 的 F1 档案（11 01 00 / 11 80 1c 等）
PROFILE_RE = re.compile(r"011101000?([0-9A-Fa-f]+)")
# 组网成功率
SUCCESS_RATE_RE = re.compile(r"successRate\s*=\s*([0-9.]+),\s*NeedsuccessRate\s*=\s*([0-9.]+)")

# 地址规范化：12位hex 直接大写（与日志 ReadMeter 一致）
def norm_addr(addr: str) -> str:
    return addr.upper()


@dataclass
class ReadEvent:
    seq: Optional[int]
    addr: str
    ts: str
    result: str  # "SEND" | "OK" | "FAIL" | "BUSY" | "RECEIVED_F101"


@dataclass
class TaishAnalysis:
    log_path: Path
    send_counts: Counter = field(default_factory=Counter)
    send_times: dict[str, list[str]] = field(default_factory=dict)
    ok_set: set[str] = field(default_factory=set)
    fail_counts: Counter = field(default_factory=Counter)
    busy_counts: Counter = field(default_factory=Counter)
    f101_received: Counter = field(default_factory=Counter)
    events: list[ReadEvent] = field(default_factory=list)
    success_rates: list[tuple[str, float, float]] = field(default_factory=list)
    profile_addresses: set[str] = field(default_factory=set)
    profile_blocks: list[list[str]] = field(default_factory=list)
    final_verdict: str = ""

    @property
    def never_ok(self) -> list[str]:
        return sorted(a for a in self.send_counts if a not in self.ok_set)

    @property
    def over_retry(self) -> list[str]:
        # 补抄超过 3 次仍无成功的表
        return sorted(a for a in self.send_counts
                      if self.send_counts[a] > 3 and a not in self.ok_set)


def analyze_taish_log(path: str | Path) -> TaishAnalysis:
    """解析台体高频采集日志。"""
    p = Path(path)
    data = p.read_bytes().decode("gbk", errors="replace")
    lines = data.splitlines()

    res = TaishAnalysis(log_path=p)

    for l in lines:
        ts = l[:23] if len(l) >= 23 else ""
        # 组网成功率
        m = SUCCESS_RATE_RE.search(l)
        if m:
            res.success_rates.append((ts, float(m.group(1)), float(m.group(2))))
        # 最终判定
        if "read fail" in l or "执行结果" in l:
            res.final_verdict = l.strip()
        # send cmd 采集帧
        if "send cmd" in l:
            fm = READ_FRAME_RE.search(l)
            if fm:
                addr = norm_addr(fm.group(1))
                # seq 从帧中取（info[5]）
                hm = re.search(r"'([0-9A-Fa-f]+)'", l)
                seq = None
                if hm:
                    try:
                        bs = bytes.fromhex(hm.group(1))
                        if len(bs) > 9:
                            seq = bs[9]
                    except ValueError:
                        pass
                res.send_counts[addr] += 1
                res.send_times.setdefault(addr, []).append(ts)
                res.events.append(ReadEvent(seq, addr, ts, "SEND"))
            # 建档案帧（afn=0x11，DT1=0x01）——仅在非采集帧行判断，避免误伤地址含 011101 的采集帧
            elif "01110100" in l:
                hm = re.search(r"'([0-9A-Fa-f]+)'", l)
                if hm:
                    blk = parse_profile_block(hm.group(1))
                    if blk:
                        res.profile_blocks.append(blk)
                        res.profile_addresses.update(blk)
        # ReadMeter
        m = READ_METER_RE.search(l)
        if m:
            status, addr = m.group(1), norm_addr(m.group(2))
            if status == "Success":
                res.ok_set.add(addr)
            else:
                res.fail_counts[addr] += 1
            res.events.append(ReadEvent(None, addr, ts, status))
        if RECEIVED_F101_RE.search(l):
            res.f101_received["f101"] += 1
            # 关联最近的 SEND 地址
            for ev in reversed(res.events):
                if ev.result == "SEND":
                    res.f101_received[ev.addr] += 1
                    break
        if BUSY_RE.search(l):
            res.busy_counts["busy"] += 1

    return res


def parse_profile_block(hex_str: str) -> Optional[list[str]]:
    """解析建档案帧（68 LL 00 43 ... 11 01 00 <cnt> <addr6B type1B> x cnt）返回地址列表。

    addr 6 字节小端（如 04 00 00 01 12 01 -> 040000011201），
    每条 7 字节：6B 地址 + 1B 协议类型（0x03 = DL/T 698.45）。
    """
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return None
    if len(raw) < 16:
        return None
    # 实测布局：68 LL 00 43 00 00 00 00 00 01 11 01 00 <cnt> <addr6B type1B> ...
    try:
        if raw[10] == 0x11 and raw[11] == 0x01 and raw[12] == 0x00:
            cnt = raw[13]
            addrs = []
            pos = 14
            for _ in range(cnt):
                if pos + 7 > len(raw):
                    break
                addr = raw[pos:pos + 6].hex().upper()
                proto = raw[pos + 6]
                if proto == 0x03:  # DL/T 698.45
                    addrs.append(addr)
                pos += 7
            return addrs
    except IndexError:
        return None
    return None


def format_report(res: TaishAnalysis) -> str:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("  台体高频采集日志分析")
    lines.append("=" * 70)
    lines.append(f"日志: {res.log_path}")
    lines.append(f"组网成功率节点: {len(res.success_rates)} 次")
    for ts, cur, need in res.success_rates[:5]:
        lines.append(f"  {ts} successRate={cur:.3f} needs={need:.3f}")
    lines.append("")
    lines.append(f"send 采集帧总数: {sum(res.send_counts.values())}  涉及表: {len(res.send_counts)}")
    lines.append(f"ReadMeter Success 表: {len(res.ok_set)}  ReadMeter Fail: {sum(res.fail_counts.values())}")
    lines.append(f"Recieved F101 应答: {res.f101_received.get('f101', 0)}  busy: {res.busy_counts.get('busy', 0)}")
    if res.profile_blocks:
        total_profile = sum(len(b) for b in res.profile_blocks)
        lines.append(f"建档案帧: {len(res.profile_blocks)} 块, 档案表总数: {total_profile}")
        lines.append(f"档案表: {', '.join(sorted(res.profile_addresses))}")
    lines.append("")
    lines.append("---- 补抄次数分布 ----")
    dist = Counter(res.send_counts.values())
    for k in sorted(dist):
        lines.append(f"  send {k} 次: {dist[k]} 只表")
    lines.append("")
    lines.append("---- send>=3 且从未成功 或 send>=3 的表 ----")
    for addr in sorted(a for a in res.send_counts if res.send_counts[a] >= 3):
        st = "OK" if addr in res.ok_set else "NO-OK"
        times = " | ".join(res.send_times[addr])
        lines.append(f"  {addr}: send={res.send_counts[addr]} 结果={st}")
        lines.append(f"     时间: {times}")
    lines.append("")
    lines.append("---- send 过但从未 Success 的表 ----")
    for addr in res.never_ok:
        lines.append(f"  {addr}: send={res.send_counts[addr]} "
                     f"f101={res.f101_received.get(addr, 0)} 时间={' | '.join(res.send_times[addr])}")
    lines.append("")
    lines.append("---- 最终判定 ----")
    lines.append(res.final_verdict or "(无)")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) < 1:
        print("用法: python 台体/analyze_taish.py <台体日志路径>  (或 python run.py taish <台体日志路径>)")
        return 2
    res = analyze_taish_log(args[0])
    print(format_report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
