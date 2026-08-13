"""API + CLI 单元测试。"""
import json

import pytest
from fastapi.testclient import TestClient

from sim_concentrator.api import create_simcon_app


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

    def test_responders(self, client):
        r = client.get("/api/simcon/responders")
        assert r.status_code == 200
        rules = r.json()["rules"]
        assert len(rules) >= 5
        assert any("builtin.01xx_init" == x["id"] for x in rules)

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

    def test_verify_bad_file(self):
        from sim_concentrator import cli

        assert cli.main(["verify", "no_such_file.json"]) != 0
