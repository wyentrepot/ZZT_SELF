"""simcon 帧日志 / 单步执行 / 会话查询 API 测试（全假串口）。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import sim_concentrator.api as simcon_api
from sim_concentrator.api import create_simcon_app
from sim_concentrator.frame_codec import build_13762_frame


class FakeSerialIO:
    """与真实 SerialIO 相同接口 + journal 钩子的假串口。"""

    def __init__(self, **kwargs):
        self.port = kwargs["port"]
        self.port_identity = kwargs.get("port_identity")
        self.baudrate = kwargs.get("baudrate")
        self.journal = kwargs.get("journal")
        self.sent = []
        self._rx = []
        self._open = False

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def is_open(self):
        return self._open

    def pending_frames(self):
        return len(self._rx)

    def send_frame(self, raw):
        raw = bytes(raw)
        self.sent.append(raw)
        if self.journal is not None:
            self.journal.append("tx", raw)

    def recv_frame(self, timeout=None):
        if self._rx:
            return self._rx.pop(0)
        time.sleep(0.01)
        return None

    def rx_history(self):
        return list(self._rx)

    def feed(self, frame):
        self._rx.append(bytes(frame))
        if self.journal is not None:
            self.journal.append("rx", bytes(frame))


@pytest.fixture
def env(monkeypatch, tmp_path):
    captured = {}
    real_cls = FakeSerialIO

    class RecordingIO(real_cls):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured["io"] = self
            captured.setdefault("ios", []).append(self)

    monkeypatch.setattr(simcon_api, "SerialIO", RecordingIO)
    client = TestClient(create_simcon_app(journal_dir=tmp_path))
    return {"client": client, "captured": captured, "tmp_path": tmp_path}


class TestStepEndpoint:
    def test_step_requires_send_or_recv_only(self, env):
        r = env["client"].post("/api/simcon/step", json={})
        assert r.status_code == 422

    def test_step_rejects_raw(self, env):
        r = env["client"].post(
            "/api/simcon/step",
            json={"send": {"raw": "68 00 00 00 16"}},
        )
        assert r.status_code == 422
        assert "ADR-5" in r.json()["detail"]

    def test_step_semantic_send_auto_open_and_journal(self, env):
        r = env["client"].post(
            "/api/simcon/step",
            json={"send": {"afn": "00", "fn": 1, "params": {}}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["step"]["result"] == "pass"
        assert body["session_id"] and body["run_id"].startswith("manual-")
        assert body["frames_seq"] == [1, 1]

        frames = env["client"].get(
            "/api/simcon/frames", params={"direction": "tx"}).json()
        assert len(frames["entries"]) == 1
        assert frames["entries"][0]["afn"] == "00"
        assert frames["entries"][0]["run_id"] == body["run_id"]

        session = env["client"].get("/api/simcon/session").json()
        assert session["current"]["session_id"] == body["session_id"]

    def test_step_recv_only_waits_for_uplink(self, env):
        client = env["client"]
        client.post("/api/simcon/step", json={"send": {"afn": "00", "fn": 1, "params": {}}})
        env["captured"]["io"].feed(build_13762_frame(afn=0x06, fn=230, direction="up"))

        r = client.post("/api/simcon/step", json={
            "recv_only": True, "expect": {"afn": 6, "fn": 230},
            "expect_timeout": 2.0,
        })
        assert r.status_code == 200
        assert r.json()["step"]["result"] == "pass"

        uplinks = client.get("/api/simcon/frames", params={"updown": "up"}).json()
        assert len(uplinks["entries"]) == 1
        assert uplinks["entries"][0]["afn"] == "06"
        assert uplinks["entries"][0]["fn"] == "F230"

    def test_step_second_call_reuses_open_session(self, env):
        client = env["client"]
        first = client.post("/api/simcon/step", json={"send": {"afn": "00", "fn": 1, "params": {}}})
        second = client.post("/api/simcon/step", json={"send": {"afn": "00", "fn": 1, "params": {}}})
        assert first.json()["session_id"] == second.json()["session_id"]
        assert len(env["captured"]["ios"]) == 1


class TestFramesQuery:
    def test_frames_without_session_404(self, env):
        assert env["client"].get("/api/simcon/frames").status_code == 404

    def test_frames_run_id_filter(self, env):
        client = env["client"]
        run1 = client.post("/api/simcon/step", json={"send": {"afn": "00", "fn": 1, "params": {}}}).json()
        run2 = client.post("/api/simcon/step", json={"send": {"afn": "00", "fn": 1, "params": {}}}).json()
        assert run1["run_id"] != run2["run_id"]
        only1 = client.get("/api/simcon/frames", params={"run_id": run1["run_id"]}).json()
        assert len(only1["entries"]) == 1
        assert client.get("/api/simcon/frames", params={"afn": "FF"}).json()["entries"] == []


class TestSessionAndStatus:
    def test_status_includes_session_after_open(self, env):
        client = env["client"]
        assert client.get("/api/simcon/status").json()["session"] is None
        client.post("/api/simcon/open", json={})
        status = client.get("/api/simcon/status").json()
        assert status["open"] is True
        assert status["session"]["session_id"]

    def test_close_keeps_session_queryable(self, env):
        client = env["client"]
        opened = client.post("/api/simcon/open", json={}).json()
        client.post("/api/simcon/step", json={"send": {"afn": "00", "fn": 1, "params": {}}})
        client.post("/api/simcon/close")
        assert client.get("/api/simcon/status").json()["open"] is False

        frames = client.get(
            "/api/simcon/frames", params={"session_id": opened["session_id"]}).json()
        assert len(frames["entries"]) == 1


class TestVerifyJournal:
    def test_verify_returns_run_scoping(self, env):
        client = env["client"]
        r = client.post("/api/simcon/verify", json={
            "id": "t-journal",
            "port": "COM_TEST",
            "enable_responder": False,
            "steps": [
                {"name": "s1", "send": {"format": "local", "afn": 0x06, "fn": 230, "buff": ""}},
            ],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["verdict"] == "pass"
        assert body["session_id"] and body["run_id"].startswith("run-t-journal-")
        assert body["frames_seq"] == [1, 1]

        frames = client.get(
            "/api/simcon/frames", params={"run_id": body["run_id"], "direction": "tx"}).json()
        assert len(frames["entries"]) == 1
        assert frames["entries"][0]["kind"] == "step_send"

    def test_verify_temp_session_survives_for_query(self, env):
        client = env["client"]
        body = client.post("/api/simcon/verify", json={
            "id": "t-temp", "port": "COM_TEST", "enable_responder": False,
            "steps": [{"send": {"format": "local", "afn": 0x06, "fn": 230, "buff": ""}}],
        }).json()
        session = client.get("/api/simcon/session").json()
        ids = [item["session_id"] for item in session["sessions"]]
        assert body["session_id"] in ids
