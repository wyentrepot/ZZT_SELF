from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

import pytest

from test_automation.models import Artifact, AssertionResult, Report, ResourceLease, Run, RunStatus, StepResult
from workbench.orchestration.store import RunStore
from workbench.orchestration.runner import RunExecutor
from workbench.orchestration.mappers import (
    artifact_to_view,
    assertion_result_to_view,
    canonical_report_to_view,
    canonical_run_to_view,
    request_to_execution_context,
)
from workbench.orchestration.dto import RunRequest


def test_run_mapping_preserves_identity_and_audit_fields():
    lease = ResourceLease("serial_port", "COM24", "run-1", False)
    run = Run(id="run-1", case_id="case-a", case_version="1.2.3", case_fingerprint="f" * 64,
              status=RunStatus.RUNNING, parameters={"x": 1}, resource_leases=[lease], error="warning")
    view = canonical_run_to_view(run)
    assert view.run_id == view.id == "run-1"
    assert view.scenario_id == view.case_id == "case-a"
    assert view.case_version == "1.2.3"
    assert view.case_fingerprint == "f" * 64
    assert view.parameters == {"x": 1}
    assert view.resource_leases[0]["resource_id"] == "COM24"
    assert view.error == "warning"


def test_request_maps_to_execution_context_without_becoming_run():
    req = RunRequest(scenario_id="case-a", parameters={"x": 1})
    context = request_to_execution_context(req)
    assert context["case_id"] == "case-a"
    assert context["parameters"] == {"x": 1}
    assert "run_id" not in context


def test_assertion_and_artifact_mapping_preserves_result_outcome_and_fields():
    result = AssertionResult(run_id="run-1", assertion_id="a1", outcome="pass", expected=1, actual=1,
                             evidence_ids=["run-1-ev-1"], message="ok")
    assertion = assertion_result_to_view(result)
    assert assertion.id == "a1"
    assert assertion.result == assertion.outcome == "pass"
    assert assertion.evidence_ids == ["run-1-ev-1"]
    artifact = Artifact(run_id="run-1", type="log", name="a.log", sha256="a" * 64,
                        path="/tmp/a.log")
    view = artifact_to_view(artifact)
    assert view.id == artifact.id
    assert view.run_id == "run-1"
    assert view.sha256 == artifact.sha256
    assert view.path == "/tmp/a.log"


def test_report_mapping_preserves_evidence_and_artifacts():
    report = Report(run_id="run-1", summary={"passed": 1}, steps=[StepResult(stage="monitor", adapter="fake")],
                    assertions=[AssertionResult(run_id="run-1", assertion_id="a", outcome="inconclusive")],
                    evidence_index={"run-1-ev-1": {"kind": "frame"}},
                    artifacts=[Artifact(run_id="run-1", type="log", name="a", sha256="b" * 64)])
    view = canonical_report_to_view(report)
    assert view.evidence_index == report.evidence_index
    assert view.artifacts[0].sha256 == "b" * 64
    assert view.assertions[0].result == "inconclusive"

def test_artifact_size_roundtrip():
    artifact = Artifact(run_id="r", type="log", name="x", sha256="a" * 64, size=12)
    assert artifact_to_view(artifact).size == 12


def test_old_schema_gets_additive_audit_columns(tmp_path):
    db = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, scenario_id TEXT, status TEXT, firmware_ver TEXT, firmware_commit TEXT, created_at TEXT, updated_at TEXT, report_path TEXT)")
        conn.execute("INSERT INTO runs VALUES ('old-1','case-old','failed',NULL,NULL,'2026-01-01','2026-01-01',NULL)")
    store = RunStore(db_path=db, reports_dir=tmp_path / "reports")
    cols = {row[1] for row in store._conn.execute("PRAGMA table_info(runs)")}
    assert {"case_id", "case_version", "case_fingerprint", "parameters_json", "resource_leases_json", "started_at", "finished_at", "error", "firmware_json"} <= cols
    old = store.get_run("old-1")
    assert old["case_version"] == "legacy"
    assert old["case_fingerprint"] == "legacy-unavailable"
    store.close()


def test_legacy_report_view_reconstructs_old_summary_fields():
    report = Report(run_id="r", summary={
        "firmware": {"version": "1"}, "scenario": "case",
        "sources": {"module_log": {"events": 1}}, "flow_compare": {"verdict": "pass"},
        "feedback": [], "verdict": "pass", "ts": "2026-01-01",
        "evidence_detail": {}, "evidence_frozen": True,
    }, steps=[], assertions=[], evidence_index={}, artifacts=[])
    view = canonical_report_to_view(report)
    dumped = view.model_dump()
    assert dumped["sources"]["module_log"]["events"] == 1
    assert dumped["flow_compare"]["verdict"] == "pass"
    assert dumped["evidence_frozen"] is True


def test_store_roundtrips_canonical_run_audit_fields(tmp_path):
    lease = ResourceLease("serial_port", "COM24", "run-c", False)
    run = Run(id="run-c", case_id="case-c", case_version="1.0.0", case_fingerprint="c" * 64,
              status=RunStatus.RUNNING, parameters={"firmware": {"version": "v"}},
              resource_leases=[lease], error="e")
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    store.create_run(run)
    loaded = store.get_canonical_run("run-c")
    assert loaded.case_id == "case-c"
    assert loaded.case_version == "1.0.0"
    assert loaded.case_fingerprint == "c" * 64
    assert loaded.parameters["firmware"]["version"] == "v"
    assert loaded.resource_leases[0].resource_id == "COM24"
    assert loaded.error == "e"
    store.close()


def test_runner_store_receives_canonical_run(tmp_path):
    from test_automation.ports import MonitorResult, StimulusResult
    class Monitor:
        def scan(self, request):
            return MonitorResult()
    class Stimulus:
        def execute(self, request):
            return StimulusResult(payload=None)
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    captured = []
    original = store.create_run
    def capture(run):
        captured.append(run)
        return original(run)
    store.create_run = capture
    ex = RunExecutor(store, monitor_port=Monitor(), stimulus_port=Stimulus())
    request = RunRequest(scenario_id="join_anhui", log_dir=str(tmp_path), skip_flash=True, skip_stimulus=True, skip_compare=True, skip_feedback=True)
    ex.execute(request, scenarios_dir=Path(__file__).parents[1] / "scenarios")
    assert captured and isinstance(captured[0], Run)
    store.close()


def test_scenario_fingerprint_changes_with_effective_scenario_content(tmp_path):
    from workbench.orchestration.runner import _canonical_run
    from workbench.orchestration.models import FirmwareInfo, Run as LegacyRun
    base = {"id": "case", "version": "1.0.0", "expected_flow": [], "monitor": {}, "stimulus": {}}
    request = RunRequest(scenario_id="case", log_dir=str(tmp_path))
    legacy = LegacyRun(run_id="r", scenario_id="case", firmware=FirmwareInfo())
    first = _canonical_run(legacy, request, base)
    changed = {**base, "expected_flow": [{"step": "x"}]}
    second = _canonical_run(legacy, request, changed)
    assert first.case_fingerprint != second.case_fingerprint


def test_store_rejects_legacy_write_model(tmp_path):
    from workbench.orchestration.models import Run as LegacyRun
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    with pytest.raises(TypeError, match="canonical"):
        store.create_run(LegacyRun(run_id="r", scenario_id="s"))
    store.close()


def test_canonical_report_contains_step_assertion_artifact_and_legacy_summary(tmp_path):
    from test_automation.models import AssertionResult, Artifact, Report, StepResult
    report = Report(run_id="r", summary={"verdict": "pass", "sources": {}, "flow_compare": {}, "feedback": []},
                    steps=[StepResult(stage="monitor", adapter="fake")],
                    assertions=[AssertionResult(run_id="r", assertion_id="a", outcome="pass")],
                    evidence_index={}, artifacts=[Artifact(run_id="r", type="log", name="x", sha256="a" * 64, size=3)])
    view = canonical_report_to_view(report)
    assert view.steps and view.assertions and view.artifacts[0].size == 3
    assert view.sources == {} and view.flow_compare == {}


def test_canonical_run_lifecycle_is_persisted(tmp_path):
    from datetime import datetime, timezone
    from test_automation.models import Run
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    run = Run(id="r", case_id="c", case_version="1", case_fingerprint="a" * 64,
              status=RunStatus.PASSED, started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc))
    store.create_run(run)
    loaded = store.get_canonical_run("r")
    assert loaded.status == RunStatus.PASSED and loaded.started_at and loaded.finished_at
    store.close()


def test_fake_execution_store_receives_canonical_run_and_report(tmp_path):
    from test_automation.models import Report as CanonicalReport, Run as CanonicalRun
    from test_automation.ports import MonitorResult, StimulusResult
    class Monitor:
        def scan(self, request):
            return MonitorResult(files=[str(tmp_path / "e.log")], events=[{"type": "join", "label": "ok"}], total_lines=1)
    class Stimulus:
        def execute(self, request):
            return StimulusResult(payload=None)
    (tmp_path / "e.log").write_text("e", encoding="utf-8")
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    runs, reports = [], []
    original_create, original_save = store.create_run, store.save_report
    def capture_run(run):
        runs.append(run)
        return original_create(run)
    def capture_report(run_id, report):
        reports.append(report)
        return original_save(run_id, report)
    store.create_run, store.save_report = capture_run, capture_report
    ex = RunExecutor(store, monitor_port=Monitor(), stimulus_port=Stimulus())
    request = RunRequest(scenario_id="join_anhui", log_dir=str(tmp_path), skip_flash=True, skip_stimulus=True, skip_compare=True, skip_feedback=True)
    ex.execute(request, scenarios_dir=Path(__file__).parents[1] / "scenarios")
    assert isinstance(runs[0], CanonicalRun)
    assert runs[0].started_at and runs[0].finished_at and runs[0].status in RunStatus
    assert isinstance(reports[0], CanonicalReport)
    assert reports[0].steps and reports[0].assertions and reports[0].artifacts
    assert reports[0].artifacts[0].size == 1
    assert {"firmware", "scenario", "sources", "flow_compare", "feedback", "verdict", "ts", "evidence_detail", "evidence_frozen"} <= reports[0].summary.keys()
    store.close()
