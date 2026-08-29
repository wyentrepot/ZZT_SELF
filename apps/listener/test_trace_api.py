"""通信流追踪（需求 0009）G3 测试：页面 API / live 增量 / feature_hint / 串口钩子。

fixture 索引库 + 假帧，绝不打开真实 COM、绝不写入 runtime 数据（0007 红线）。
"""
import json

import pytest
from fastapi.testclient import TestClient

from listener.app import create_app
from listener.log_service import LogFileService
from listener.serial_service import SerialCaptureService
from listener.trace_service import TraceService
from listener.test_trace_service import (
    ADDR_A,
    DISPLAY_A,
    ack_raw_hex,
    down645,
    summary_ack,
    summary0003,
    up645,
)


class FakeParser:
    def __init__(self, summaries_by_hex):
        self._by_hex = {k.replace(" ", "").upper(): v for k, v in summaries_by_hex.items()}

    def parse_summary(self, value):
        key = value.replace(" ", "").upper()
        if key not in self._by_hex:
            raise ValueError("未登记的 fixture 帧")
        return {"simple": self._by_hex[key]}

    def parse(self, value):
        return {"parse_error": "fixture 不支持完整解析", "simple": {}, "full": {}}


def _frames():
    return [
        down645(0x1EC2, [ADDR_A], t="10:00:00.000"),
        ("10:00:00.100", ack_raw_hex("087"), summary_ack()),
        up645(0x1EC2, ADDR_A, t="10:00:01.000"),
    ]


def build_service(tmp_path):
    frames = _frames()
    parser = FakeParser({raw: summary for _, raw, summary in frames})
    service = LogFileService(parser=parser, database_path=tmp_path / "idx.sqlite3")
    service.append_frames([(str(i + 1), t, raw) for i, (t, raw, _) in enumerate(frames)])
    return service


@pytest.fixture()
def client(tmp_path):
    service = build_service(tmp_path)
    app = create_app(None, service, None)
    return TestClient(app), service, app


def test_replay_via_page_api(client):
    http, service, _ = client
    r = http.post("/api/listener/traces", json={
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC2"},
    })
    assert r.status_code == 200
    report = r.json()
    assert report["mode"] == "replay"
    assert report["flow"]["stage"] == "confirmed"
    assert report["flow"]["ack"]["frame_id"] == 2


def test_replay_validation_422(client):
    http, _, _ = client
    r = http.post("/api/listener/traces", json={"scope": "flow", "feature": {"app_id": "0003"}})
    assert r.status_code == 422
    assert "msg_seq" in r.json()["detail"]


def test_live_register_snapshot_stop(client):
    """live 全生命周期：注册 → 钩子驱动增量 → 快照 → 停止。"""
    http, service, app = client
    trace_service = app.state.trace_service
    r = http.post("/api/listener/traces", json={
        "scope": "round",
        "window": {"mode": "live"},
        "feature": {"app_id": "0003"},
    })
    assert r.status_code == 200
    handle = r.json()
    assert handle["status"] == "live"
    trace_id = handle["trace_id"]
    start_id = handle["start_frame_id"]
    assert start_id == 3  # 注册时索引已有 3 帧

    # 模拟串口钩子：注册后再入库一轮同序号流（新帧摘要需登记进假解析器）
    new_frames = [
        down645(0x1EC9, [ADDR_A], t="10:05:00.000"),
        up645(0x1EC9, ADDR_A, t="10:05:01.000"),
    ]
    for _, raw, summary in new_frames:
        service.parser._by_hex[raw.replace(" ", "").upper()] = summary
    records = [(str(100 + i), t, raw) for i, (t, raw, _) in enumerate(new_frames)]
    results = service.append_frames(records)
    trace_service.on_frames_appended(results[-1][0])

    snap = http.get(f"/api/listener/traces/{trace_id}").json()
    assert snap["mode"] == "live"
    assert snap["summary"]["rounds"] == 1
    assert snap["summary"]["full_chain"] == 1
    seqs = [f["msg_seq"] for rd in snap["rounds"] for f in rd["flows"]]
    assert seqs == ["0x1EC9"]  # 只含注册后入库的帧

    # 停止后快照冻结、列表可见
    assert http.delete(f"/api/listener/traces/{trace_id}").json()["status"] == "stopped"
    listing = http.get("/api/listener/traces").json()["traces"]
    assert [t["status"] for t in listing if t["trace_id"] == trace_id] == ["stopped"]
    assert http.get("/api/listener/traces/tr-missing").status_code == 404


def test_live_validation_422(client):
    http, _, _ = client
    r = http.post("/api/listener/traces", json={
        "window": {"mode": "live"}, "feature": {"app_id": "0003", "msg_seq": "XXXX"},
    })
    assert r.status_code == 422


def test_trace_service_unavailable_503(tmp_path):
    app = create_app(None, None, None)
    http = TestClient(app)
    assert http.post("/api/listener/traces", json={}).status_code == 503
    assert http.get("/api/listener/traces").status_code == 503
    assert http.get("/api/listener/traces/tr-x").status_code == 503


def test_frame_detail_feature_hint(client):
    """frames/{id} 响应带 feature_hint：0003 下行帧反推出序号+目标地址。"""
    http, _, _ = client
    frame = http.get("/api/logs/frames/1").json()
    hint = frame["feature_hint"]
    assert hint is not None
    assert hint["feature"]["app_id"] == "0003"
    assert hint["feature"]["msg_seq"] == "1EC2"
    assert hint["response_policy"]["expect_meters"] == [DISPLAY_A]
    # ACK 帧（无应用层）hint 为 None
    ack_frame = http.get("/api/logs/frames/2").json()
    assert ack_frame["feature_hint"] is None


def test_serial_hook_invoked_on_ingest(tmp_path):
    """SerialCaptureService 批量入库后回调 on_frames_appended(last_frame_id)。"""
    frames = _frames()
    parser = FakeParser({raw: summary for _, raw, summary in frames})
    service = LogFileService(parser=parser, database_path=tmp_path / "idx.sqlite3")
    trace_service = TraceService(service)
    calls = []

    class HookSerial(SerialCaptureService):
        """绕过真实串口：仅复用 _ingest_batch。"""

        def __init__(self, log_service):
            # 不调用父类 __init__（避免 pyserial/端口目录副作用），仅补齐所需属性
            import threading
            self.log_service = log_service
            self.on_frames_appended = calls.append
            self._sequence = 0
            self._lock = threading.Lock()
            self._status = {}
            self._minute_state = None
            self._buffer = bytearray()
            self._log_file = None
            self._log_day = None

    serial = HookSerial(service)
    serial._ingest_batch([bytes.fromhex(frames[0][1].replace(" ", ""))])
    assert calls and calls[0] == 1
    # 钩子异常不影响采集主链路
    serial.on_frames_appended = lambda _: (_ for _ in ()).throw(RuntimeError("boom"))
    serial._ingest_batch([bytes.fromhex(frames[1][1].replace(" ", ""))])
    assert serial._status.get("frame_count") == 2
