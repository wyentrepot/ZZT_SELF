"""会话帧日志（FrameJournal / SessionManager）单元测试。"""
from __future__ import annotations

import json

import pytest

from sim_concentrator.frame_codec import build_13762_frame, build_local_13762_frame
from sim_concentrator.journal import (
    FrameJournal,
    SessionManager,
    normalize_afn,
    normalize_fn,
)


def _down_frame() -> bytes:
    return build_local_13762_frame(afn=0x06, fn=230, buff=b"")


def _up_frame() -> bytes:
    return build_13762_frame(afn=0x06, fn=230, direction="up")


@pytest.fixture
def journal(tmp_path):
    return FrameJournal(port="COM99", log_dir=tmp_path)


class TestNormalize:
    def test_afn_variants(self):
        assert normalize_afn(6) == "06"
        assert normalize_afn("6") == "06"
        assert normalize_afn("0x06") == "06"
        assert normalize_afn("AB") == "AB"
        assert normalize_afn("zz") is None
        assert normalize_afn(None) is None

    def test_fn_variants(self):
        assert normalize_fn(230) == "F230"
        assert normalize_fn("230") == "F230"
        assert normalize_fn("F230") == "F230"
        assert normalize_fn("f1") == "F1"
        assert normalize_fn("x9") is None


class TestFrameJournal:
    def test_append_tx_rx_with_decode_summary_and_jsonl(self, journal, tmp_path):
        up = journal.append("rx", _up_frame())
        down = journal.append("tx", _down_frame())

        assert up["seq"] == 1 and down["seq"] == 2
        assert up["dir"] == "rx" and up["updown"] == "up"
        assert up["afn"] == "06" and up["fn"] == "F230"
        assert down["dir"] == "tx" and down["updown"] == "down"
        assert isinstance(up["parsed"], dict) and up["parsed"]

        info = journal.info()
        assert info["counts"] == {"tx": 1, "rx": 1, "uplink": 1}
        assert info["last_seq"] == 2
        assert info["log_file"] == f"{journal.session_id}.jsonl"

        lines = (tmp_path / f"{journal.session_id}.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["frame_hex"] == up["frame_hex"]
        assert first["afn"] == "06"

    def test_scope_tags_run_id_and_kind(self, journal):
        with journal.scope("run-1", "step_send"):
            tx1 = journal.append("tx", _down_frame())
            with journal.scope(None, "auto_reply"):
                tx2 = journal.append("tx", _down_frame())
            rx = journal.append("rx", _up_frame())
        outside = journal.append("tx", _down_frame())

        assert tx1["run_id"] == "run-1" and tx1["kind"] == "step_send"
        assert tx2["run_id"] == "run-1" and tx2["kind"] == "auto_reply"
        assert rx["run_id"] == "run-1" and rx["kind"] is None
        assert outside["run_id"] is None and outside["kind"] is None

    def test_query_filters(self, journal):
        with journal.scope("run-a", "step_send"):
            journal.append("tx", _down_frame())
        with journal.scope("run-b", "manual_send"):
            journal.append("rx", _up_frame())
            journal.append("tx", _down_frame())

        assert len(journal.query(direction="tx")["entries"]) == 2
        uplinks = journal.query(updown="up")
        assert len(uplinks["entries"]) == 1
        assert uplinks["counts"] == {"tx": 2, "rx": 1, "uplink": 1}

        by_afn = journal.query(updown="up", afn="06")
        assert len(by_afn["entries"]) == 1 and by_afn["entries"][0]["fn"] == "F230"
        assert journal.query(afn="FF")["entries"] == []

        by_run = journal.query(run_id="run-a")
        assert len(by_run["entries"]) == 1 and by_run["entries"][0]["dir"] == "tx"
        assert len(journal.query(kind="manual_send")["entries"]) == 1

    def test_query_pagination(self, journal):
        for _ in range(3):
            journal.append("tx", _down_frame())
        page1 = journal.query(limit=2)
        assert len(page1["entries"]) == 2
        assert page1["has_more"] is True
        page2 = journal.query(limit=2, after_seq=page1["next_after_seq"])
        assert len(page2["entries"]) == 1
        assert page2["has_more"] is False

    def test_memory_cap_keeps_recent(self, tmp_path):
        journal = FrameJournal(port="COM99", log_dir=tmp_path, memory_limit=3)
        for _ in range(5):
            journal.append("tx", _down_frame())
        assert journal.info()["last_seq"] == 5
        assert len(journal.query()["entries"]) == 3

    def test_append_invalid_input_never_raises(self, journal):
        assert journal.append("tx", b"") is None
        assert journal.append("tx", b"\xff\xfe\xff") is not None  # 不可解码帧也留底


class TestSessionManager:
    def test_open_and_resolve(self, tmp_path):
        manager = SessionManager(log_dir=tmp_path)
        assert manager.resolve() is None
        first = manager.open_session("COM1")
        second = manager.open_session("COM2")
        assert manager.current() is second
        assert manager.resolve() is second
        assert manager.resolve(first.session_id) is first
        assert manager.resolve("sc-nope") is None

    def test_ephemeral_session_queryable(self, tmp_path):
        manager = SessionManager(log_dir=tmp_path)
        temp = FrameJournal(port="COMX", log_dir=tmp_path)
        temp.append("tx", _down_frame())
        manager.register_ephemeral(temp)
        manager.finalize(temp)
        assert manager.resolve(temp.session_id) is temp
        assert manager.resolve().session_id == temp.session_id  # 无当前会话时取最近

    def test_retention_trims_oldest(self, tmp_path):
        manager = SessionManager(log_dir=tmp_path, retain=2)
        s1 = manager.open_session("COM1")
        manager.open_session("COM2")
        s3 = manager.open_session("COM3")
        assert manager.get(s1.session_id) is None
        assert manager.get(s3.session_id) is s3
        infos = manager.list_info()
        assert len(infos) == 2
        assert any(item["current"] for item in infos)
