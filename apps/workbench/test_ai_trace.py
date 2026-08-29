"""AI 控制面通信流追踪接口测试（/api/ai/v1/listener/traces，全假实现/fixture 库）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from listener.log_service import LogFileService
from listener.trace_service import TraceService
from workbench.ai_auth import AuthorizationStore
from workbench.app import create_workbench_app
from workbench.test_ai_operations import FakeModuleService
from listener.test_trace_api import FakeParser, build_service


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def isolated_ai_control_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    return tmp_path


def _listener_factory(trace_service):
    def factory():
        app = FastAPI()
        app.state.module_serial_service = None
        app.state.log_service = trace_service.log_service if trace_service else None
        app.state.trace_service = trace_service
        app.state.serial_service = None
        return app
    return factory


def _plain_listener_factory():
    def factory():
        app = FastAPI()
        app.state.module_serial_service = None
        return app
    return factory


def _client(tmp_path, *, with_trace=True, scopes=None):
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=scopes or ["listener:trace", "evidence:read", "status:read"],
        resources=["*"], ttl_seconds=60, created_by="human",
    )
    trace_service = None
    if with_trace:
        log_service = build_service(tmp_path / "idx.sqlite3")
        trace_service = TraceService(log_service)
    app = create_workbench_app(
        module_log_factory=lambda: FastAPI(),
        listener_factory=(_listener_factory(trace_service) if with_trace
                          else _plain_listener_factory),
        ai_auth_store=auth,
    )
    return TestClient(app), token, auth


REPLAY_FEATURE = {"scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC2"}}
LIVE_FEATURE = {"scope": "round", "window": {"mode": "live"}, "feature": {"app_id": "0003"}}


def test_create_requires_bearer(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.post("/api/ai/v1/listener/traces", json=REPLAY_FEATURE).status_code == 401


def test_create_missing_scope_403(tmp_path):
    client, token, _ = _client(tmp_path, scopes=["evidence:read"])
    r = client.post("/api/ai/v1/listener/traces", json=REPLAY_FEATURE,
                    headers=_auth_header(token))
    assert r.status_code == 403
    assert "listener:trace" in r.json()["detail"]


def test_missing_trace_service_503(tmp_path):
    client, token, _ = _client(tmp_path, with_trace=False)
    r = client.post("/api/ai/v1/listener/traces", json=REPLAY_FEATURE,
                    headers=_auth_header(token))
    assert r.status_code == 503


def test_replay_async_flow_and_audit(tmp_path):
    client, token, auth = _client(tmp_path)
    headers = _auth_header(token)
    r = client.post("/api/ai/v1/listener/traces", json=REPLAY_FEATURE, headers=headers)
    assert r.status_code == 202
    operation_id = r.json()["operation_id"]

    waited = client.get(f"/api/ai/v1/operations/{operation_id}/wait?timeout_seconds=5",
                        headers=headers)
    assert waited.status_code == 200
    body = waited.json()
    assert body["state"] == "succeeded"
    report = body["result"]["report"]
    assert body["result"]["mode"] == "replay"
    assert report["flow"]["stage"] == "confirmed"

    # 结果读取复用 evidence:read scope
    got = client.get(f"/api/ai/v1/operations/{operation_id}", headers=headers)
    assert got.status_code == 200

    # 审计落账
    audit = client.get("/api/ai/v1/audit", headers=headers).json()["entries"]
    actions = [e["action"] for e in audit]
    assert "listener.trace" in actions


def test_live_flow_register_snapshot(tmp_path):
    client, token, _ = _client(tmp_path)
    headers = _auth_header(token)
    r = client.post("/api/ai/v1/listener/traces", json=LIVE_FEATURE, headers=headers)
    assert r.status_code == 202
    operation_id = r.json()["operation_id"]
    waited = client.get(f"/api/ai/v1/operations/{operation_id}/wait?timeout_seconds=5",
                        headers=headers)
    assert waited.json()["state"] == "succeeded"
    result = waited.json()["result"]
    assert result["mode"] == "live"
    trace_id = result["trace"]["trace_id"]
    assert result["snapshot"]["mode"] == "live"

    # 快照读取（evidence:read）
    snap = client.get(f"/api/ai/v1/listener/traces/{trace_id}", headers=headers)
    assert snap.status_code == 200
    assert snap.json()["trace_id"] == trace_id

    listing = client.get("/api/ai/v1/listener/traces", headers=headers)
    assert [t["trace_id"] for t in listing.json()["traces"]] == [trace_id]


def test_invalid_feature_422(tmp_path):
    client, token, _ = _client(tmp_path)
    r = client.post("/api/ai/v1/listener/traces",
                    json={"scope": "flow", "feature": {"app_id": "0003"}},
                    headers=_auth_header(token))
    assert r.status_code == 422


def test_idempotent_replay_same_operation(tmp_path):
    client, token, _ = _client(tmp_path)
    headers = _auth_header(token)
    feature = {**REPLAY_FEATURE, "client_request_id": "req-tr-1"}
    first = client.post("/api/ai/v1/listener/traces", json=feature, headers=headers)
    replay = client.post("/api/ai/v1/listener/traces", json=feature, headers=headers)
    assert first.status_code == 202 and replay.status_code == 202
    assert replay.json()["operation_id"] == first.json()["operation_id"]


def test_trace_unknown_404(tmp_path):
    client, token, _ = _client(tmp_path)
    r = client.get("/api/ai/v1/listener/traces/tr-missing", headers=_auth_header(token))
    assert r.status_code == 404
