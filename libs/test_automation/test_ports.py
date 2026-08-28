from pathlib import Path

import pytest

from test_automation.ports import (
    MonitorPort,
    MonitorRequest,
    MonitorResult,
    PortError,
    StimulusPort,
    StimulusRequest,
    StimulusResult,
)


def test_monitor_request_is_frozen_and_has_stable_shape(tmp_path: Path):
    req = MonitorRequest(log_dir=tmp_path, rules=["common"], run_id="run-1")
    assert req.log_dir == tmp_path
    assert req.rules == ["common"]
    assert req.run_id == "run-1"
    with pytest.raises((AttributeError, TypeError)):
        req.run_id = "other"


def test_monitor_result_defaults_are_value_only():
    result = MonitorResult()
    assert result.files == []
    assert result.events == []
    assert result.evidence == []
    assert result.summary == {}
    assert result.drift is False
    assert result.drift_list == []
    assert result.total_lines == 0
    assert result.unmatched == []


def test_stimulus_contract_and_port_error():
    req = StimulusRequest(task={"steps": []}, task_file=None, resource_id="serial/COM24")
    assert req.resource_id == "serial/COM24"
    assert StimulusResult(payload={"ok": True}).payload["ok"] is True
    err = PortError("PORT_FAILED", "failed", {"stage": "stimulus"})
    assert err.code == "PORT_FAILED"
    assert err.message == "failed"
    assert err.details == {"stage": "stimulus"}
    assert str(err) == "failed"


def test_protocols_expose_required_methods():
    assert hasattr(MonitorPort, "scan")
    assert hasattr(StimulusPort, "execute")