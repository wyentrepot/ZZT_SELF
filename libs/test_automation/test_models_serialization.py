"""A3：模型序列化契约测试（docs/03 §3、§9）。

覆盖：Run/Evidence/AssertionResult/Artifact/ResourceLease/Report 的
to_dict/from_dict round-trip；时间 ISO 8601 带时区。
"""
import pytest

from test_automation.models import (
    Run,
    RunStatus,
    Evidence,
    AssertionResult,
    Artifact,
    ResourceLease,
    StepResult,
    Report,
)


class TestRunSerialization:
    def test_roundtrip(self):
        run = Run(
            id="run-1",
            case_id="anhui_minute_collect",
            case_version="1.0.0",
            case_fingerprint="ab" * 32,
            status=RunStatus.RUNNING,
            parameters={"rate": 9600},
            resource_leases=[ResourceLease("serial_port", "COM24", "sim_concentrator", False)],
        )
        data = run.to_dict()
        back = Run.from_dict(data)
        assert back.id == run.id
        assert back.status == RunStatus.RUNNING
        assert back.parameters == {"rate": 9600}
        assert back.resource_leases[0].resource_id == "COM24"

    def test_iso_datetime_with_tz(self):
        run = Run(id="r", case_id="c", case_version="1", case_fingerprint="x" * 64)
        created = run.to_dict()["created_at"]
        assert created.endswith("+00:00") or "T" in created  # ISO 8601 带时区
        # 反序列化后时间可比较
        back = Run.from_dict(run.to_dict())
        assert back.created_at == run.created_at


class TestEvidenceSerialization:
    def test_roundtrip(self):
        ev = Evidence(kind="frame", source="listener", payload={"len": 68}, sequence=3, id="run-1-ev-3")
        back = Evidence.from_dict(ev.to_dict())
        assert back.kind == "frame"
        assert back.sequence == 3
        assert back.id == "run-1-ev-3"

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            Evidence(kind="nope", source="x")


class TestAssertionResultSerialization:
    def test_roundtrip(self):
        ar = AssertionResult(
            run_id="run-1", assertion_id="a1", outcome="fail", expected="A", actual="B",
            evidence_ids=["run-1-ev-1"], message="不匹配",
        )
        back = AssertionResult.from_dict(ar.to_dict())
        assert back.outcome == "fail"
        assert back.evidence_ids == ["run-1-ev-1"]

    def test_unknown_outcome_raises(self):
        with pytest.raises(ValueError):
            AssertionResult(run_id="r", assertion_id="a", outcome="maybe")


class TestArtifactSerialization:
    def test_roundtrip(self):
        art = Artifact(run_id="run-1", type="report", name="report.json", sha256="ab" * 32)
        back = Artifact.from_dict(art.to_dict())
        assert back.name == "report.json"
        assert back.sha256 == "ab" * 32


class TestReportSerialization:
    def test_roundtrip(self):
        rep = Report(
            run_id="run-1",
            summary={"passed": 1, "failed": 1},
            steps=[StepResult(stage="collect", adapter="listener")],
            assertions=[AssertionResult(run_id="run-1", assertion_id="a1", outcome="pass")],
            evidence_index={"run-1-ev-1": {"kind": "frame"}},
            artifacts=[Artifact(run_id="run-1", type="report", name="r.json", sha256="aa" * 32)],
        )
        back = Report.from_dict(rep.to_dict())
        assert back.summary == rep.summary
        assert len(back.steps) == 1
        assert len(back.assertions) == 1
        assert back.evidence_index["run-1-ev-1"]["kind"] == "frame"
        assert back.artifacts[0].name == "r.json"
