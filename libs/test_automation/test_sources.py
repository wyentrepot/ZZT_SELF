"""test_automation 三源 Evidence 适配测试（任务3）。"""
from dataclasses import dataclass, field
from typing import Any

from test_automation.sources import (
    ListenerFrameAdapter,
    SimConcentratorAdapter,
    LoghooksEventAdapter,
    listener_frame_evidence,
    simcon_step_evidence,
    loghooks_event_evidence,
)
from test_automation.models import Evidence


class TestListenerFrameEvidence:
    def test_basic_fields(self):
        ev = listener_frame_evidence("000001", "2026-08-17 10:00:00", "7e 68 01 02 7e", run_id="run-1")
        assert ev.kind == "frame"
        assert ev.source == "listener"
        assert ev.run_id == "run-1"
        assert ev.payload["sequence"] == "000001"
        assert ev.payload["hex_frame"] == "7E 68 01 02 7E"  # 规范化大写
        assert ev.payload["hex_length"] == 5
        assert ev.raw_ref == "listener:seq:000001"

    def test_correlation_key(self):
        ev = listener_frame_evidence(1, None, "7e 7e", correlation_key="k1")
        assert ev.correlation_key == "k1"


class TestSimConStepEvidence:
    def test_basic_fields(self):
        step = {
            "index": 0, "name": "查询路由状态", "sent_hex": "68 01",
            "matched": {"afn": 16}, "result": "pass", "reason": "",
        }
        ev = simcon_step_evidence(step, run_id="run-1", case_id="c1")
        assert ev.kind == "interaction"
        assert ev.source == "sim_concentrator"
        assert ev.payload["name"] == "查询路由状态"
        assert ev.payload["result"] == "pass"
        assert ev.payload["case_id"] == "c1"
        assert ev.correlation_key == "0"


class TestLoghooksEventEvidence:
    def test_basic_fields(self):
        @dataclass
        class FakeEvent:
            type: str = "report"
            label: str = "主动上报"
            message: str = "发现 06H-F230"
            level: str = "info"
            time: str = "10:00:00"
            rule_id: str = "r1"
            category: str = "report"
            source: str = "cco.log"
            source_line: str = "line"
            captures: dict = field(default_factory=dict)
            line_drift: bool = False
            drift_actual: Any = None
            drift_expected: Any = None
            source_line_idx: Any = None

        ev = loghooks_event_evidence(FakeEvent(), run_id="run-1")
        assert ev.kind == "event"
        assert ev.source == "loghooks"
        assert ev.payload["type"] == "report"
        assert ev.payload["label"] == "主动上报"
        assert ev.payload["rule_id"] == "r1"
        assert ev.correlation_key == "r1"


class TestListenerFrameAdapter:
    def test_collect_produces_evidence(self):
        records = [("1", "t1", "7e 01 7e"), ("2", "t2", "7e 02 7e")]
        adapter = ListenerFrameAdapter(records)
        adapter.start({"run_id": "run-1"})
        sink = []
        produced = adapter.collect(sink.append)
        assert len(produced) == 2
        assert all(isinstance(e, Evidence) for e in produced)
        assert all(e.run_id == "run-1" for e in produced)
        assert [e.payload["sequence"] for e in produced] == ["1", "2"]
        assert len(sink) == 2
        adapter.stop()
        assert adapter.health().ok is False


class TestSimConcentratorAdapter:
    def test_collect_produces_evidence(self):
        steps = [
            {"index": 0, "name": "s1", "result": "pass"},
            {"index": 1, "name": "s2", "result": "fail", "reason": "超时"},
        ]
        adapter = SimConcentratorAdapter(steps, case_id="c1")
        adapter.start({"run_id": "run-1"})
        produced = adapter.collect(None)
        assert len(produced) == 2
        assert produced[0].payload["case_id"] == "c1"
        assert produced[1].payload["result"] == "fail"
        assert produced[1].payload["reason"] == "超时"


class TestLoghooksEventAdapter:
    def test_collect_produces_evidence(self):
        @dataclass
        class FakeEvent:
            type: str = "report"
            label: str = "主动上报"
            message: str = "发现 06H-F230"
            level: str = "info"
            time: str = "10:00:00"
            rule_id: str = "r1"
            category: str = "report"
            source: str = "cco.log"
            source_line: str = "line"
            captures: dict = field(default_factory=dict)
            line_drift: bool = False
            drift_actual: Any = None
            drift_expected: Any = None
            source_line_idx: Any = None

        adapter = LoghooksEventAdapter([FakeEvent(), FakeEvent()])
        adapter.start({"run_id": "run-1"})
        produced = adapter.collect(None)
        assert len(produced) == 2
        assert all(e.source == "loghooks" for e in produced)
        assert all(e.run_id == "run-1" for e in produced)
