"""REQS-0027 单测：expect_rules 加载/默认 expect/否认码/超时档位/滑窗调度/聚合。"""
import sys
import time
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim_concentrator import expect_rules as er
from sim_concentrator.batch import BatchReadJob, meter_read_645, _addr_in_frame
from sim_concentrator.frame_codec import build_13762_frame, decode_frame, frame_to_hex
from sim_concentrator.matcher import deny_info
from sim_concentrator.runner import run_single_step
from sim_concentrator.aggregate import collect_readings


# ---------------------------------------------------------------------------
# expect_rules：加载与档位
# ---------------------------------------------------------------------------
def test_rules_load_and_shape():
    doc = er.load(refresh=True)
    assert doc["rules"] and doc["timeout_tiers"]
    assert any(r["id"] == "er.05" for r in doc["rules"])


def test_timeout_tiers():
    assert er.timeout_for(0x02, 1)["seconds"] == 59.0
    assert er.timeout_for(0x02, 1)["tier"] == "single_read"
    assert er.timeout_for(0xF1, 1)["seconds"] == 99.0
    assert er.timeout_for(0xF1, 1)["tier"] == "batch_read"
    assert er.timeout_for(0x03, 4)["seconds"] == 5.0
    assert er.timeout_for(0x11, 2)["tier"] == "default"
    assert "59" in er.timeout_for(0x02, 1)["note"]


def test_default_expect_mapping():
    e, r = er.default_expect(0x05, 2)
    assert e["afn"] == 0x00 and e["fn"] == 1 and e["dir"] == "up"
    assert e["form"] == "confirm_or_deny"
    e, _ = er.default_expect(0x03, 4)
    assert e["afn"] == 0x03 and e["fn"] == 4 and e["form"] == "table"
    e, _ = er.default_expect(0x02, 1)
    assert e["afn"] == 0x02 and e["fn"] == 1
    # 06H 主动上报无预期应答
    assert er.default_expect(0x06, 3) == (None, None)
    assert er.is_report_afn(0x06) and not er.is_report_afn(0x03)


def test_deny_text():
    assert "超过最大并发数" in er.deny_text(0x6D)
    assert "超过" in er.deny_text(0x6E) and "正在抄读" in er.deny_text(0x6F)
    assert "通信超时" in er.deny_text(0)


# ---------------------------------------------------------------------------
# matcher：否认帧识别
# ---------------------------------------------------------------------------
def _deny_frame(code: int) -> bytes:
    return build_13762_frame(afn=0x00, fn=2, appdata=bytes([code]),
                             direction="up", info={"seq": 1},
                             address={"src": "111111111111", "dst": "222222222222"})


def test_deny_info_detection():
    d = deny_info(decode_frame(_deny_frame(0x6D)))
    assert d is not None and d["code"] == 109 and "超过最大并发数" in d["text"]
    assert deny_info(decode_frame(_deny_frame(0x01)))["code"] == 1
    confirm = build_13762_frame(afn=0x00, fn=1, appdata=b"", direction="up")
    assert deny_info(decode_frame(confirm)) is None


# ---------------------------------------------------------------------------
# run_single_step：auto_expect + per-Fn 超时
# ---------------------------------------------------------------------------
class FakeStepIO:
    """极简 io：send 时立即向历史+队列注入回帧。"""

    def __init__(self, reply_fn):
        self.journal = None
        self.history = []
        self.sent = []
        self.reply_fn = reply_fn

    def send_frame(self, raw):
        self.sent.append(raw)
        for up in self.reply_fn(raw) or []:
            self.history.append(up)

    def rx_history(self):
        return list(self.history)

    def recv_frame(self, timeout=None):
        return self.history.pop(0) if self.history else None


def test_auto_expect_confirm():
    io = FakeStepIO(lambda raw: [build_13762_frame(
        afn=0x00, fn=1, appdata=b"", direction="up")])
    out = run_single_step(io, send={"afn": 0x05, "fn": 2, "params": {"enable": 1}},
                          profile={}, seq=1)
    step = out["step"]
    assert step["result"] == "pass"
    assert step["pairing"]["expect_source"] == "auto"
    assert step["pairing"]["timeout"]["tier"] == "default"


def test_auto_expect_deny_path():
    io = FakeStepIO(lambda raw: [_deny_frame(0x6D)])
    out = run_single_step(io, send={"afn": 0x05, "fn": 2, "params": {"enable": 1}},
                          profile={}, seq=1)
    step = out["step"]
    assert step["result"] == "fail"
    assert step["deny"]["code_hex"] == "6D"
    assert "超过最大并发数" in step["reason"]


def test_auto_expect_timeout_tier_single_read():
    # 02H-F1 需要 protocol+payload（645 读数据帧 hex）
    from sim_concentrator.batch import meter_read_645
    io = FakeStepIO(lambda raw: [])
    out = run_single_step(io, send={
        "afn": 0x02, "fn": 1,
        "params": {"protocol": 2, "payload": meter_read_645("111111111111").hex()},
    }, profile={}, expect_timeout=0.3, seq=1)
    step = out["step"]
    assert step["pairing"]["timeout"]["seconds"] == 59.0
    assert step["result"] == "fail" and "超时" in step["reason"]


def test_explicit_expect_overrides_auto():
    io = FakeStepIO(lambda raw: [build_13762_frame(
        afn=0x03, fn=4, appdata=b"", direction="up")])
    out = run_single_step(io, send={"afn": 0x03, "fn": 4, "params": {}},
                          profile={}, expect={"afn": 0x03, "fn": 4, "dir": "up"},
                          seq=1)
    step = out["step"]
    assert step["result"] == "pass"
    assert "pairing" not in step  # 显式 expect 不带 auto 配对元数据


# ---------------------------------------------------------------------------
# 滑窗调度（G5）
# ---------------------------------------------------------------------------
class FakeWindowIO:
    """模拟 CCO：收到抄读帧后延时回同 AFN/Fn 上行帧（含表地址）。

    deny_meters 中的表回 00H-F2(6D)；silent_meters 不回（触发超时释放）。
    """

    def __init__(self, meters, delay=0.1, deny=(), silent=()):
        self.journal = None
        self.history = []
        self.sent = []
        self.lock = threading.Lock()
        self.delay = delay
        self.deny = set(deny)
        self.silent = set(silent)
        self.meter_set = set(meters)

    def send_frame(self, raw):
        self.sent.append(raw)
        hexs = raw.hex().upper()
        target = None
        for m in self.meter_set:
            if _addr_in_frame(raw, m):
                target = m
                break
        if target is None or target in self.silent:
            return
        t = threading.Timer(self.delay, self._reply, args=(target,))
        t.daemon = True
        t.start()

    def _reply(self, meter):
        addr_le = bytes.fromhex(meter)[::-1]
        with self.lock:
            if meter in self.deny:
                self.history.append(build_13762_frame(
                    afn=0x00, fn=2, appdata=bytes([0x6D]), direction="up",
                    info={"seq": 1}, address={"src": meter, "dst": "999999999999"}))
            else:
                # 回 02H-F1 上行，嵌套 645 应答（帧内含表地址即可被归属）
                from parser_lib.adapters.adapter_645 import build_frame
                n645 = build_frame(addr_le, 0x91, bytes([0x33] * 4))
                payload = bytes([2, 0]) + len(n645).to_bytes(2, "big") + n645
                self.history.append(build_13762_frame(
                    afn=0x02, fn=1, appdata=payload, direction="up"))

    def rx_history(self):
        with self.lock:
            return list(self.history)


def test_meter_read_645_shape():
    raw = meter_read_645("111111111111")
    assert raw[0] == 0x68 and raw[-1] == 0x16
    assert _addr_in_frame(raw, "111111111111")


def test_sliding_window_keeps_max_concurrent():
    meters = [f"{i:012d}" for i in range(1, 7)]  # 6 块表
    io = FakeWindowIO(meters, delay=0.2)
    job = BatchReadJob(io, meters, max_concurrent=2, mode="single", timeout=3.0)
    peak = {"n": 0}
    orig = job._row

    def spy_row(*a, **kw):
        with job.lock:
            peak["n"] = max(peak["n"], job.state["in_flight"])
        return orig(*a, **kw)

    job._row = spy_row
    job.run()
    snap = job.snapshot()
    assert snap["finished"]
    assert snap["done"] == 6 and snap["success"] == 6 and snap["failed"] == 0
    assert peak["n"] <= 2  # 在途数从未超过最大并发


def test_slot_release_paths_deny_timeout():
    meters = ["000000000001", "000000000002", "000000000003"]
    io = FakeWindowIO(meters, delay=0.05, deny={"000000000002"},
                      silent={"000000000003"})
    job = BatchReadJob(io, meters, max_concurrent=3, mode="single", timeout=0.4)
    job.run()
    snap = job.snapshot()
    assert snap["success"] == 1
    assert snap["failed"] == 2
    assert any(r["status"] == "deny" and r["deny_code"] == "6D"
               for r in snap["rows"])
    assert any(r["status"] == "timeout" for r in snap["rows"])
    assert snap["deny_breakdown"].get("6D") == 1
    assert snap["timeout"]["tier"] == "manual" and snap["timeout"]["seconds"] == 0.4


def test_timeout_tier_batch_mode():
    # 不启动任务（不 run），只验证档位解析：mode=batch → 并抄 99s
    io = FakeWindowIO(["000000000001"], delay=0.05)
    job = BatchReadJob(io, ["000000000001"], max_concurrent=1, mode="batch")
    assert job.timeout_meta["seconds"] == 99.0
    job2 = BatchReadJob(io, ["000000000001"], max_concurrent=1, mode="batch",
                        timeout=3.0)
    assert job2.timeout_meta["tier"] == "manual"


# ---------------------------------------------------------------------------
# 聚合（G4）
# ---------------------------------------------------------------------------
def test_collect_readings_stats():
    class FakeJob:
        def __init__(self, snap):
            self._snap = snap

        def snapshot(self):
            return self._snap

    jobs = {"j1": FakeJob({
        "job_id": "j1", "mode": "single", "rows": [
            {"meter": "1", "afn_fn": "02H-F1", "status": "success", "ts": "2026-09-05T10:00:00"},
            {"meter": "2", "afn_fn": "00H-F2", "status": "deny", "ts": "2026-09-05T10:00:01",
             "deny_code": "6D", "deny_text": "超过最大并发数"},
            {"meter": "3", "afn_fn": "02H-F1", "status": "timeout", "ts": "2026-09-05T10:00:02"},
        ]})}
    out = collect_readings(None, jobs)
    s = out["stats"]
    assert s["sent"] == 3 and s["success"] == 1 and s["failed"] == 2
    assert s["replied"] == 2
    assert s["success_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["deny_breakdown"].get("6D 超最大并发") == 1
    assert s["deny_breakdown"].get("timeout 超时/无应答") == 1
    # 过滤
    out2 = collect_readings(None, jobs, result="deny")
    assert len(out2["rows"]) == 1 and out2["stats"]["sent"] == 1
