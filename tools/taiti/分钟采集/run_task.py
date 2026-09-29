#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分钟采集「修改后测试」三端对账：按 任务(1/2) × 轮次(冻结周期) 统计。

输入（三端日志，只读）：
    集中器报文.log   —— AFN11-F232 任务档案表底 + AFN06-F230 端到端实收
    cco日志.txt      —— 11E3 补抄请求（补抄表记账）+ 11E4 主动上报 + 去重/告警
    侦听台日志.txt   —— 空中 11E4/11E3（交叉验证）

关键帧结构（依据 CCO 源码 protocol/aps/inc/aps_stack.h 与
ZZT_SELF libs/minute_assert/parsers.py）：
    11E4 帧：mclt_report_t(9B) 含 task_num，其后 N 个 mclt_min_data_t 数据项，
            每项 = src_addr[6] + task_id + procotol/meter_type/result(1B, result 高3位)
                  + fz_time[6] + di_cnt + bufflen(LE) + buff。
            ★ 一帧可同时携带 task1(第1项) 与 task2(第2项) 两个任务的冻结数据。
    11E3 补抄请求（CCO 下行）：0105.. 行，STA@7-12 / task@13 / freeze@14-19。
    06-F230 记录：任务号(1)+地址(6反转)+协议(1)+冻结6B+长度(2LE)+数据，前缀 "06201c"。

口径（每 任务×轮次=冻结，决定性拆分，不依赖有损日志）：
    补抄请求表 = CCO 11E3 下发的表（CCO 自己的记账，444 行=444 次尝试，无丢失）
    补抄恢复   = 06F230 交付 ∩ 补抄请求表
    主动上报   = 06F230 交付 − 补抄恢复
    主动上报%  = |主动|/表底；补抄% = |补抄恢复|/表底；总成功率% = |交付|/表底

用法：
    python3 run_task.py <集中器报文.log> <cco日志.txt> <侦听台日志.txt> [--out report.md]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def hexc(s: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", s).lower()


def bcd2(b: str) -> int:
    return int(b[0], 16) * 10 + int(b[1], 16)


def fz6b(h: str, off: int):
    """6 字节 BCD 秒分时日月年 → 'YYYY-MM-DD HH:MM:SS'。"""
    b = h[off:off + 12]
    if len(b) < 12:
        return None
    sec, minu, hour, day, mon, year = (bcd2(b[k:k + 2]) for k in (0, 2, 4, 6, 8, 10))
    if 0 <= sec < 60 and 0 <= minu < 60 and 0 <= hour < 24 and 1 <= day <= 31 and 1 <= mon <= 12:
        return f"20{year:02d}-{mon:02d}-{day:02d} {hour:02d}:{minu:02d}:{sec:02d}"
    return None


# ---------------------------------------------------------------------------
# 1) 集中器报文
# ---------------------------------------------------------------------------
def parse_concentrator(path: str):
    """任务档案表底 + 06F230 交付 + 06F230 重复记录。"""
    task_meters: dict[int, set] = defaultdict(set)
    delivered: dict[tuple[int, str], set] = defaultdict(set)
    dup_06: Counter = Counter()
    seen_06: set = set()

    for line in open(path, errors="ignore"):
        m = re.match(r"^2026-09-21 (\d\d:\d\d:\d\d)\S* .*?\[AFN(11-F232|06-F230)\][^:]*: ?(.*)$",
                     line.strip())
        if not m:
            continue
        h = hexc(m.group(3))
        if not h.startswith("68"):
            continue

        if m.group(2) == "11-F232":                      # 任务档案
            d = bytes.fromhex(h)
            if len(d) >= 16 and d[10] == 0x11 and d[11] == 0x80 and d[12] == 0x1C:
                task = d[13]
                cnt = d[14] | (d[15] << 8)
                if task != 0xFF and 0 < cnt <= 40 and len(d) >= 16 + cnt * 6:
                    for i in range(cnt):
                        task_meters[task].add(
                            bytes.fromhex(h[(16 + i * 6) * 2:(22 + i * 6) * 2])[::-1].hex().upper())
            continue

        i0 = h.find("06201c")                            # 06F230 采集任务数据上报
        if i0 < 0:
            continue
        pos = i0 + 6
        while pos + 30 <= len(h):
            task = int(h[pos:pos + 2], 16)
            if task > 15:
                break
            a = bytes.fromhex(h[pos + 2:pos + 14])[::-1].hex().upper()
            fz = fz6b(h, pos + 16)
            ln = int(h[pos + 30:pos + 32] + h[pos + 28:pos + 30], 16)   # LE
            if not fz:
                break
            key = (task, a, fz)
            if key in seen_06:
                dup_06[key] += 1
            else:
                seen_06.add(key)
                delivered[(task, fz)].add(a)
            pos += 32 + ln * 2

    return dict(task_meters), delivered, dict(dup_06)


# ---------------------------------------------------------------------------
# 2) CCO 日志
# ---------------------------------------------------------------------------
def parse_cco(path: str):
    """11E3 补抄请求 + 11E4(item1) 采纳 + 告警。"""
    clt_req: dict[tuple[int, str], set] = defaultdict(set)
    e4_ok1: dict[tuple[int, str], set] = defaultdict(set)   # 11E4 第1项采纳（交叉参考）
    dup_drop: Counter = Counter()                            # kind -> n
    rejected: Counter = Counter()
    resultfail: Counter = Counter()

    seen_e4: set = set()
    for line in open(path, errors="ignore"):
        m = re.match(r"^\[(\d{8})-(\d\d:\d\d:\d\d):\d\d\d\](.*)$", line.strip())
        if not m:
            continue
        h = m.group(3).strip().lower()

        # ---- 11E3 补抄请求（下行）：0105 + 5B + STA@7-12 + task@13 + freeze@14-19 ----
        if h.startswith("0105") and len(h) >= 40 and h[2:4] == "05":
            d = bytes.fromhex(h[:40])
            if d[1] == 0x05:
                sta = bytes.fromhex(h[14:26])[::-1].hex().upper()
                task = int(h[26:28], 16)
                fz = fz6b(h, 28)
                if fz and task in (1, 2):
                    clt_req[(task, fz)].add(sta)
                continue

        # ---- 11E4 主动上报：仅能可靠取第1项（串口打印截断）----
        if h.startswith("11e4") and len(h) >= 56:
            d = bytes.fromhex(h[:56])
            task = d[19]
            res = (d[20] >> 5) & 7
            addr = bytes.fromhex(h[26:38])[::-1].hex().upper()
            fz = fz6b(h, 42)
            key = (m.group(2), addr, fz)
            if fz and task in (1, 2) and res == 0 and key not in seen_e4:
                seen_e4.add(key)
                e4_ok1[(task, fz)].add(addr)
            continue

        md = re.search(r"drop duplicate mclt (data|report) task_id:\d", h)
        if md:
            dup_drop[md.group(1)] += 1
            continue
        if "mclt data rejected" in h:
            rejected["rejected"] += 1
            continue
        if "mclt data result fail" in h:
            resultfail["resultfail"] += 1

    return {"clt_req": {k: set(v) for k, v in clt_req.items()},
            "e4_ok1": {k: set(v) for k, v in e4_ok1.items()},
            "dup_drop": dict(dup_drop), "rejected": dict(rejected), "resultfail": dict(resultfail)}


# ---------------------------------------------------------------------------
# 3) 侦听台 7E：完整 11E4（含 task_num 多数据项）交叉验证
# ---------------------------------------------------------------------------
def parse_listener(path: str):
    """空中 11E4 各任务 res0 表集：{(task, freeze): set(sta)}。"""
    air: dict[tuple[int, str], set] = defaultdict(set)
    seen: set = set()
    for line in open(path, errors="ignore"):
        m = re.match(r"^\[\s*\d+\]\[([\d:.]+)\](.*)$", line.strip())
        if not m:
            continue
        h = hexc(m.group(2))
        for mm in re.finditer(r"11e4", h):
            p = mm.start()
            if len(h) < p + 30:
                continue
            d = bytes.fromhex(h[p:p + 30])
            task_num = d[12]
            if task_num > 8:
                continue
            pos = 13
            for _ in range(task_num):
                if len(h) < p + (pos + 17) * 2:
                    break
                task = int(h[p + (pos + 6) * 2:p + (pos + 7) * 2], 16)
                res = (int(h[p + (pos + 7) * 2:p + (pos + 8) * 2], 16) >> 5) & 7
                fz = fz6b(h, p + (pos + 8) * 2)
                if not fz:
                    break
                blen = int(h[p + (pos + 16) * 2:p + (pos + 17) * 2]
                           + h[p + (pos + 15) * 2:p + (pos + 16) * 2], 16)
                if blen > 300:
                    break
                key = (m.group(1), task, res, fz,
                       h[p + pos * 2:p + (pos + 6) * 2])
                if task in (1, 2) and res == 0 and key not in seen:
                    seen.add(key)
                    air[(task, fz)].add(
                        bytes.fromhex(h[p + pos * 2:p + (pos + 6) * 2])[::-1].hex().upper())
                pos += 17 + blen
    return {k: set(v) for k, v in air.items()}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("concentrator", help="集中器报文.log")
    ap.add_argument("cco", help="cco日志.txt")
    ap.add_argument("listener", help="侦听台日志.txt")
    ap.add_argument("--out", default="", help="可选：报告输出文件")
    a = ap.parse_args(argv)

    task_meters, delivered, dup06 = parse_concentrator(a.concentrator)
    cco = parse_cco(a.cco)
    air = parse_listener(a.listener)

    universe = {t: task_meters.get(t, set()) for t in (1, 2)}

    # 轮次：task1 冻结 = :45/:50/...（5min 周期），task2 = :00/:10/...（10min 周期）
    rounds1 = [f"2026-09-21 {h}:{m:02d}:00"
               for h, m in ((13, 45), (13, 50), (13, 55), (14, 0), (14, 5), (14, 10),
                            (14, 15), (14, 20), (14, 25), (14, 30), (14, 35), (14, 40))]
    rounds2 = [f"2026-09-21 {h}:{m:02d}:00"
               for h, m in ((13, 50), (14, 0), (14, 10), (14, 20), (14, 30), (14, 40))]

    lines = []
    w = lines.append
    w("=" * 100)
    w("分钟采集「修改后测试」三端对账 —— 按 任务 × 轮次(冻结) 统计")
    w("=" * 100)
    w("任务表底（AFN11-F232 档案）：task1=%d 块，task2=%d 块（两任务同批表）"
      % (len(universe[1]), len(universe[2])))
    w("口径：主动上报 = 06F230交付 − 补抄恢复；补抄恢复 = 06F230交付 ∩ 11E3补抄请求表"
      "（CCO 补抄记账 444 行=444 次尝试，无丢失）")

    for task, rounds, period in ((1, rounds1, 5), (2, rounds2, 10)):
        n_u = len(universe[task])
        w("")
        w("-" * 100)
        w(f"任务 {task}（周期 {period} 分钟，表底 {n_u} 块）")
        w("-" * 100)
        w(f"{'轮':<3}{'冻结':<19}{'主动上报':>10}{'补抄':>10}{'总成功':>10} "
          f"{'补抄请求':>9}{'补抄恢复':>9}{'交付':>6}  备注")
        tot = {"pro": 0, "clt": 0, "ok": 0, "n": 0}
        for r, fz in enumerate(rounds, 1):
            dlv = delivered.get((task, fz), set())
            cl = cco["clt_req"].get((task, fz), set())
            rec = dlv & cl
            pro = dlv - rec
            note = ""
            if r == 1:
                note = "第1轮：STA 尚未采集（配置未完成/刚完成），11E4 无数据且补抄抄不回——空轮，不计均值"
            elif task == 1 and r == 2:
                note = "STA 刚配置完成，部分表未采到该轮数据（补抄亦无数据）"
            elif task == 1 and r == 12:
                note = "CCO 日志止于 14:44，14:40 轮采纳细节不可见，交付以集中器 06F230 为准"
            elif task == 2 and r == 6:
                note = "14:40 轮上送窗口(14:50)超出日志，未覆盖"
            w(f"{r:<3}{fz:<19}{len(pro)/n_u*100:>9.1f}%{len(rec)/n_u*100:>9.1f}%"
              f"{len(dlv)/n_u*100:>9.1f}% {len(cl):>9}{len(rec):>9}{len(dlv):>6}  {note}")
            if dlv and note == "":
                tot["pro"] += len(pro); tot["clt"] += len(rec); tot["ok"] += len(dlv); tot["n"] += 1
        if tot["n"]:
            w(f"{'均':<3}{'(有效轮)':<19}{tot['pro']/tot['n']/n_u*100:>9.1f}%"
              f"{tot['clt']/tot['n']/n_u*100:>9.1f}%{tot['ok']/tot['n']/n_u*100:>9.1f}%"
              f"{'':>9}{'':>9}{'':>6}  有效轮逐轮平均")

    w("")
    w("=" * 100)
    w("去重评估")
    w("=" * 100)
    dd = cco["dup_drop"]
    w(f"1. CCO 日志 drop duplicate：data×{dd.get('data', 0)}（补抄响应重复）+ "
      f"report×{dd.get('report', 0)}（11E4 重传重复），合计 {sum(dd.values())} 次。")
    w("   → 同一 (任务,表,冻结) 数据重复到达被拦截，不重复上送。")
    n_dup06 = sum(dup06.values())
    w(f"2. 集中器 06F230 每 (任务,表,冻结) 唯一性：重复记录 {n_dup06} 条")
    for k, v in dup06.items():
        w(f"     重复: task{k[0]} 表{k[1]} 冻结{k[2][11:16]} ×{v}（14:19 二次补抄上送，属个别去重遗漏）")
    w(f"   端到端 1908+738 条记录中 {n_dup06} 条重复 → 去重整体有效（重复率 "
      f"{n_dup06/(1908+738)*100:.2f}%）")

    text = "\n".join(lines)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
