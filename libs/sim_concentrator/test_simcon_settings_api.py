"""simcon /settings API + archive/query 起始序号基址测试（全假串口）。

现场口径（2026-11-19，同一模块同一档案）：

    起始序号 00 00 → 上行 03 00 00        （总数量 3、本次应答 0，空壳）
    起始序号 01 00 → 上行 03 00 03 + 明细 （3 条齐全）

故 /settings 提供 query_start_base（默认 1），archive/query 缺省读它，
显式传 start_base 时以显式值为准。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import sim_concentrator.api as simcon_api
from sim_concentrator.api import create_simcon_app


class FakeSerialIO:
    """最小假串口：open/close/send_frame + journal 钩子。"""

    def __init__(self, **kwargs):
        self.port = kwargs["port"]
        self.port_identity = kwargs.get("port_identity")
        self.baudrate = kwargs.get("baudrate")
        self.journal = kwargs.get("journal")
        self.sent = []
        self._open = False

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def is_open(self):
        return self._open

    def pending_frames(self):
        return 0

    def send_frame(self, raw):
        self.sent.append(bytes(raw))

    def rx_cursor(self):
        return 0

    def rx_history(self):
        return []

    def wait_rx(self, *args, **kwargs):
        return None


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(simcon_api, "SerialIO", FakeSerialIO)
    return TestClient(create_simcon_app(journal_dir=tmp_path))


@pytest.fixture
def settings_dir(monkeypatch, tmp_path):
    """把全局设置定向到 tmp，避免污染真实 data/runtime。"""
    import shared.simcon_settings as ss

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ss, "default_runtime_dir", lambda: runtime)
    return runtime


def test_settings_default_is_base_one(client, settings_dir):
    r = client.get("/api/simcon/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["query_start_base"] == 1
    assert "query_start_base" in body["labels"]


def test_settings_roundtrip(client, settings_dir):
    r = client.post("/api/simcon/settings", json={"query_start_base": 0})
    assert r.status_code == 200 and r.json()["saved"] is True
    assert client.get("/api/simcon/settings").json()["settings"]["query_start_base"] == 0
    # 落盘文件真的写了
    assert (settings_dir / "simcon_settings.json").exists()


def test_settings_ignores_unknown_and_illegal(client, settings_dir):
    client.post("/api/simcon/settings", json={"query_start_base": 1, "bogus": "x"})
    r = client.post("/api/simcon/settings", json={"query_start_base": 99})
    assert r.json()["settings"]["query_start_base"] == 1, "越界值应退回默认 1"


def test_archive_query_applies_start_base(client, settings_dir, monkeypatch):
    """缺省读全局设置；显式 start_base 覆盖全局。捕获实际下发的起始序号字节。"""
    seen = []

    def fake_query(io, *, start, count, timeout, profile=None, session=None, seq=1):
        seen.append(start)
        return {"total": 0, "nodes": []}

    monkeypatch.setattr("sim_concentrator.deep_app.query_archive", fake_query)
    client.post("/api/simcon/open", json={"port": "/dev/null"})
    client.post("/api/simcon/settings", json={"query_start_base": 1})

    client.get("/api/simcon/archive/query?count=16")
    assert seen[-1] == 1, "base=1 时 offset 0 应映射为起始序号 1"

    client.post("/api/simcon/settings", json={"query_start_base": 0})
    client.get("/api/simcon/archive/query?count=16")
    assert seen[-1] == 0, "base=0 时保持 0 起"

    # 显式覆盖优先于全局
    client.post("/api/simcon/settings", json={"query_start_base": 0})
    client.get("/api/simcon/archive/query?count=16&start_base=1")
    assert seen[-1] == 1


def test_archive_query_second_page_offset(client, settings_dir, monkeypatch):
    """分页：start=16 + base=1 → 17（不是 16）。"""
    seen = []

    def fake_query(io, *, start, count, timeout, profile=None, session=None, seq=1):
        seen.append(start)
        return {"total": 0, "nodes": []}

    monkeypatch.setattr("sim_concentrator.deep_app.query_archive", fake_query)
    client.post("/api/simcon/open", json={"port": "/dev/null"})
    client.post("/api/simcon/settings", json={"query_start_base": 1})
    client.get("/api/simcon/archive/query?count=16&start=16")
    assert seen[-1] == 17
