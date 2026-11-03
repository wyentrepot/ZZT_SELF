# -*- coding: utf-8 -*-
"""REQS-0037：06H-F230 分钟采集数据上报的接收/入库/分类/自动确认 单元测试。

用真实台架帧（2026-11-02 重庆 STA+CCO 台架，只读证据）：
- 698.45 场景 task 1（BENCH-v5-taskid-RECOVERED.md §2，54B 内层）
- 645 场景 task 1 / task 2 缺项（bench-645-regression-20261102-180650，56B 内层）
覆盖：journal.ingest → frame_log + report_mclt_data；report_buckets F230 桶；
responder 对真实 F230 帧回 00H-F1 确认（用户拍板 2026-11-02：F230 为主动上报，
必须确认防 CCO 重复上报，与 F1/F3/F4/F5 同类口径）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "libs"))

from sim_concentrator.aggregate import report_buckets  # noqa: E402
from sim_concentrator.frame_codec import decode_frame  # noqa: E402
from sim_concentrator.journal import FrameJournal  # noqa: E402
from sim_concentrator.responder import Responder  # noqa: E402
from sim_concentrator.sink import ingest_entry  # noqa: E402
from sim_concentrator.store import ListenerStore  # noqa: E402

# 真实 06H-F230 帧（hex 只读自台架证据，勿改）
F230_698_TASK1 = (
    "685500c300004000002006201c01000000000001030010170211263600683400c305"
    "00000000000101cda990002285020103200002010111012001020101110220000200"
    "01ffff00000100046ae8c3e84f77168616"
)
F230_645_TASK1 = (
    "685700c300004000003506201c010000000000010200051802112638006800000000"
    "0001689106333434353455c116680000000000016891073334353567453319166800"
    "0000000001689107333236356587335816b216"
)
F230_645_TASK2_MISSING = (
    "685700c300004000003906201c020000000000010200151802112638006800000000"
    "0001689106333434353455c116680000000000016891073334353567453319166800"
    "000000000168910733343635323232d116b916"
)


def _ingest(store: ListenerStore, frame_hex: str) -> Optional[int]:
    """按 journal.append 的 entry 语义直接 ingest 一帧（不建会话文件）。"""
    raw = bytes.fromhex(frame_hex)
    parsed = decode_frame(raw)
    entry = {
        "seq": 1, "ts": "2026-11-02T18:14:07.169", "ts_epoch": 0.0,
        "dir": "rx", "kind": None, "run_id": None,
        "frame_hex": raw.hex(), "afn": "06", "fn": "F230", "updown": "up",
        "parsed": parsed,
    }
    return ingest_entry(store, entry)


def test_f230_698_ingest_and_classify():
    """698.45 task 1 真实帧：入库 report_mclt_data，task_id/协议/采集时间/长度齐全。"""
    with tempfile.TemporaryDirectory() as td:
        store = ListenerStore(Path(td) / "t.sqlite")
        fid = _ingest(store, F230_698_TASK1)
        assert fid is not None
        rows = store.list_report_mclt_data()
        assert len(rows) == 1
        r = rows[0]
        assert r["frame_id"] == fid
        assert r["task_id"] == "1"
        assert r["proto_type"] == "3"          # DL/T 698.45
        assert r["collect_time"] == "2026-11-02 17:10:00"
        assert r["node_addr"] == "000000000001"
        assert r["data_len"] == 54
        assert r["nested_count"] == 1
        # payload_hex = 内层 698 帧（与 11E3 上行 bufflen 一致）
        assert r["payload_hex"].startswith("683400c3")
        assert len(r["payload_hex"]) == 54 * 2
        payload = json.loads(r["payload_json"])
        assert payload["proto_raw"].startswith("0x03")
        assert payload["nested_structures"] == ["698.45"]
        # 证据链：frame_log 里有原帧
        fr = store.query("SELECT * FROM frame_log WHERE id=?", (fid,))[0]
        assert fr["afn"] == "06" and fr["fn"] == "F230" and fr["updown"] == "up"
        # 不应混入 F1-F5 事件表
        assert store.list_report_events() == []
        store.close()


def test_f230_645_ingest_and_classify():
    """645 task 1 / task 2 缺项 真实帧：协议 2、采集时间、缺项 0x32 内层一致。"""
    with tempfile.TemporaryDirectory() as td:
        store = ListenerStore(Path(td) / "t.sqlite")
        assert _ingest(store, F230_645_TASK1) is not None
        assert _ingest(store, F230_645_TASK2_MISSING) is not None
        rows = store.list_report_mclt_data()
        assert len(rows) == 2
        by_task = {r["task_id"]: r for r in rows}
        t1 = by_task["1"]
        assert t1["proto_type"] == "2"         # DL/T 645-2007
        assert t1["collect_time"] == "2026-11-02 18:05:00"
        assert t1["data_len"] == 56
        assert t1["nested_count"] == 3
        assert t1["payload_hex"].startswith("68000000000001689106")
        t2 = by_task["2"]
        # 缺项 DI：内层第三帧 32 32 32 满长度 0x32（在报文内容里可核对）
        assert t2["collect_time"] == "2026-11-02 18:15:00"
        assert "33343635323232" in t2["payload_hex"]
        assert t2["data_len"] == 56 and t2["nested_count"] == 3
        store.close()


def test_f230_report_buckets():
    """report_buckets：F230 单列桶 + task/协议二级计数，F1-F5 桶不受影响。"""
    with tempfile.TemporaryDirectory() as td:
        store = ListenerStore(Path(td) / "t.sqlite")
        _ingest(store, F230_698_TASK1)
        _ingest(store, F230_645_TASK1)
        _ingest(store, F230_645_TASK2_MISSING)
        out = report_buckets(store)
        b = out["buckets"]["F230"]
        assert b["count"] == 3
        assert b["name"] == "分钟采集数据上报"
        assert b["by_task"] == {"1": 2, "2": 1}
        assert b["by_proto"] == {"698.45": 1, "645-2007": 2}
        item = b["items"][0]
        assert item["afn_fn"] == "06H F230"
        assert item["task_id"] in ("1", "2")
        assert item["proto"] in ("698.45", "645-2007")
        assert item["collect_time"].startswith("2026-11-02")
        # F230 计入上报总数；F1-F5 桶仍为 0
        assert out["total"] == 3
        for k in ("F1", "F2", "F3", "F4", "F5"):
            assert out["buckets"][k]["count"] == 0
        # F5_power 桶不重复计入 total
        assert out["buckets"]["F5_power"]["count"] == 0
        store.close()


def test_f230_responder_acks_real_frames():
    """真实 F230 帧（698/645）命中内置规则 → 回 00H-F1 确认。

    协议依据（用户拍板 2026-11-02）：F230 是主动上报，与 F1/F3/F4/F5 同类，
    必须回确认，否则 CCO 可能重复上报。
    """
    rp = Responder()
    for hx in (F230_698_TASK1, F230_645_TASK1, F230_645_TASK2_MISSING):
        reply = rp.reply_for(bytes.fromhex(hx))
        assert reply is not None, hx[:16]
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x00
        assert d["fields"]["FN"]["raw"] == 1


def test_f230_journal_pipeline_ingests_once_per_frame():
    """journal.append（带 store）→ 自动 ingest：F230 落库一次，F1-F5 路径不回归。"""
    with tempfile.TemporaryDirectory() as td:
        store = ListenerStore(Path(td) / "t.sqlite")
        j = FrameJournal(port="COM_TEST", log_dir=Path(td) / "logs", store=store)
        j.append("rx", bytes.fromhex(F230_698_TASK1))
        j.append("rx", bytes.fromhex(F230_645_TASK1))
        j.close_file()
        assert len(store.list_report_mclt_data()) == 2
        assert len(store.list_report_events()) == 0
        store.close()


def test_f230_report_days_counts_mclt():
    """按天统计包含 F230 桶（report_days.mclt）。"""
    with tempfile.TemporaryDirectory() as td:
        store = ListenerStore(Path(td) / "t.sqlite")
        _ingest(store, F230_698_TASK1)
        days = store.report_days()
        assert len(days) == 1
        assert days[0]["mclt"] == 1
        store.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n== {passed}/{len(fns)} 通过 ==")
