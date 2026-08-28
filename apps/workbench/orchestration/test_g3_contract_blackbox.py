from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from test_automation.models import Artifact, Report, ResourceLease, Run, StepResult
from workbench.orchestration.mappers import artifact_to_view, canonical_run_to_view
from workbench.orchestration.store import RunStore
from workbench.orchestration.dto import RunRequest


ROOT = Path(__file__).resolve().parents[3]


def _imports_workbench_models(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == ".models" or node.module.endswith(".orchestration.models"):
                return True
        if isinstance(node, ast.Import):
            if any(alias.name.endswith("orchestration.models") for alias in node.names):
                return True
    return False


def test_execution_layers_have_no_legacy_execution_model_imports():
    for relative in (
        "apps/workbench/orchestration/runner.py",
        "apps/workbench/orchestration/store.py",
        "apps/workbench/api.py",
    ):
        assert not _imports_workbench_models(ROOT / relative)


def test_orchestration_models_contains_no_execution_domain_class_defs():
    trees = [
        ast.parse(path.read_text(encoding="utf-8"))
        for path in (ROOT / "apps/workbench/orchestration").glob("*.py")
    ]
    duplicate_names = {
        "RunInput",
        "Run",
        "RunStep",
        "Assertion",
        "ArtifactInfo",
        "Report",
        "FirmwareInfo",
    }
    defined = {
        node.name
        for tree in trees
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert not (defined & duplicate_names)


def test_store_exposes_no_legacy_projection_write_apis():
    store_methods = {name for name in dir(RunStore) if not name.startswith("_")}
    assert "add_step" not in store_methods
    assert "update_status" not in store_methods


def test_views_never_expose_execution_inputs_errors_or_artifact_paths():
    run = Run(
        id="r",
        case_id="c",
        case_version="1",
        case_fingerprint="a" * 64,
        parameters={"secret": "do-not-expose"},
        resource_leases=[ResourceLease("serial_port", "COM19", "r", False)],
        error="/private/secret",
    )
    run_view = canonical_run_to_view(run).model_dump()
    assert not {"parameters", "resource_leases", "error", "report_path"} & run_view.keys()

    artifact_view = artifact_to_view(
        Artifact(run_id="r", type="log", name="x.log", sha256="b" * 64, path="/private/x.log")
    ).model_dump()
    assert "path" not in artifact_view


def test_skipped_step_detail_survives_legacy_report_projection(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    run = Run(id="r", case_id="c", case_version="1", case_fingerprint="a" * 64)
    store.create_run(run)
    report = Report(
        run_id="r",
        summary={"verdict": "pass"},
        steps=[StepResult(stage="stimulus", adapter="fake", status="skipped")],
        assertions=[],
        evidence_index={},
        artifacts=[],
    )
    store.save_report("r", report)
    payload = store.get_report("r")
    assert payload["steps"][0]["result"] == "skipped"
    assert payload["steps"][0]["detail"] == "skipped"
    store.close()


def test_api_unknown_run_start_error_is_stable_and_does_not_leak_exception(monkeypatch):
    import workbench.api as api

    class ExplodingExecutor:
        def submit(self, _request):
            raise RuntimeError("/private/secret/path")

    monkeypatch.setattr(api, "_executor", lambda: ExplodingExecutor())
    from workbench.app import create_workbench_app

    response = TestClient(create_workbench_app(mount_listener=False)).post(
        "/api/run", json={"scenario_id": "join_anhui"}
    )
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "run_start_failed"
    assert body["message"] == "Run 启动失败"
    assert "/private/secret/path" not in response.text


def test_api_artifact_unavailable_never_exposes_path_or_path_field(tmp_path, monkeypatch):
    """不可访问 Artifact 的列表/下载黑盒响应不泄露 path 或内部路径。"""
    import workbench.api as api_mod
    from workbench.app import create_workbench_app

    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    run = Run(id="r-private", case_id="c", case_version="1", case_fingerprint="a" * 64)
    store.create_run(run)
    private_path = "/private/secret/x.log"
    store.save_report(
        run.id,
        Report(
            run_id=run.id,
            artifacts=[
                Artifact(
                    id="r-private-art-1",
                    run_id=run.id,
                    type="log",
                    name="x.log",
                    sha256="b" * 64,
                    path=private_path,
                )
            ],
        ),
    )

    class Executor:
        def __init__(self):
            self.store = store

    monkeypatch.setattr(api_mod, "_executor", lambda: Executor())
    client = TestClient(create_workbench_app(mount_listener=False))

    listed = client.get(f"/api/run/{run.id}/artifacts")
    assert listed.status_code == 200
    assert "path" not in listed.json()[0]
    assert private_path not in listed.text

    downloaded = client.get(f"/api/run/{run.id}/artifacts/r-private-art-1")
    assert downloaded.status_code == 404
    assert "path" not in downloaded.text
    assert private_path not in downloaded.text
    assert downloaded.json()["detail"] == "Artifact 不可访问"
    store.close()


def test_api_create_run_canonical_miss_returns_controlled_error(monkeypatch):
    import workbench.api as api_mod

    class Store:
        def get_canonical_run(self, _run_id):
            return None

    class Executor:
        store = Store()

        def submit(self, _request):
            return Run(id="r-missing", case_id="case", case_version="1",
                       case_fingerprint="a" * 64)

    monkeypatch.setattr(api_mod, "_executor", lambda: Executor())
    import asyncio
    with pytest.raises(Exception) as caught:
        asyncio.run(api_mod.create_run(RunRequest(scenario_id="case")))
    error = caught.value
    assert getattr(error, "status_code", None) == 500
    detail = getattr(error, "detail", {})
    assert detail["code"] == "canonical_run_unavailable"
    assert "parameters_json" not in str(detail)
    assert "/private" not in str(detail)


def test_api_get_run_canonical_miss_returns_controlled_not_found(monkeypatch):
    import workbench.api as api_mod

    class Store:
        def get_run(self, _run_id):
            raise AssertionError("legacy get_run must not be used")

        def get_canonical_run(self, _run_id):
            return None

    class Executor:
        store = Store()

    monkeypatch.setattr(api_mod, "_executor", lambda: Executor())
    import asyncio
    with pytest.raises(Exception) as caught:
        asyncio.run(api_mod.get_run("r-missing"))
    error = caught.value
    assert getattr(error, "status_code", None) == 404
    assert getattr(error, "detail", "") == "Run 不存在：r-missing"
    assert "report_path" not in str(error.detail)


def test_api_list_runs_canonical_miss_returns_safe_empty_result(monkeypatch):
    import workbench.api as api_mod

    class Store:
        def list_runs(self, _limit):
            return [{
                "run_id": "r-missing",
                "parameters_json": "/private/secret",
                "error": "/private/error",
                "report_path": "/private/report.json",
            }]

        def get_canonical_run(self, _run_id):
            return None

    class Executor:
        store = Store()

    monkeypatch.setattr(api_mod, "_executor", lambda: Executor())
    import asyncio
    result = asyncio.run(api_mod.list_runs())
    assert result == []
    assert "parameters_json" not in str(result)
    assert "/private" not in str(result)
