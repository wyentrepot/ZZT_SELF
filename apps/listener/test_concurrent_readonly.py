# -*- coding: utf-8 -*-
"""REQS-0030：侦听台只读统计单测（构造 frame_log 行 + 临时库，不发帧）。"""
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "apps"))

from listener.concurrent_readonly import (  # noqa: E402
    _addr_from_hex, _payload, collect_attempts, concurrent_stats)


def row(ts, d, parsed, fid=0):
    return {"id": fid, "ts": ts, "dir": d,
            "parsed": json.dumps(parsed), "frame_hex": ""}


ADDR_HEX = "010203040506"  # 线上字节序


def down_frame(ts, fid, addr_hex=ADDR_HEX):
    return row(ts, "tx", {
        "raw_hex": "68" * 20,
        "nested": [{"fields": {"地址域A": {"hex": addr_hex, "raw": addr_hex}}}]}, fid)


def up_frame(ts, fid, length=4, addr_hex=ADDR_HEX):
    # 头 13 字节：68 L(2) C(1) R(6) AFN DT(2)；应用数据单元 = 规约类型+长度L
    # （长度低字节在前，02 卡 datalen=(buff[3]<<8)+buff[2]）
    payload_hex = "02" + f"{length & 0xFF:02X}{(length >> 8) & 0xFF:02X}"
    return row(ts, "rx", {
        "raw_hex": "68" + "00" * 12 + payload_hex + "EB16",
        "fields": {"地址域A": {"hex": addr_hex, "raw": addr_hex}},
        "nested": []}, fid)


class TestAddr(unittest.TestCase):
    def test_pairwise_reverse(self):
        self.assertEqual(_addr_from_hex("010203040506"), "060504030201")
        self.assertEqual(_addr_from_hex("ABCDEF123456"), "563412EFCDAB")


class TestPairing(unittest.TestCase):
    def test_success_pair(self):
        a = collect_attempts([down_frame("2026-09-13T10:00:00", 1),
                              up_frame("2026-09-13T10:00:05", 2, length=4)])
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["status"], "success")
        self.assertEqual(a[0]["meter"], "060504030201")

    def test_fail_L0(self):
        a = collect_attempts([down_frame("2026-09-13T10:00:00", 1),
                              up_frame("2026-09-13T10:00:05", 2, length=0)])
        self.assertEqual(a[0]["status"], "timeout")

    def test_duplicate_then_lifetime(self):
        a = collect_attempts([
            down_frame("2026-09-13T10:00:00", 1),
            down_frame("2026-09-13T10:00:10", 2),   # 未结清再发 → 旧尝试超时结清
            up_frame("2026-09-13T10:00:20", 3, length=4),
        ])
        statuses = sorted(x["status"] for x in a)
        self.assertEqual(statuses, ["success", "timeout"])

    def test_duplicate_flagged_on_redispatch(self):
        """未结清再下发：新尝试带 duplicate 标记（此前恒漏计的口径缺陷）。"""
        a = collect_attempts([
            down_frame("2026-09-13T10:00:00", 1),
            down_frame("2026-09-13T10:00:10", 2),   # 未结清再发 → 重复下发
            up_frame("2026-09-13T10:00:20", 3, length=4),
        ])
        dups = [x for x in a if x.get("duplicate")]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["status"], "success")
        from datetime import datetime
        self.assertEqual(dups[0]["start_epoch"],
                         datetime.fromisoformat("2026-09-13T10:00:10").timestamp())

    def test_payload_with_address_domain(self):
        """带地址域的真实 1376.2 帧（batch 下发/回装形态）不再切错位。"""
        from sim_concentrator.frame_codec import build_13762_frame, decode_frame
        raw = build_13762_frame(
            afn=0xF1, fn=1, appdata=bytes([0x02, 0x10, 0x00]) + b"DATA",
            direction="up",
            address={"src": "060504030201", "relay": [], "dst": "000000000001"})
        d = decode_frame(raw)
        unit = _payload(d, d["raw_hex"])
        self.assertEqual(unit, "021000" + b"DATA".hex().upper())
        # 无地址域帧同样正确
        raw2 = build_13762_frame(afn=0xF1, fn=1,
                                 appdata=bytes([0x02, 0x10, 0x00]) + b"DATA",
                                 direction="up")
        d2 = decode_frame(raw2)
        self.assertEqual(_payload(d2, d2["raw_hex"]),
                         "021000" + b"DATA".hex().upper())

    def test_lifetime_bottom(self):
        a = collect_attempts([
            down_frame("2026-09-13T10:00:00", 1),
            up_frame("2026-09-13T10:20:00", 2, length=4,
                     addr_hex="FEDCBA987654"),  # 另一表的应答
        ])
        # 10:00 的下发 20 分钟未结清 → 5 分钟生命周期兜底超时
        self.assertTrue(any(x["status"] == "timeout" for x in a))


class TestConcurrentStats(unittest.TestCase):
    def test_end_to_end(self):
        fd, path = tempfile_path()
        try:
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE frame_log (id INTEGER PRIMARY KEY, ts TEXT, "
                        "dir TEXT, afn TEXT, fn TEXT, parsed TEXT, frame_hex TEXT)")
            for i, r in enumerate([
                down_frame("2026-09-13T10:00:00", 1),
                down_frame("2026-09-13T10:00:01", 2, addr_hex="FEDCBA987654"),
                up_frame("2026-09-13T10:00:05", 3, 4),
                up_frame("2026-09-13T10:00:08", 4, 0,
                         addr_hex="FEDCBA987654"),
            ], 1):
                d = dict(r)
                con.execute("INSERT INTO frame_log (id, ts, dir, afn, fn, parsed, "
                            "frame_hex) VALUES (?,?,?,?,?,?,?)",
                            (d["id"], d["ts"], d["dir"], "F1", "F1",
                             d["parsed"], d["frame_hex"]))
            con.commit()
            con.close()
            out = concurrent_stats(path, period="15m")
            self.assertEqual(out["frames_total"], 4)
            b = out["buckets"][0]
            self.assertEqual(b["dispatch_count"], 2)
            self.assertEqual(b["success_count"], 1)
            self.assertEqual(b["failed_count"], 1)
            self.assertEqual(b["max_concurrent"], 2)
            self.assertEqual(b["duplicate_count"], 0)
        finally:
            os.unlink(path)

    def test_end_to_end_duplicate(self):
        """端到端：未结清重发在周期统计中计入 duplicate_count。"""
        fd, path = tempfile_path()
        try:
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE frame_log (id INTEGER PRIMARY KEY, ts TEXT, "
                        "dir TEXT, afn TEXT, fn TEXT, parsed TEXT, frame_hex TEXT)")
            for i, r in enumerate([
                down_frame("2026-09-13T10:00:00", 1),
                down_frame("2026-09-13T10:00:10", 2),   # 未结清再发
                up_frame("2026-09-13T10:00:20", 3, 4),
            ], 1):
                d = dict(r)
                con.execute("INSERT INTO frame_log (id, ts, dir, afn, fn, parsed, "
                            "frame_hex) VALUES (?,?,?,?,?,?,?)",
                            (d["id"], d["ts"], d["dir"], "F1", "F1",
                             d["parsed"], d["frame_hex"]))
            con.commit()
            con.close()
            out = concurrent_stats(path, period="15m")
            b = out["buckets"][0]
            self.assertEqual(b["dispatch_count"], 2)
            self.assertEqual(b["duplicate_count"], 1)
            self.assertEqual(b["success_count"], 1)
        finally:
            os.unlink(path)


def tempfile_path():
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    return fd, path


if __name__ == "__main__":
    unittest.main()
