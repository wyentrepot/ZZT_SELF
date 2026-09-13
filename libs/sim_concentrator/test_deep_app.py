# -*- coding: utf-8 -*-
"""REQS-0030：deep_app 深化应用单测（全构帧，不依赖实机日志/串口）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim_concentrator.deep_app import (  # noqa: E402
    ArchiveSession, MAX_CONCURRENT_CAP, _app_payload, _node_from_record,
    precheck_max_concurrent, query_archive, query_online,
)
from sim_concentrator.frame_codec import build_13762_frame  # noqa: E402


def _le(n, size):
    return n.to_bytes(size, "little")


class FakeIO:
    """假串口：记录下发；每次下发后把预置应答追加进 rx_history（模拟模块回帧）。"""

    def __init__(self, replies=()):
        self.sent = []
        self._replies = list(replies)
        self._history = []

    def send_frame(self, raw):
        self.sent.append(raw)
        while self._replies:
            self._history.append(self._replies.pop(0))

    def rx_history(self):
        return list(self._history)


def _f2_reply(nodes):
    """构造 10H-F2 应答帧：total + n 条 (BCD 地址 + 2B 节点信息)。"""
    app = _le(len(nodes), 2) + bytes([len(nodes)])
    for addr_hex, info in nodes:
        app += bytes.fromhex(addr_hex) + _le(info, 2)
    return build_13762_frame(afn=0x10, fn=2, appdata=app, direction="up")


def _f1_reply(total, capacity):
    app = _le(total, 2) + _le(capacity, 2)
    return build_13762_frame(afn=0x10, fn=1, appdata=app, direction="up")


class TestPrecheck(unittest.TestCase):
    def test_range(self):
        self.assertEqual(precheck_max_concurrent(1), 1)
        self.assertEqual(precheck_max_concurrent(20), 20)

    def test_over_cap_denies_109(self):
        with self.assertRaises(ValueError) as ctx:
            precheck_max_concurrent(21)
        self.assertIn("109", str(ctx.exception))
        with self.assertRaises(ValueError):
            precheck_max_concurrent(0)


class TestNodeParse(unittest.TestCase):
    def test_signal_relay_online(self):
        n = _node_from_record({"从节点地址": "129078563412", "从节点信息": {"hex": "3400"}})
        self.assertEqual(n["signal"], 3)   # 0x34 → D7~D4=3
        self.assertEqual(n["relay"], 4)    # D3~D0=0
        self.assertTrue(n["online"])
        n0 = _node_from_record({"从节点地址": "010203040506", "从节点信息": {"hex": "0500"}})
        self.assertFalse(n0["online"])     # 信号品质 0 → 离线

    def test_app_payload_slice(self):
        raw = _f2_reply([("010203040506", 0x0034)])
        self.assertEqual(raw[0], 0x68)
        self.assertEqual(raw[-1], 0x16)


class TestQueryArchive(unittest.TestCase):
    def test_query_and_session(self):
        io = FakeIO(replies=[_f2_reply([
            ("010203040506", 0x0034), ("112233445566", 0x0025)])])
        sess = ArchiveSession()
        r = query_archive(io, session=sess, timeout=1.0)
        self.assertEqual(len(io.sent), 1)
        self.assertEqual(r["nodes"][0]["addr"], "102030405060")  # BCD 反序输出
        self.assertEqual(sess.snapshot()["total"], 2)
        self.assertTrue(sess.snapshot()["nodes"][1]["online"])

    def test_timeout_no_stale(self):
        io = FakeIO()  # 无应答
        sess = ArchiveSession()
        sess.replace([{"addr": "old"}])
        with self.assertRaises(TimeoutError):
            query_archive(io, timeout=0.2, session=sess)
        # 旧数据不被顶替，但也不返回：下一次成功才全量重建
        self.assertEqual(sess.snapshot()["nodes"], [{"addr": "old"}])

    def test_excel_export(self):
        import tempfile, os
        io = FakeIO(replies=[_f2_reply([("010203040506", 0x0034)])])
        sess = ArchiveSession()
        query_archive(io, session=sess, timeout=1.0)
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            sess.export_excel(path)
            from openpyxl import load_workbook
            ws = load_workbook(path).active
            self.assertEqual(ws.cell(row=2, column=2).value, "102030405060")
            self.assertEqual(ws.cell(row=2, column=5).value, "在线")
        finally:
            os.unlink(path)


class TestQueryOnline(unittest.TestCase):
    def test_total_capacity(self):
        io = FakeIO(replies=[_f1_reply(120, 1152)])
        r = query_online(io, timeout=1.0)
        self.assertEqual(r["total"], 120)
        self.assertEqual(r["capacity"], 1152)


if __name__ == "__main__":
    unittest.main()
