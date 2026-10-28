"""REQS-0003 P0-3：v2 job 列表接口 `GET /api/ai/v2/jobs` 测试。

job_id 一丢就没有入口找回最近任务，这里验证列表的过滤、上限与错误映射。
全假实现，不开串口、不碰现网 operations.json。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workbench.app import create_workbench_app


def _plain_app() -> FastAPI:
    return FastAPI()


def _app():
    return create_workbench_app(
        module_log_factory=_plain_app,
        simcon_factory=_plain_app,
        listener_factory=_plain_app,
    )


def _seed(store, *, kind: str, created_at: str, state: str = "created",
          error: str | None = None) -> str:
    operation = store.create(kind, "ai:test", {"marker": "payload 不应出现在列表"})
    if state != "created":
        store.set_state(operation["operation_id"], state, error=error)
    with store._lock:
        store._operations[operation["operation_id"]]["created_at"] = created_at
    return operation["operation_id"]


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = _app()
    return app, TestClient(app)


def test_v2_jobs_list_newest_first_and_summary_only(monkeypatch, tmp_path):
    app, client = _client(monkeypatch, tmp_path)
    store = app.state.ai_control_service.store
    oldest = _seed(store, kind="investigation",
                   created_at="2026-01-01T00:00:01+00:00")
    newest = _seed(store, kind="flash_job", state="succeeded",
                   created_at="2026-01-01T00:00:03+00:00")
    middle = _seed(store, kind="verification_run", state="error", error="模拟失败",
                   created_at="2026-01-01T00:00:02+00:00")

    response = client.get("/api/ai/v2/jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert body["limit"] == 20
    assert [row["operation_id"] for row in body["jobs"]] == [newest, middle, oldest]
    assert [row["job_id"] for row in body["jobs"]] == [
        "job-" + operation_id.removeprefix("op-")
        for operation_id in (newest, middle, oldest)
    ]
    first = body["jobs"][0]
    assert first["state"] == "succeeded"
    assert set(first) == {
        "operation_id", "job_id", "kind", "actor", "state", "created_at",
        "updated_at", "client_request_id", "error",
    }
    assert "payload" not in response.text and "marker" not in response.text
    assert "模拟失败" in response.text  # error 字段仍然可读


def test_v2_jobs_list_filters_kind_state_and_since(monkeypatch, tmp_path):
    app, client = _client(monkeypatch, tmp_path)
    store = app.state.ai_control_service.store
    flash_old = _seed(store, kind="flash_job",
                      created_at="2026-01-01T00:00:01+00:00")
    verify = _seed(store, kind="verification_run", state="succeeded",
                   created_at="2026-01-01T00:00:02+00:00")
    flash_new = _seed(store, kind="flash_job", state="succeeded",
                      created_at="2026-01-01T00:00:03+00:00")

    repeated = client.get("/api/ai/v2/jobs",
                          params=[("kind", "flash_job"), ("kind", "verification_run")])
    comma = client.get("/api/ai/v2/jobs", params={"kind": "flash_job,verification_run"})
    assert repeated.status_code == comma.status_code == 200
    assert {row["operation_id"] for row in repeated.json()["jobs"]} == {
        flash_old, verify, flash_new,
    }
    assert comma.json()["jobs"] == repeated.json()["jobs"]

    only_flash = client.get("/api/ai/v2/jobs", params={"kind": "flash_job"})
    assert {row["operation_id"] for row in only_flash.json()["jobs"]} == {
        flash_old, flash_new,
    }

    only_succeeded = client.get("/api/ai/v2/jobs", params={"state": "succeeded"})
    assert {row["operation_id"] for row in only_succeeded.json()["jobs"]} == {
        verify, flash_new,
    }

    # since 边界：>= 生效，等于 created_at 的那条要留下
    boundary = client.get("/api/ai/v2/jobs",
                          params={"since": "2026-01-01T00:00:02+00:00"})
    assert [row["operation_id"] for row in boundary.json()["jobs"]] == [
        flash_new, verify,
    ]


def test_v2_jobs_list_limit_is_capped_at_100(monkeypatch, tmp_path):
    app, client = _client(monkeypatch, tmp_path)
    store = app.state.ai_control_service.store
    for index in range(120):
        _seed(store, kind="investigation",
              created_at=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00")

    default = client.get("/api/ai/v2/jobs")
    assert default.json()["limit"] == 20
    assert len(default.json()["jobs"]) == 20

    capped = client.get("/api/ai/v2/jobs", params={"limit": 500})
    assert capped.status_code == 200
    assert capped.json()["limit"] == 100
    assert capped.json()["count"] == 100
    assert len(capped.json()["jobs"]) == 100


def test_v2_jobs_list_empty_result(monkeypatch, tmp_path):
    _, client = _client(monkeypatch, tmp_path)

    response = client.get("/api/ai/v2/jobs")

    assert response.status_code == 200
    assert response.json() == {"jobs": [], "count": 0, "limit": 20}


def test_v2_jobs_list_invalid_since_is_422_not_500(monkeypatch, tmp_path):
    _, client = _client(monkeypatch, tmp_path)

    response = client.get("/api/ai/v2/jobs", params={"since": "2026-13-01T00:00:00"})

    assert response.status_code == 422
    assert "ISO" in response.json()["detail"]


def test_v2_jobs_list_rejects_non_positive_limit(monkeypatch, tmp_path):
    _, client = _client(monkeypatch, tmp_path)

    assert client.get("/api/ai/v2/jobs", params={"limit": 0}).status_code == 422


def test_v2_jobs_list_lan_requires_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    client = TestClient(_app(), client=("192.168.1.20", 50001))

    assert client.get("/api/ai/v2/jobs").status_code == 401
