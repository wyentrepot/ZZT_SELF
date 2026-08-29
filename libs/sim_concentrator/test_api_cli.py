"""API + CLI 单元测试。"""
import json

import pytest
from fastapi.testclient import TestClient

from sim_concentrator.api import create_simcon_app
import sim_concentrator.api as simcon_api


@pytest.fixture
def client():
    return TestClient(create_simcon_app())


class TestAPI:
    def test_status_closed(self, client):
        r = client.get("/api/simcon/status")
        assert r.status_code == 200
        assert r.json()["open"] is False

    def test_ports(self, client):
        r = client.get("/api/simcon/ports")
        assert r.status_code == 200
        assert isinstance(r.json()["ports"], list)

    def test_ports_list_unmapped_details_without_simcon_mapping(self, client):
        r = client.get("/api/simcon/ports")

        assert r.status_code == 200
        body = r.json()
        assert body["mapping_error"] == ""
        # simcon 不再有固定映射：端口列表只含未映射实际端口
        assert all(detail["mapping_id"] != "simcon"
                   for detail in body["port_details"])
        assert all(detail["usage"] in ("", "simcon")
                   for detail in body["port_details"])

    def _fake_list_ports(self, monkeypatch, devices):
        import sim_concentrator.serial_io as serial_mod
        from types import SimpleNamespace
        fake = SimpleNamespace(comports=lambda: [
            SimpleNamespace(device=d, description="ELTIMA Virtual Serial Port")
            for d in devices
        ])
        monkeypatch.setattr(serial_mod, "list_ports", fake)

    def test_open_without_explicit_port_auto_selects_port(self, monkeypatch):
        captured = {}

        class FakeSerialIO:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.port = kwargs["port"]
                self.port_identity = kwargs.get("port_identity")
                self._open = False

            def open(self):
                self._open = True

            def is_open(self):
                return self._open

            def close(self):
                self._open = False

            def pending_frames(self):
                return 0

        monkeypatch.setattr(simcon_api, "SerialIO", FakeSerialIO)
        # 仓库配置只映射 listener/cco/sta（COM4/9/8）；注入假端口保证确定性
        self._fake_list_ports(monkeypatch, ["COM10", "COM2"])
        client = TestClient(create_simcon_app())

        r = client.post("/api/simcon/open", json={})

        assert r.status_code == 200
        assert captured["port"] == "COM2"
        assert captured["baudrate"] == 9600
        assert captured["parity"] == "E"
        assert captured["bytesize"] == 8
        assert captured["stopbits"] == 1
        body = r.json()
        assert body["port"] == "COM2"
        assert body["port_identity"]["mapping_id"] == ""

    def test_responders(self, client):
        r = client.get("/api/simcon/responders")
        assert r.status_code == 200
        rules = r.json()["rules"]
        assert len(rules) >= 5
        assert any("builtin.01xx_init" == x["id"] for x in rules)

    def test_verify_with_removed_simcon_mapping_returns_409(self, client):
        # simcon 映射已移除：显式请求该 mapping_id 得到明确报错（不静默换端口）
        r = client.post("/api/simcon/verify", json={"mapping_id": "simcon", "steps": []})

        assert r.status_code == 409
        assert "未找到串口映射" in r.json()["detail"]

    def test_verify_empty_steps_reports_auto_selected_port(self, client, monkeypatch):
        self._fake_list_ports(monkeypatch, ["COM10", "COM2"])
        r = client.post("/api/simcon/verify", json={"steps": []})

        assert r.status_code == 200
        body = r.json()
        assert body["port"] == "COM2"
        assert body["baudrate"] == 9600
        assert body["mapping_id"] == ""
        assert body["summary"]["verdict"] == "fail"

    def test_verify_empty_steps(self, client):
        # 空步骤：verdict 应为 fail（无步骤）
        r = client.post("/api/simcon/verify", json={
            "id": "empty", "port": "COM_TEST",
            "steps": [],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["verdict"] == "fail"
        assert body["summary"]["total"] == 0


class TestCLI:
    def test_responders_cmd(self, capsys):
        from sim_concentrator.cli import cmd_responders

        class A:
            json = True
        rc = cmd_responders(A())
        out = capsys.readouterr().out
        rules = json.loads(out)
        assert any(x["id"] == "builtin.01xx_init" for x in rules)
        assert rc == 0

    def test_ports_cmd(self, capsys):
        from sim_concentrator.cli import cmd_ports

        class A:
            json = True
        rc = cmd_ports(A())
        out = capsys.readouterr().out
        assert "ports" in out
        assert rc == 0

    def test_verify_accepts_mapping_id_flag(self):
        from sim_concentrator import cli

        assert cli.main(["verify", "no_such_file.json", "--mapping-id", "simcon"]) != 0

    def test_verify_bad_file(self):
        from sim_concentrator import cli

        assert cli.main(["verify", "no_such_file.json"]) != 0
