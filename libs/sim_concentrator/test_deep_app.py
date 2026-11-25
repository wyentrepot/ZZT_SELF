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
        self.assertEqual(n["protocol"], 0)  # D13~D11 = 0（透明）
        self.assertEqual(n["phase"], 0)     # D10~D8 = 0
        self.assertTrue(n["online"])
        n0 = _node_from_record({"从节点地址": "010203040506", "从节点信息": {"hex": "0500"}})
        self.assertFalse(n0["online"])     # 信号品质 0 → 离线

    def test_protocol_phase(self):
        # E3 真帧黄金（REQS-0043 §E3 / CASE-BACKLOG §G-7，位段 D13~D11）：
        # 0x180F 线上小端 hex="0f18"（698 建档，run 20261125-082646-8cff48 s3c）
        # → 协议类型 3（698.45，与 11H-F1 实发 0x03 闭环）/ 相位 0 / 信号 0 / 中继 0xF
        n = _node_from_record({"从节点地址": "129078563412", "从节点信息": {"hex": "0f18"}})
        self.assertEqual(n["protocol"], 3)
        self.assertEqual(n["phase"], 0)
        self.assertEqual(n["signal"], 0)
        self.assertEqual(n["relay"], 0xF)
        self.assertFalse(n["online"])
        # 0x50F1 线上小端 hex="f150"（645 日常态真帧词，CASE-BACKLOG §G-7）
        # → 协议类型 2（645-2007，与 645 建档一致）
        n645 = _node_from_record({"从节点地址": "010203040506", "从节点信息": {"hex": "f150"}})
        self.assertEqual(n645["protocol"], 2)
        self.assertEqual(n645["phase"], 0)
        self.assertEqual(n645["signal"], 0xF)
        self.assertTrue(n645["online"])
        # 合成样本：0x1334 → 协议 2（645-2007）/ 相位 3 / 信号 3 / 中继 4
        n2 = _node_from_record({"从节点地址": "010203040506", "从节点信息": {"hex": "3413"}})
        self.assertEqual(n2["protocol"], 2)
        self.assertEqual(n2["phase"], 3)
        self.assertEqual(n2["signal"], 3)
        self.assertEqual(n2["relay"], 4)
        # 原有关键键不缺失
        for key in ("addr", "node_info_hex", "signal", "relay", "online",
                    "protocol", "phase"):
            self.assertIn(key, n2)

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
        # 线上 01 02 03 04 05 06 按协议低字节在前还原 → 060504030201
        # （2026-11-19 真机：档案 880000000000 → 表地址 000000000088）
        self.assertEqual(r["nodes"][0]["addr"], "060504030201")
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
            # 列序：1序号 2从节点地址 3信号品质 4中继级别 5协议类型 6相位
            #       7在网判定 8节点信息 9获取时间
            self.assertEqual([ws.cell(row=1, column=c).value
                              for c in range(1, 10)],
                             ["序号", "从节点地址", "侦听信号品质", "中继级别",
                              "协议类型", "相位", "在网判定",
                              "从节点信息(hex)", "获取时间"])
            self.assertEqual(ws.cell(row=2, column=2).value, "060504030201")
            self.assertEqual(ws.cell(row=2, column=5).value, 0)   # 协议类型
            self.assertEqual(ws.cell(row=2, column=6).value, 0)   # 相位
            self.assertEqual(ws.cell(row=2, column=7).value, "在线")
        finally:
            os.unlink(path)

    def test_excel_export_protocol_column(self):
        """协议类型列：0x1800 小端 → D13~D11=3 → 协议类型=3（698.45，E3 位段口径）。"""
        import tempfile, os
        io = FakeIO(replies=[_f2_reply([("010203040506", 0x1800)])])
        sess = ArchiveSession()
        query_archive(io, session=sess, timeout=1.0)
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            sess.export_excel(path)
            from openpyxl import load_workbook
            ws = load_workbook(path).active
            self.assertEqual(ws.cell(row=2, column=1).value, 1)
            self.assertEqual(ws.cell(row=2, column=5).value, 3)
            self.assertEqual(ws.cell(row=2, column=8).value, "0018")
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
