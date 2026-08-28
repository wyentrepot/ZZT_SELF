"""runner 与会话帧日志的集成测试（execute_task / run_single_step / run_id 归属）。"""
from __future__ import annotations

import json

import pytest

from sim_concentrator.frame_codec import build_13762_frame, build_local_13762_frame
from sim_concentrator.journal import FrameJournal
from sim_concentrator.responder import Responder
from sim_concentrator.runner import execute_task, run_single_step
from sim_concentrator.scenario_codec import ScenarioCodecError


class FakeJournalIO:
    """带 journal 的假串口：send_frame/收帧走真实 journal 记录路径。"""

    def __init__(self, journal, preset_rx=None):
        self.journal = journal
        self.sent = []
        self._rx = list(preset_rx or [])
        self.port = "COM_TEST"
        self.baudrate = 9600
        self.port_identity = {"mapping_id": "simcon", "device": "COM_TEST"}
        self._open = True

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def is_open(self):
        return self._open

    def send_frame(self, raw):
        raw = bytes(raw)
        self.sent.append(raw)
        if self.journal is not None:
            self.journal.append("tx", raw)

    def recv_frame(self, timeout=None):
        if self._rx:
            return self._rx.pop(0)
        import time
        time.sleep(0.01)
        return None

    def pending_frames(self):
        return len(self._rx)

    def rx_history(self):
        return list(self._rx)


def _up_report() -> bytes:
    return build_13762_frame(afn=0x06, fn=230, direction="up")


def _down_frame() -> bytes:
    return build_local_13762_frame(afn=0x06, fn=230, buff=b"")


def _task(**overrides):
    task = {
        "id": "t1",
        "port": "COM_TEST",
        "enable_responder": False,
        "profile": "test",
        "steps": [
            {"name": "s1", "send": {"format": "local", "afn": 0x06, "fn": 230, "buff": ""}},
        ],
    }
    task.update(overrides)
    return task


class TestExecuteTaskJournal:
    def test_run_id_and_frames_seq_in_response(self, tmp_path):
        journal = FrameJournal(port="COM_TEST", log_dir=tmp_path)
        io = FakeJournalIO(journal)
        result = execute_task(_task(), io=io)

        assert result["session_id"] == journal.session_id
        assert result["run_id"].startswith("run-t1-")
        assert result["frames_seq"] == [1, 1]
        assert result["summary"]["verdict"] == "pass"

        tx = journal.query(direction="tx")["entries"]
        assert len(tx) == 1
        assert tx[0]["kind"] == "step_send"
        assert tx[0]["run_id"] == result["run_id"]
        assert tx[0]["afn"] == "06" and tx[0]["fn"] == "F230"

    def test_journal_file_written(self, tmp_path):
        journal = FrameJournal(port="COM_TEST", log_dir=tmp_path)
        execute_task(_task(), io=io_fixture(journal))
        lines = (tmp_path / f"{journal.session_id}.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["dir"] == "tx"

    def test_auto_reply_tagged(self, tmp_path):
        journal = FrameJournal(port="COM_TEST", log_dir=tmp_path)
        io = FakeJournalIO(journal, preset_rx=[_up_report()])
        task = _task(enable_responder=True, steps=[
            {"name": "等上报", "recv_only": True, "expect": {"afn": 6, "fn": 230},
             "expect_timeout": 1.0},
        ])
        result = execute_task(task, io=io)
        assert result["steps"][0]["result"] == "pass"

        tx = journal.query(direction="tx")["entries"]
        assert len(tx) == 1
        assert tx[0]["kind"] == "auto_reply"
        assert tx[0]["run_id"] == result["run_id"]


def io_fixture(journal):
    return FakeJournalIO(journal)


class TestRunSingleStep:
    def test_manual_send_tagged_and_seq_range(self, tmp_path):
        journal = FrameJournal(port="COM_TEST", log_dir=tmp_path)
        io = FakeJournalIO(journal)
        out = run_single_step(
            io, send={"afn": "00", "fn": 1, "params": {}}, seq=7,
        )
        assert out["step"]["result"] == "pass"
        assert out["run_id"].startswith("manual-")
        assert out["session_id"] == journal.session_id
        assert out["frames_seq"] == [1, 1]

        tx = journal.query(direction="tx", kind="manual_send")["entries"]
        assert len(tx) == 1

    def test_raw_rejected(self, tmp_path):
        io = FakeJournalIO(FrameJournal(port="COM_TEST", log_dir=tmp_path))
        with pytest.raises(ScenarioCodecError):
            run_single_step(io, send={"raw": "6800"})

    def test_send_and_recv_only_mutually_exclusive(self, tmp_path):
        io = FakeJournalIO(FrameJournal(port="COM_TEST", log_dir=tmp_path))
        with pytest.raises(ScenarioCodecError):
            run_single_step(io, send={"afn": 0, "fn": 1}, recv_only=True)
        with pytest.raises(ScenarioCodecError):
            run_single_step(io, recv_only=False, send=None)

    def test_recv_only_waits_for_uplink(self, tmp_path):
        journal = FrameJournal(port="COM_TEST", log_dir=tmp_path)
        io = FakeJournalIO(journal, preset_rx=[_up_report()])
        out = run_single_step(
            io, recv_only=True, expect={"afn": 6, "fn": 230}, expect_timeout=1.0,
        )
        assert out["step"]["result"] == "pass"
        assert out["step"]["parsed"]["fields"]["AFN"]["raw"] == 6
