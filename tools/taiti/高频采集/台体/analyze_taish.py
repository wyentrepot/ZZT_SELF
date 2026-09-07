# -*- coding: utf-8 -*-
"""高频采集台体日志分析器（复用项目 parser_lib / sim_concentrator.frame_codec）。

输入：台体高频采集日志（GBK 编码）。

分析内容：
- 采集帧目标表地址、发送时间与每表发送次数；
- ReadMeter 成功/失败、并发满（109）与电表忙（111）；
- 两阶段采集下按基线计算的额外补发次数；
- cycle 推进、全局无新上报等待，以及未入网 MAC 与未成功读表地址关联；
- 建档案帧中的档案表列表和最终判定。

注意：台体日志没有将 F101 回应与发送请求关联的事务 ID，F101 仅做全局计数，
不会被错误归属给“最近发送”的表地址。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Optional

_REPO = Path(__file__).resolve().parents[4]  # tools/taiti/高频采集/台体 -> 仓库根
for _p in (str(_REPO / "libs"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 采集帧：630198900000 + 6B 地址 + F101。
READ_FRAME_RE = re.compile(r"630198900000([0-9A-Fa-f]{12})F101")
READ_METER_RE = re.compile(r"ReadMeter\s+(Success|Fail),\s*mac\s+addr:'([0-9A-Fa-f]{12})'")
RECEIVED_F101_RE = re.compile(r"Recieved\s+F101", re.I)
MAXIMUM_ALLOWABLE_RE = re.compile(
    r"More than maximum allowable number of 376\.2\s*\(109\)", re.I)
METER_BUSY_RE = re.compile(r"Meter reading busy\s*\(111\)", re.I)
BUSY_RE = re.compile(r"Meter reading busy|maximum allowable", re.I)
PROFILE_RE = re.compile(r"011101000?([0-9A-Fa-f]+)")
SUCCESS_RATE_RE = re.compile(
    r"successRate\s*=\s*([0-9.]+),\s*NeedsuccessRate\s*=\s*([0-9.]+)")
CYCLE_RE = re.compile(
    r"restart timer ti_wait_new_report\.+,\s*cycle\s*(?:(\.\.)\s*)?:\s*(\d+)", re.I)
READ_CYCLE_MAX_RE = re.compile(r"read cycle reach max\((\d+)\)", re.I)
MIDDLE_DURATION_RE = re.compile(r"Total read time in middle\s*:\s*([0-9.]+)", re.I)
FIRST_DURATION_RE = re.compile(r"First duration\s*:\s*([0-9.]+)", re.I)
SECOND_DURATION_RE = re.compile(r"Second duration\s*:\s*([0-9.]+)", re.I)
HIGH_FREQUENCY_RESULT_RE = re.compile(
    r"High Frequence RM\s+(pass|fail),\s*time consumed\s*:\s*([0-9.]+)", re.I)
NOT_IN_NET_RE = re.compile(r"not in net mac\s*\{([^}]*)\}", re.I)
MAC_BYTE_RE = re.compile(r"'([0-9A-Fa-f]{2})'O")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S:%f"


def norm_addr(addr: str) -> str:
    """将日志中的 6 字节 MAC 地址规范为 12 位大写十六进制。"""
    return addr.upper()


def _parse_timestamp(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, _TIMESTAMP_FORMAT)
    except ValueError:
        return None


@dataclass
class ReadEvent:
    seq: Optional[int]
    addr: str
    ts: str
    result: str  # SEND | OK | FAIL


@dataclass(frozen=True)
class CycleEvent:
    ts: str
    cycle: int
    kind: str  # RESET | ADVANCE


@dataclass(frozen=True)
class IdleWait:
    report_ts: str
    total_ts: str
    seconds: float


@dataclass
class TaishAnalysis:
    log_path: Path
    send_counts: Counter = field(default_factory=Counter)
    send_times: dict[str, list[str]] = field(default_factory=dict)
    ok_set: set[str] = field(default_factory=set)
    fail_counts: Counter = field(default_factory=Counter)
    busy_counts: Counter = field(default_factory=Counter)
    busy_code_counts: Counter = field(default_factory=Counter)
    f101_received: int = 0
    events: list[ReadEvent] = field(default_factory=list)
    success_rates: list[tuple[str, float, float]] = field(default_factory=list)
    topology_missing_addrs: set[str] = field(default_factory=set)
    cycle_events: list[CycleEvent] = field(default_factory=list)
    cycle_max_values: list[int] = field(default_factory=list)
    idle_waits: list[IdleWait] = field(default_factory=list)
    middle_durations: list[float] = field(default_factory=list)
    first_duration: Optional[float] = None
    second_duration: Optional[float] = None
    total_duration: Optional[float] = None
    result_summary: str = ""
    profile_addresses: set[str] = field(default_factory=set)
    profile_blocks: list[list[str]] = field(default_factory=list)
    final_verdict: str = ""

    @property
    def never_ok(self) -> list[str]:
        return sorted(addr for addr in self.send_counts if addr not in self.ok_set)

    @property
    def over_retry(self) -> list[str]:
        """兼容原规则：发送超过 3 次且最终从未成功的失败候选。"""
        return sorted(
            addr for addr in self.send_counts
            if self.send_counts[addr] > 3 and addr not in self.ok_set
        )

    @property
    def phase_count(self) -> int:
        """已识别两段时长时按两阶段采集，否则退化为单阶段。"""
        return 2 if self.first_duration is not None and self.second_duration is not None else 1

    @property
    def extra_resend_counts(self) -> Counter:
        """相对阶段基线的额外发送次数；不是每一阶段的单独重试次数。"""
        return Counter({
            addr: max(count - self.phase_count, 0)
            for addr, count in self.send_counts.items()
        })

    @property
    def extra_resend_total(self) -> int:
        return sum(self.extra_resend_counts.values())

    @property
    def retried_addresses(self) -> list[str]:
        return sorted(
            addr for addr, count in self.extra_resend_counts.items() if count > 0
        )

    @property
    def last_success_rate(self) -> Optional[tuple[str, float, float]]:
        return self.success_rates[-1] if self.success_rates else None

    @property
    def topology_never_ok(self) -> list[str]:
        return sorted(self.topology_missing_addrs.intersection(self.never_ok))


def _topology_addresses(line: str) -> list[str]:
    """从 not in net mac 的小端字节列表恢复显示地址。"""
    m = NOT_IN_NET_RE.search(line)
    if not m:
        return []
    values = MAC_BYTE_RE.findall(m.group(1))
    return [
        "".join(reversed(values[pos:pos + 6])).upper()
        for pos in range(0, len(values) - 5, 6)
    ]


def analyze_taish_log(path: str | Path) -> TaishAnalysis:
    """解析台体高频采集日志。"""
    p = Path(path)
    data = p.read_bytes().decode("gbk", errors="replace")
    res = TaishAnalysis(log_path=p)
    last_report_ts: Optional[tuple[str, datetime]] = None

    for line in data.splitlines():
        ts = line[:23] if len(line) >= 23 else ""
        parsed_ts = _parse_timestamp(ts)

        m = SUCCESS_RATE_RE.search(line)
        if m:
            res.success_rates.append((ts, float(m.group(1)), float(m.group(2))))

        topology_addrs = _topology_addresses(line)
        if topology_addrs:
            res.topology_missing_addrs = set(topology_addrs)

        m = CYCLE_RE.search(line)
        if m:
            res.cycle_events.append(CycleEvent(
                ts=ts,
                cycle=int(m.group(2)),
                kind="ADVANCE" if m.group(1) else "RESET",
            ))

        m = READ_CYCLE_MAX_RE.search(line)
        if m:
            res.cycle_max_values.append(int(m.group(1)))

        m = HIGH_FREQUENCY_RESULT_RE.search(line)
        if m:
            res.result_summary = (
                f"High Frequence RM {m.group(1).lower()}, time consumed: {m.group(2)}"
            )
            res.total_duration = float(m.group(2))
            res.final_verdict = line.strip()
        elif "read fail" in line or "执行结果" in line:
            res.final_verdict = line.strip()

        if "send cmd" in line:
            fm = READ_FRAME_RE.search(line)
            if fm:
                addr = norm_addr(fm.group(1))
                hm = re.search(r"'([0-9A-Fa-f]+)'", line)
                seq = None
                if hm:
                    try:
                        raw = bytes.fromhex(hm.group(1))
                        if len(raw) > 9:
                            seq = raw[9]
                    except ValueError:
                        pass
                res.send_counts[addr] += 1
                res.send_times.setdefault(addr, []).append(ts)
                res.events.append(ReadEvent(seq, addr, ts, "SEND"))
            elif "01110100" in line:
                hm = re.search(r"'([0-9A-Fa-f]+)'", line)
                if hm:
                    block = parse_profile_block(hm.group(1))
                    if block:
                        res.profile_blocks.append(block)
                        res.profile_addresses.update(block)

        m = READ_METER_RE.search(line)
        if m:
            status, addr = m.group(1), norm_addr(m.group(2))
            if status == "Success":
                res.ok_set.add(addr)
            else:
                res.fail_counts[addr] += 1
            res.events.append(ReadEvent(None, addr, ts, status))
            if parsed_ts is not None:
                last_report_ts = (ts, parsed_ts)

        if RECEIVED_F101_RE.search(line):
            res.f101_received += 1
            if parsed_ts is not None:
                last_report_ts = (ts, parsed_ts)

        if MAXIMUM_ALLOWABLE_RE.search(line):
            res.busy_counts["busy"] += 1
            res.busy_code_counts["109"] += 1
        elif METER_BUSY_RE.search(line):
            res.busy_counts["busy"] += 1
            res.busy_code_counts["111"] += 1
        elif BUSY_RE.search(line):
            res.busy_counts["busy"] += 1
            res.busy_code_counts["other"] += 1

        m = MIDDLE_DURATION_RE.search(line)
        if m:
            res.middle_durations.append(float(m.group(1)))
            if last_report_ts is not None and parsed_ts is not None:
                delta = (parsed_ts - last_report_ts[1]).total_seconds()
                if delta >= 0:
                    res.idle_waits.append(IdleWait(last_report_ts[0], ts, delta))

        m = FIRST_DURATION_RE.search(line)
        if m:
            res.first_duration = float(m.group(1))
        m = SECOND_DURATION_RE.search(line)
        if m:
            res.second_duration = float(m.group(1))

    return res


def parse_profile_block(hex_str: str) -> Optional[list[str]]:
    """解析建档案帧，返回其中 DL/T 698.45 档案表地址。"""
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return None
    if len(raw) < 16:
        return None
    try:
        if raw[10] == 0x11 and raw[11] == 0x01 and raw[12] == 0x00:
            count = raw[13]
            addresses = []
            pos = 14
            for _ in range(count):
                if pos + 7 > len(raw):
                    break
                addr = raw[pos:pos + 6].hex().upper()
                protocol = raw[pos + 6]
                if protocol == 0x03:  # DL/T 698.45
                    addresses.append(addr)
                pos += 7
            return addresses
    except IndexError:
        return None
    return None


def _format_extra_distribution(res: TaishAnalysis) -> list[str]:
    dist = Counter(res.extra_resend_counts.values())
    return [
        f"  额外补发 {count} 次: {tables} 只表"
        for count, tables in sorted(dist.items())
    ]


def format_report(res: TaishAnalysis) -> str:
    """将结构化分析结果输出为可审阅的文本报告。"""
    lines: list[str] = [
        "=" * 70,
        "  台体高频采集日志分析",
        "=" * 70,
        f"日志: {res.log_path}",
        f"组网成功率节点: {len(res.success_rates)} 次",
    ]
    for ts, current, needed in res.success_rates[:5]:
        lines.append(f"  {ts} successRate={current:.3f} needs={needed:.3f}")

    final_rate = res.last_success_rate
    if final_rate:
        lines.append(
            f"最终组网成功率: {final_rate[1]:.6f} (门限 {final_rate[2]:.6f})"
        )
    if res.topology_missing_addrs:
        lines.append(
            "最终未入网 MAC: " + ", ".join(sorted(res.topology_missing_addrs))
        )
        lines.append(
            "未入网且无 Success: " +
            (", ".join(res.topology_never_ok) or "(无)")
        )

    lines.extend([
        "",
        f"send 采集帧总数: {sum(res.send_counts.values())}  涉及表: {len(res.send_counts)}",
        f"ReadMeter Success 表: {len(res.ok_set)}  ReadMeter Fail: {sum(res.fail_counts.values())}",
        f"Recieved F101 应答（仅全局计数）: {res.f101_received}",
        "并发/忙提示: "
        f"109={res.busy_code_counts.get('109', 0)} "
        f"111={res.busy_code_counts.get('111', 0)} "
        f"其他={res.busy_code_counts.get('other', 0)} "
        f"合计={res.busy_counts.get('busy', 0)}",
        "",
        "---- 补抄次数分布（总发送）----",
    ])
    for count, tables in sorted(Counter(res.send_counts.values()).items()):
        lines.append(f"  send {count} 次: {tables} 只表")

    lines.extend([
        f"阶段基线: {res.phase_count}（"
        + ("已识别 First/Second duration" if res.phase_count == 2 else "未识别双阶段时长")
        + "）",
        f"相对阶段基线的额外补发: {res.extra_resend_total} 次，"
        f"涉及 {len(res.retried_addresses)} 只表",
        "说明：额外补发是总发送次数减去每表每阶段 1 次基线；"
        "日志无事务 ID，不能断言每次补发都收到逐条回应。",
    ])
    lines.extend(_format_extra_distribution(res))

    lines.extend(["", "---- cycle 与全局等待 ----"])
    if res.cycle_events:
        advances = sum(event.kind == "ADVANCE" for event in res.cycle_events)
        resets = len(res.cycle_events) - advances
        lines.append(f"cycle 事件: RESET={resets}，ADVANCE={advances}")
    if res.cycle_max_values:
        lines.append(
            "读表 cycle 上限: " + ", ".join(map(str, res.cycle_max_values))
        )
    if res.idle_waits:
        waits = [wait.seconds for wait in res.idle_waits]
        lines.append(
            f"最后上报至阶段结算等待: {min(waits):.3f}~{max(waits):.3f} 秒"
            f"（中位数 {median(waits):.3f} 秒）"
        )
        for wait in res.idle_waits[-3:]:
            lines.append(
                f"  {wait.report_ts} -> {wait.total_ts}: {wait.seconds:.3f} 秒"
            )
    else:
        lines.append("最后上报至阶段结算等待: (日志未形成可计算样本)")

    if res.first_duration is not None:
        lines.append(f"First duration: {res.first_duration:.6f}")
    if res.second_duration is not None:
        lines.append(f"Second duration: {res.second_duration:.6f}")
    if res.total_duration is not None:
        lines.append(f"总耗时: {res.total_duration:.6f}")

    if res.profile_blocks:
        total_profile = sum(len(block) for block in res.profile_blocks)
        lines.append(f"建档案帧: {len(res.profile_blocks)} 块, 档案表总数: {total_profile}")
        lines.append(f"档案表: {', '.join(sorted(res.profile_addresses))}")

    lines.extend(["", "---- send>=3 的表 ----"])
    for addr in sorted(addr for addr in res.send_counts if res.send_counts[addr] >= 3):
        status = "OK" if addr in res.ok_set else "NO-OK"
        lines.append(f"  {addr}: send={res.send_counts[addr]} 结果={status}")
        lines.append(f"     时间: {' | '.join(res.send_times[addr])}")

    lines.extend(["", "---- send 过但从未 Success 的表 ----"])
    for addr in res.never_ok:
        lines.append(
            f"  {addr}: send={res.send_counts[addr]} "
            f"时间={' | '.join(res.send_times[addr])}"
        )

    lines.extend(["", "---- 最终判定 ----", res.result_summary or res.final_verdict or "(无)"])
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print("用法: python 台体/analyze_taish.py <台体日志路径>  (或 python run.py taish <台体日志路径>)")
        return 2
    print(format_report(analyze_taish_log(args[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
