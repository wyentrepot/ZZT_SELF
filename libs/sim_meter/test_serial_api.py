"""串口通道（loop:// 虚拟回环）与 REST API 集成测试。

loop:// 由 pyserial 提供：写入字节立即回读，通道的读线程把它当主站
请求收下 → 电表脑应答 → 应答帧写回 → 又被读线程收回（从站应答帧方向
位为 1，脑不再应答，总线上自然收敛）。
"""
import time

import pytest
from fastapi.testclient import TestClient

from parser_lib.adapters.adapter_645 import build_frame_escaped
from sim_meter.api import SimMeterService, create_simeter_app
from sim_meter.journal import MeterJournal
from sim_meter.meter import MeterBrain
from sim_meter.serial_channel import MeterSerialChannel

pytest.importorskip("serial", reason="需要 pyserial")

ADDR = bytes.fromhex("000000000001")


def _d33(data: bytes) -> bytes:
    return bytes((b + 0x33) & 0xFF for b in data)


@pytest.fixture
def channel():
    brain = MeterBrain()
    journal = MeterJournal(port="loop://", log_dir=None)
    ch = MeterSerialChannel(brain, port="loop://", baudrate=2400,
                            journal=journal)
    ch.open()
    time.sleep(0.05)
    yield ch
    ch.close()


class TestSerialChannel:
    def test_echo_request_gets_reply(self, channel):
        req = build_frame_escaped(ADDR, 0x13, b"")            # 广播读通信地址
        channel.send_frame(req)
        deadline = time.time() + 3
        reply = None
        while time.time() < deadline:
            frame = channel.recv_frame(timeout=0.2)
            if frame is not None and frame[8] & 0x80:          # 从站应答帧
                reply = frame
                break
        assert reply is not None, "未收到应答帧"
        assert reply[8] == 0x93
        assert reply[10:16] == ADDR                            # 13H 应答地址不加 33H

    def test_journal_records_rx_tx(self, channel):
        req = build_frame_escaped(ADDR, 0x11, _d33(bytes.fromhex("00000100")))
        channel.send_frame(req)
        deadline = time.time() + 3
        while time.time() < deadline:
            # 应答 tx 的 note 带 func（区别于手动注入 tx 的 manual_send）
            if any(e["dir"] == "tx" and e["note"].get("func")
                   for e in channel.journal.entries()):
                break
            time.sleep(0.05)
        entries = channel.journal.entries()
        dirs = [e["dir"] for e in entries]
        assert "rx" in dirs and "tx" in dirs
        tx = [e for e in entries if e["dir"] == "tx" and e["note"].get("func")][-1]
        assert tx["note"]["func"] == "读数据"

    def test_baudrate_hot_switch(self, channel):
        channel.set_baudrate(9600)
        assert channel.baudrate == 9600
        assert channel.is_open()                               # 读线程跨重开存活
        req = build_frame_escaped(ADDR, 0x13, b"")
        channel.send_frame(req)
        deadline = time.time() + 3
        reply = None
        while time.time() < deadline:
            frame = channel.recv_frame(timeout=0.2)
            if frame is not None and frame[8] & 0x80:          # 跳过回环 echo 的请求帧
                reply = frame
                break
        assert reply is not None and reply[8] == 0x93


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    svc = SimMeterService(brain=MeterBrain(), log_dir=None)
    app = create_simeter_app(service=svc)
    return TestClient(app)


class TestApi:
    def test_status_and_config(self, client):
        r = client.get("/api/simeter/status")
        assert r.status_code == 200
        assert r.json()["brain"]["version"] == "2007"          # 默认 07 版
        r = client.get("/api/simeter/config")
        assert r.json()["addr"] == "000000000001"
        assert "0x12" in r.json()["jzjc_types"]

    def test_config_version_and_addr(self, client):
        r = client.post("/api/simeter/config",
                        json={"version": "97", "addr": "1200000000AA"})
        assert r.status_code == 200
        assert r.json()["brain"]["version"] == "1997"
        assert r.json()["brain"]["addr"] == "1200000000AA"
        r = client.post("/api/simeter/config", json={"version": "07"})
        assert r.json()["brain"]["version"] == "2007"
        r = client.post("/api/simeter/config", json={"version": "25"})
        assert r.status_code == 400

    def test_baud_endpoint_closed_state(self, client):
        r = client.post("/api/simeter/baud", json={"baudrate": 9600})
        assert r.status_code == 200
        assert r.json()["brain"]["baudrate"] == 9600

    def test_data_get_set(self, client):
        r = client.get("/api/simeter/data")
        assert r.json()["registers"]["00010000"]["value"] == "001234.56"
        r = client.post("/api/simeter/data",
                        json={"di": "00010000", "value": "002345.67"})
        assert r.status_code == 200
        r = client.post("/api/simeter/data",
                        json={"di": "00010000", "value": "xx"})
        assert r.status_code == 400

    def test_jzjc_flow(self, client):
        r = client.post("/api/simeter/jzjc", json={"device_type": "0x28"})
        assert r.status_code == 200
        jc = r.json()
        assert jc["device_type"] == "0x28"
        assert jc["descriptor"] == "LKT666Z-D"
        r = client.post("/api/simeter/jzjc/report", json={"style": "sta"})
        assert r.status_code == 200
        payload = bytes.fromhex(r.json()["payload_hex"])
        assert payload[0] == 51
        n = payload[8]
        assert payload[9:9 + n] == b"LKT666Z-D"

    def test_handle_hex_roundtrip(self, client):
        req = build_frame_escaped(ADDR, 0x13, b"").hex().upper()
        r = client.post("/api/simeter/handle_hex", json={"hex": req})
        assert r.status_code == 200
        body = r.json()
        assert body["note"]["func"] == "读通信地址"
        reply = bytes.fromhex(body["reply_wire_hex"])
        assert reply[8] == 0x93 and reply[10:16] == ADDR

    def test_baud_via_handle_hex(self, client):
        # 17H 更改速率：脑内状态与 action 一并暴露
        req = build_frame_escaped(ADDR, 0x17, _d33(bytes([0x20]))).hex().upper()
        r = client.post("/api/simeter/handle_hex", json={"hex": req})
        body = r.json()
        assert body["note"]["baudrate"] == 9600
        assert body["action"] == ["baud", 9600]
        status = client.get("/api/simeter/status").json()
        assert status["brain"]["baudrate"] == 9600

    def test_open_requires_port(self, client):
        r = client.post("/api/simeter/open", json={})
        assert r.status_code == 400

    def test_open_loop_and_frames(self):
        svc = SimMeterService(brain=MeterBrain(), log_dir=None)
        app = create_simeter_app(service=svc)
        with TestClient(app) as client:
            r = client.post("/api/simeter/open", json={"port": "loop://"})
            assert r.status_code == 200
            assert r.json()["serial"]["open"] is True
            req = build_frame_escaped(ADDR, 0x13, b"").hex().upper()
            assert client.post("/api/simeter/send_hex",
                               json={"hex": req}).status_code == 200
            deadline = time.time() + 3
            while time.time() < deadline:
                frames = client.get("/api/simeter/frames").json()
                if any(e["dir"] == "tx" for e in frames["entries"]):
                    break
                time.sleep(0.05)
            frames = client.get("/api/simeter/frames?dir=tx").json()
            assert frames["entries"], "应答帧未入会话日志"
            assert client.post("/api/simeter/close").status_code == 200
            status = client.get("/api/simeter/status").json()
            assert status["serial"] is None

def test_meta_endpoint_self_describing_and_state_service():
    """_meta 小抄：端点清单 + 典型序列 + 错误语义；state 暴露服务供 AI 桥用。"""
    svc = SimMeterService(brain=MeterBrain(), log_dir=None)
    app = create_simeter_app(service=svc)
    client = TestClient(app)
    r = client.get("/api/simeter/_meta")
    assert r.status_code == 200
    meta = r.json()
    assert meta["service"].startswith("sim_meter")
    assert "POST /open" in meta["endpoints"] and "GET /frames" in meta["endpoints"]
    assert any("000000000001" in n for n in meta["notes"])
    assert meta["open_sequence"][0].startswith("POST /api/simeter/open")
    # 工作台 AI 桥经 app.state 拿服务实例
    assert app.state.simeter_service is svc
