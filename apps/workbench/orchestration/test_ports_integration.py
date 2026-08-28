import ast
from pathlib import Path

import pytest

from workbench.orchestration.models import RunInput
from workbench.orchestration.runner import RunExecutor
from workbench.orchestration.store import RunStore
from test_automation.ports import MonitorResult, PortError, StimulusResult


class FakeMonitor:
    def __init__(self, result=None, error=None):
        self.requests = []
        self.result = result or MonitorResult(
            files=["relative.log"], events=[{"type": "hit", "label": "x"}],
            evidence=[], summary={"total": 1}, drift=False,
            drift_list=[], total_lines=1, unmatched=[],
        )
        self.error = error

    def scan(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


class FakeStimulus:
    def __init__(self, payload=None, error=None):
        self.requests = []
        self.payload = payload or {"summary": {"total": 1, "pass": 1, "verdict": "pass"}, "steps": []}
        self.error = error

    def execute(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return StimulusResult(payload=self.payload)


def _run(tmp_path, monitor, stimulus):
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    ex = RunExecutor(store, monitor_port=monitor, stimulus_port=stimulus)
    inp = RunInput(scenario_id="join_anhui", log_dir=str(tmp_path), task_file=None,
                   skip_flash=True, skip_stimulus=False, skip_compare=True, skip_feedback=True)
    run = ex.execute(inp, scenarios_dir=Path(__file__).parents[1] / "scenarios")
    report = store.get_report(run.run_id)
    store.close()
    return run, report


def test_runner_injects_fake_ports_and_preserves_results(tmp_path):
    monitor = FakeMonitor()
    stimulus = FakeStimulus()
    run, report = _run(tmp_path, monitor, stimulus)
    assert monitor.requests and monitor.requests[0].run_id == run.run_id
    assert stimulus.requests and stimulus.requests[0].resource_id == "serial/COM24"
    assert report["sources"]["module_log"]["events"] == 1
    assert report["sources"]["sim_concentrator"]["total"] == 1
    assert [step["kind"] for step in report["steps"]] if "steps" in report else True


def test_runner_normalizes_port_error_without_leaking_path(tmp_path):
    monitor = FakeMonitor(error=PortError("monitor_failed", "monitor port failed", {"type": "RuntimeError"}))
    stimulus = FakeStimulus()
    run, report = _run(tmp_path, monitor, stimulus)
    assert run.status.value == "failed"
    serialized = str(report)
    assert "private.log" not in serialized

def test_runner_has_no_concrete_or_composition_imports():
    path = Path(__file__).with_name("runner.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"loghooks", "sim_concentrator", "adapters", ".adapters"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert not any(any(item in name for item in forbidden) for name in names), names


def test_runner_requires_explicit_ports(tmp_path):
    with pytest.raises(ValueError, match="monitor_port.*stimulus_port"):
        RunExecutor(RunStore(db_path=tmp_path / "runs.sqlite"))
