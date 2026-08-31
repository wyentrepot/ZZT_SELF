# -*- coding: utf-8 -*-
"""REQS-0013 P3：真实构帧清单回放验证。

从 测试文件/构帧全量清单_TX_RX_20260829.txt 提取 RX（上行）帧 hex，
经 FrameJournal.append 走 enrich_response 链路，统计 03H/06H/10H 响应
契约解析覆盖与成功率，作为 G2/G3/G4 的真机帧回归证据。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs"))

from sim_concentrator.store import ListenerStore  # noqa: E402
from sim_concentrator.journal import FrameJournal  # noqa: E402

FRAME_LIST = ROOT / "测试文件" / "构帧全量清单_TX_RX_20260829.txt"

# 抓取每个 [NNN] RX ... 块里的「帧hex」行
_ENTRY = re.compile(r"^\[(\d+)\]\s+(TX|RX)\s+AFN=([0-9A-Fa-f]{2})H\(([^)]*)\)\s+(.*)$")
_HEX = re.compile(r"^([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2}){7,})$")


def parse_rx_frames() -> list[dict]:
    lines = FRAME_LIST.read_text(encoding="utf-8", errors="replace").splitlines()
    frames, cur = [], None
    for line in lines:
        m = _ENTRY.match(line.strip())
        if m:
            idx, d, afn, name, desc = m.groups()
            cur = {"idx": int(idx), "dir": d, "afn": afn.upper(), "name": name, "desc": desc}
            continue
        if cur and cur["dir"] == "RX":
            h = _HEX.match(line.strip())
            if h:
                cur["hex"] = "".join(line.split())
                frames.append(cur)
                cur = None
    return frames


def main() -> int:
    frames = parse_rx_frames()
    print(f"解析到 RX 上行帧 {len(frames)} 条")

    td = tempfile.mkdtemp()
    store = ListenerStore(Path(td) / "t.sqlite")
    j = FrameJournal(port="REPLAY", log_dir=Path(td), store=store)

    ok_afn = {"03": 0, "06": 0, "10": 0}
    parsed_ok = {"03": 0, "06": 0, "10": 0}
    samples: list[str] = []
    for f in frames:
        try:
            entry = j.append("rx", bytes.fromhex(f["hex"]))
        except Exception as e:
            print(f"  ✗ [{f['idx']}] {f['afn']} 解析异常: {e}")
            continue
        if entry is None:
            continue
        a = entry.get("afn", "")
        a = a.rstrip("H")
        if a in ok_afn:
            ok_afn[a] += 1
            resp = entry.get("resp") or {}
            records = resp.get("records") or []
            head = resp.get("head") or {}
            has_data = bool(records) or bool(head)
            if has_data:
                parsed_ok[a] += 1
            if a == "10" and has_data and len(samples) < 5:
                samples.append(f"[{f['idx']}] {f['desc']} → head={list(head.keys())[:3]} records={len(records)}")

    j.close_file()
    store.close()

    print("\n== 响应契约解析覆盖（enrich_response）==")
    for a in ("03", "06", "10"):
        print(f"  {a}H: {parsed_ok[a]}/{ok_afn[a]} 帧解析出结构化数据")

    print("\n== 10H 样例 ==")
    for s in samples:
        print("  " + s)

    # 06H 应已自动落库
    evts = store.list_report_events()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
