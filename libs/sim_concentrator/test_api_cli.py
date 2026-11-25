"""API + CLI 单元测试。"""
import json

import pytest
from fastapi.testclient import TestClient

from sim_concentrator.api import create_simcon_app
import sim_concentrator.api as simcon_api


@pytest.fixture
def client():
    return TestClient(create_simcon_app())


@pytest.fixture
def client_without_simcon_mapping(monkeypatch, tmp_path):
    """目录态隔离：本测试组语义是「simcon 无固定映射」，不应依赖仓库
    实况 config/serial_ports.json 是否含该映射（2026-09-29 起实机联调
    需要固定映射，测试须自建无映射目录）。"""
    import shared.serial_mapping as serial_mapping

    cfg = tmp_path / "serial_ports.json"
    cfg.write_text(json.dumps({
        "version": 1,
        "ports": [{"id": "listener", "windows_com": "COM24",
                   "linux_device": "/dev/ttyUSB1", "label": "侦听台",
                   "usage": "listener", "module": "", "baudrate": 115200,
                   "parity": "N", "bytesize": 8, "stopbits": 1,
                   "enabled": True}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(serial_mapping, "default_config_path",
                        lambda: cfg)
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

    def test_ports_list_unmapped_details_without_simcon_mapping(
            self, client_without_simcon_mapping):
        client = client_without_simcon_mapping
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

    def test_step_request_accepts_mapping_id(self, monkeypatch, tmp_path):
        """2026-11-19 现场：StepRequest 缺 mapping_id 字段 → Pydantic 静默丢弃 →
        后端自动选口（/dev/ttyS0）报 Input/output error，页面「下发」必然失败。

        配置用临时目录自建（不依赖仓库实况：simcon 映射已于 2026-11-19 移除）。
        """
        import shared.serial_mapping as serial_mapping

        cfg = tmp_path / "serial_ports.json"
        cfg.write_text(json.dumps({
            "version": 1,
            "ports": [{"id": "simcon", "windows_com": "COM31",
                       "linux_device": "/dev/ttyUSB9", "label": "模拟集中器",
                       "usage": "simcon", "module": "", "baudrate": 460800,
                       "parity": "N", "bytesize": 8, "stopbits": 1,
                       "enabled": True}],
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(serial_mapping, "default_config_path", lambda: cfg)

        created = self._stub_serial_io(monkeypatch)
        client = TestClient(create_simcon_app(journal_dir=tmp_path))

        r = client.post("/api/simcon/step", json={
            "send": {"afn": "03", "fn": "F1", "params": {}},
            "expect_timeout": 0.1,
            "mapping_id": "simcon",
        })

        assert r.status_code == 200
        assert created, "单步应打开显式指定的映射口"
        assert created[0].port_identity["mapping_id"] == "simcon"

    def test_responders(self, client):
        r = client.get("/api/simcon/responders")
        assert r.status_code == 200
        rules = r.json()["rules"]
        assert len(rules) >= 5
        assert any("builtin.01xx_init" == x["id"] for x in rules)

    # -- 换口/换参数必须重开，不能静默复用旧通道（2026-11-19 现场回归） --------
    def _stub_serial_io(self, monkeypatch):
        created = []

        class FakeSerialIO:
            def __init__(self, **kwargs):
                self.port = kwargs["port"]
                self.baudrate = kwargs["baudrate"]
                self.bytesize = kwargs["bytesize"]
                self.parity = kwargs["parity"]
                self.stopbits = kwargs["stopbits"]
                self.port_identity = kwargs.get("port_identity")
                self._open = False
                self.closed = False
                self.sent = []
                created.append(self)

            def open(self):
                self._open = True

            def is_open(self):
                return self._open

            def close(self):
                self._open = False
                self.closed = True

            def pending_frames(self):
                return 0

            def send_frame(self, raw):
                self.sent.append(raw)

            def recv_frame(self, timeout=None):
                return None

        monkeypatch.setattr(simcon_api, "SerialIO", FakeSerialIO)
        return created

    def test_open_switches_port_instead_of_reusing_old_channel(
            self, monkeypatch, tmp_path):
        created = self._stub_serial_io(monkeypatch)
        client = TestClient(create_simcon_app(journal_dir=tmp_path))

        first = client.post("/api/simcon/open",
                            json={"port": "COM_A", "baudrate": 9600, "parity": "E"})
        second = client.post("/api/simcon/open",
                             json={"port": "COM_B", "baudrate": 460800, "parity": "N"})

        assert first.json()["port"] == "COM_A"
        # 换了口就必须真的换：旧通道关闭、新通道打开
        assert second.json()["port"] == "COM_B"
        assert [io.port for io in created] == ["COM_A", "COM_B"]
        assert created[0].closed is True
        assert client.get("/api/simcon/status").json()["port"] == "COM_B"

    def test_open_reopens_when_baud_differs_on_same_port(
            self, monkeypatch, tmp_path):
        created = self._stub_serial_io(monkeypatch)
        client = TestClient(create_simcon_app(journal_dir=tmp_path))

        client.post("/api/simcon/open",
                    json={"port": "COM_A", "baudrate": 9600, "parity": "E"})
        again = client.post("/api/simcon/open",
                            json={"port": "COM_A", "baudrate": 460800, "parity": "N"})

        assert again.status_code == 200
        assert len(created) == 2
        assert created[1].baudrate == 460800
        assert created[0].closed is True

    def test_open_reuses_channel_when_port_and_params_match(
            self, monkeypatch, tmp_path):
        created = self._stub_serial_io(monkeypatch)
        client = TestClient(create_simcon_app(journal_dir=tmp_path))

        client.post("/api/simcon/open",
                    json={"port": "COM_A", "baudrate": 460800, "parity": "N"})
        again = client.post("/api/simcon/open",
                            json={"port": "COM_A", "baudrate": 460800, "parity": "N"})

        assert again.status_code == 200
        assert len(created) == 1

    def test_verify_with_removed_simcon_mapping_returns_409(
            self, client_without_simcon_mapping):
        client = client_without_simcon_mapping
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
