"""CCO 本地协议（单 68 帧）编解码 / 匹配 / 应答 / 执行闭环 单元测试。

覆盖：
- fn_to_dt / dt_to_fn FN 编码往返（231/230/10/3/4/2）
- build_local_13762_frame 构帧 → scan_local_frame 切帧 → decode_local_13762_frame 解析
- matcher 对本地帧的 afn/fn 匹配与不匹配
- responder 对 CCO 主动上报统一回 00H-F1 确认
- run_step: recv_only 等待主动上报、send+expect 回复、recv_only 超时判 fail
- execute_task 端到端（含 responder 自动应答）
"""
from __future__ import annotations

import time

from sim_concentrator.frame_codec import (
    build_local_13762_frame,
    decode_local_13762_frame,
    dt_to_fn,
    fn_to_dt,
    frame_to_hex,
    scan_local_frame,
)
from sim_concentrator.matcher import match_frame
from sim_concentrator.responder import Responder
from sim_concentrator.runner import build_send_frame, execute_task, run_step


def _cco(afn: int, fn: int, buff: bytes = b""):
    return build_local_13762_frame(afn=afn, fn=fn, buff=buff)


class FakeIO:
    """模拟 SerialIO：预置响应入队 + 历史记录。"""

    def __init__(self, responses=None, port="COM_TEST"):
        self._pending = list(responses or [])
        self.sent = []
        self._history = []
        self.port = port
        self.closed = False

    def open(self):
        return True

    def close(self):
        self.closed = True

    def is_open(self):
        return True

    def send_frame(self, raw):
        self.sent.append(raw)

    def recv_frame(self, timeout=None):
        end = time.time() + (timeout if timeout else 1.0)
        while time.time() < end:
            if self._pending:
                return self._pending.pop(0)
            time.sleep(0.01)
        return None

    def rx_history(self):
        return list(self._history)

    def pending_frames(self):
        return len(self._pending)


# ---------------------------------------------------------------------------
# FN 编码
# ---------------------------------------------------------------------------
def test_fn_to_dt_roundtrip():
    for fn in [1, 2, 3, 4, 5, 6, 7, 8, 10, 100, 230, 231, 232]:
        dt1, dt2 = fn_to_dt(fn)
        assert dt_to_fn(dt1, dt2) == fn, (fn, dt1, dt2)


def test_fn_to_dt_specific():
    # 231 -> DT1=64(0x40), DT2=28(0x1C)
    assert fn_to_dt(231) == (64, 28)
    # 230 -> DT1=32(0x20), DT2=28(0x1C)
    assert fn_to_dt(230) == (32, 28)
    # 10 -> DT1=2, DT2=1
    assert fn_to_dt(10) == (2, 1)
    # 4 -> DT1=8, DT2=0
    assert fn_to_dt(4) == (8, 0)


# ---------------------------------------------------------------------------
# 构帧 / 切帧 / 解析
# ---------------------------------------------------------------------------
def test_build_scan_decode_roundtrip():
    raw = build_local_13762_frame(afn=0x10, fn=230, buff=b"\x00")
    frame, consumed = scan_local_frame(raw)
    assert frame == raw
    assert consumed == len(raw)
    d = decode_local_13762_frame(raw)
    assert d["structure"] == "1376.2-local"
    assert d["afn"] == 0x10 and d["fn"] == 230
    assert d["buff"] == b"\x00"
    assert d["ctrl"] == 0x03


def test_scan_local_frame_dirty_prefix():
    raw = build_local_13762_frame(afn=0x10, fn=4)
    stream = b"\xaa\xbb" + raw
    frame, consumed = scan_local_frame(stream)
    assert frame == raw


def test_build_send_frame_local_format():
    raw = build_send_frame({"format": "local", "afn": 0x10, "fn": 230, "buff": b"\x00"})
    d = decode_local_13762_frame(raw)
    assert d["afn"] == 0x10 and d["fn"] == 230


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------
def test_match_local_frame():
    resp = _cco(0x10, 230, buff=b"\x01\x00\x00\x00\x01")
    matched, dec, reasons = match_frame(resp, {"format": "local", "afn": 0x10, "fn": 230})
    assert matched, reasons
    matched, _, reasons = match_frame(resp, {"format": "local", "afn": 0x11, "fn": 231})
    assert not matched


# ---------------------------------------------------------------------------
# 应答
# ---------------------------------------------------------------------------
def test_responder_unified_ack():
    rp = Responder()
    for afn, fn in [(6, 230), (6, 3), (3, 10)]:
        report = _cco(afn, fn, buff=b"\x00")
        reply = rp.reply_for(report)
        assert reply is not None, (afn, fn)
        d = decode_local_13762_frame(reply)
        assert d["afn"] == 0x00 and d["fn"] == 1, (afn, fn, d)


# ---------------------------------------------------------------------------
# 单步执行
# ---------------------------------------------------------------------------
def test_run_step_recv_only_wait_report():
    io = FakeIO(responses=[_cco(0x06, 3, buff=b"\x04")])
    step = {"name": "等待06H-F3上报空闲", "recv_only": True,
            "expect": {"format": "local", "afn": 0x06, "fn": 3},
            "expect_timeout": 1.0}
    r = run_step(io, None, step, 0)
    assert r["result"] == "pass", r


def test_run_step_send_then_expect():
    io = FakeIO(responses=[_cco(0x10, 230, buff=b"\x01\x00\x00\x00\x01")])
    step = {"name": "查询任务数量", "send": {"format": "local", "afn": 0x10, "fn": 230},
            "expect": {"format": "local", "afn": 0x10, "fn": 230}, "expect_timeout": 1.0}
    r = run_step(io, None, step, 0)
    assert r["result"] == "pass", r
    assert r["sent_hex"]


def test_run_step_recv_only_timeout_fail():
    io = FakeIO(responses=[])
    step = {"name": "等待未出现的上报", "recv_only": True,
            "expect": {"format": "local", "afn": 0x06, "fn": 230}, "expect_timeout": 0.3}
    r = run_step(io, None, step, 0)
    assert r["result"] == "fail", r
    assert "超时" in r["reason"]


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------
def test_execute_task_end_to_end():
    task = {
        "id": "anhui.minute_collect",
        "port": "COM_TEST",
        "enable_responder": True,
        "steps": [
            {"name": "查询任务数量", "send": {"format": "local", "afn": 0x10, "fn": 230},
             "expect": {"format": "local", "afn": 0x10, "fn": 230}, "expect_timeout": 1.0},
            {"name": "等待数据上报", "recv_only": True,
             "expect": {"format": "local", "afn": 0x06, "fn": 230}, "expect_timeout": 1.0},
        ],
    }
    io = FakeIO(responses=[_cco(0x10, 230, buff=b"\x01\x00\x00\x00\x01"),
                           _cco(0x06, 230, buff=b"\x01\x00\x01")])
    out = execute_task(task, io=io)
    assert out["summary"]["verdict"] == "pass", out
    assert out["summary"]["pass"] == 2, out["summary"]
