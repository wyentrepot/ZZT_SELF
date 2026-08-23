"""AI HTTP control-plane tests."""
from __future__ import annotations

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from workbench.ai_auth import AuthorizationStore
from workbench.app import create_workbench_app
from workbench.test_ai_operations import FakeListenerLogService, FakeListenerService, FakeModuleService


@pytest.fixture(autouse=True)
def isolated_ai_control_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))


def _module_factory():
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    return app


def _listener_factory():
    return FastAPI()


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_status_requires_bearer_and_returns_shared_backend_session():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["status:read", "evidence:read"], resources=["*"], ttl_seconds=60,
        created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    client = TestClient(app)

    assert client.get("/api/ai/v1/status").status_code == 401
    response = client.get("/api/ai/v1/status", headers=_auth_header(token))
    assert response.status_code == 200
    assert response.json()["module_sessions"][0]["session_id"] == "ms-cco"
    assert response.json()["module_sessions"][0]["log_path"] == "/tmp/cco.log"


def test_resource_scoped_ensure_and_literal_observation():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["module_session:ensure", "observation:create", "evidence:read"],
        resources=["cco-main"], ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    client = TestClient(app)

    denied = client.post(
        "/api/ai/v1/module-sessions/ensure",
        json={"mapping_id": "sta-main", "module": "sta"},
        headers=_auth_header(token),
    )
    assert denied.status_code == 403

    ensured = client.post(
        "/api/ai/v1/module-sessions/ensure",
        json={"mapping_id": "cco-main", "module": "cco"},
        headers=_auth_header(token),
    )
    assert ensured.status_code == 200
    assert ensured.json()["reused"] is True

    created = client.post(
        "/api/ai/v1/observations",
        json={
            "source": "module_log", "target": {"session_id": "ms-cco"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "literal", "value": "marker"},
        },
        headers=_auth_header(token),
    )
    assert created.status_code == 202
    operation_id = created.json()["operation_id"]

    app.state.ai_control_service.module_service.lines.append(
        {"seq": 2, "ts": "t2", "dir": "RX", "text": "marker seen"}
    )
    matched = client.get(
        f"/api/ai/v1/operations/{operation_id}/wait?timeout_seconds=0",
        headers=_auth_header(token),
    )
    assert matched.status_code == 200
    assert matched.json()["state"] == "matched"

def test_operation_read_is_restricted_to_the_authorized_serial_resource():
    auth = AuthorizationStore()
    _, cco_token = auth.create_grant(
        scopes=["observation:create", "evidence:read"], resources=["cco-main"],
        ttl_seconds=60, created_by="human",
    )
    _, sta_token = auth.create_grant(
        scopes=["evidence:read"], resources=["sta-main"],
        ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    client = TestClient(app)
    created = client.post(
        "/api/ai/v1/observations",
        json={
            "source": "module_log", "target": {"session_id": "ms-cco"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "literal", "value": "marker"},
        },
        headers=_auth_header(cco_token),
    )
    operation_id = created.json()["operation_id"]

    response = client.get(
        f"/api/ai/v1/operations/{operation_id}", headers=_auth_header(sta_token),
    )
    assert response.status_code == 403

def test_audit_api_never_echoes_bearer_token():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["module_session:ensure", "status:read"], resources=["cco-main"],
        ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    client = TestClient(app)
    client.post(
        "/api/ai/v1/module-sessions/ensure",
        json={"mapping_id": "cco-main", "module": "cco"},
        headers=_auth_header(token),
    )

    response = client.get("/api/ai/v1/audit", headers=_auth_header(token))
    assert response.status_code == 200
    assert any(
        item["action"] == "module_session.ensure" for item in response.json()["entries"]
    )
    assert token not in response.text

def _listener_factory_with_versioned_index():
    app = FastAPI()
    app.state.serial_service = FakeListenerService()
    app.state.log_service = FakeListenerLogService()
    return app


def test_listener_frame_detail_uses_index_and_frame_composite_key():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["evidence:read"], resources=["listener-main"], ttl_seconds=60,
        created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory_with_versioned_index,
        ai_auth_store=auth,
    )
    client = TestClient(app)

    response = client.get(
        "/api/ai/v1/listener/indexes/idx-listener-test/frames/1",
        headers=_auth_header(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["index_id"] == "idx-listener-test"
    assert data["frame_id"] == 1
    assert data["analysis"]["full"]["beacon"]["flag"] == 1


def test_listener_cursor_range_api_returns_artifact_composite_keys_and_honours_idempotency_key():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["observation:create", "evidence:read"], resources=["listener-main"], ttl_seconds=60,
        created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory_with_versioned_index,
        ai_auth_store=auth,
    )
    client = TestClient(app)
    request = {
        "source": "listener",
        "target": {"mapping_id": "listener-main"},
        "window": {
            "type": "cursor_range", "index_id": "idx-listener-test",
            "start_frame_id": 1, "end_frame_id": 3,
        },
        "match": {"kind": "frame_query", "frame_kind": "central_beacon", "selector": "all"},
    }
    headers = {**_auth_header(token), "Idempotency-Key": "listener-cursor-api-1"}

    created = client.post("/api/ai/v1/observations", json=request, headers=headers)
    retried = client.post("/api/ai/v1/observations", json=request, headers=headers)

    assert created.status_code == 202
    assert retried.status_code == 202
    assert retried.json()["operation_id"] == created.json()["operation_id"]
    result = created.json()["result"]
    assert result["condition_met"] is True
    assert result["matches"][-1]["frame_key"] == {
        "index_id": "idx-listener-test", "frame_id": 3,
    }
    artifact = client.get(
        f"/api/ai/v1/artifacts/{result['artifact_id']}/content", headers=_auth_header(token),
    )
    assert artifact.status_code == 200
    assert artifact.json()["content"]["index"]["end_frame_id"] == 3


@pytest.mark.parametrize(
    "window, target, detail",
    [
        (
            {"type": "cursor_range", "index_id": "idx-listener-test", "start_frame_id": 3, "end_frame_id": 1},
            {"mapping_id": "listener-main"}, "start_frame_id 不能大于",
        ),
        (
            {"type": "cursor_range", "index_id": "idx-listener-test", "start_frame_id": 1, "end_frame_id": 501},
            {"mapping_id": "listener-main"}, "范围过大",
        ),
        (
            {"type": "cursor_range", "index_id": "idx-unknown", "start_frame_id": 1, "end_frame_id": 1},
            {"mapping_id": "listener-main"}, "不存在",
        ),
        (
            {"type": "cursor_range", "index_id": "idx-listener-test", "start_frame_id": 1, "end_frame_id": 1},
            {"mapping_id": "listener-main", "index_id": "idx-listener-other"}, "不一致",
        ),
    ],
)
def test_listener_cursor_range_api_maps_invalid_input_to_422(window, target, detail):
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["observation:create"], resources=["listener-main"], ttl_seconds=60,
        created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory_with_versioned_index,
        ai_auth_store=auth,
    )
    client = TestClient(app)

    response = client.post(
        "/api/ai/v1/observations",
        json={
            "source": "listener", "target": target, "window": window,
            "match": {"kind": "frame_query", "frame_kind": "central_beacon"},
        },
        headers=_auth_header(token),
    )

    assert response.status_code == 422
    assert detail in response.json()["detail"]


def _module_cursor_observation(session_id="ms-cco", value="boot"):
    return {
        "source": "module_log", "target": {"session_id": session_id},
        "window": {"mode": "cursor_range", "start_seq": 1, "end_seq": 1},
        "match": {"kind": "literal", "value": value},
    }


def _listener_cursor_observation(selector="all"):
    return {
        "source": "listener", "target": {"mapping_id": "listener-main"},
        "window": {
            "type": "cursor_range", "index_id": "idx-listener-test",
            "start_frame_id": 1, "end_frame_id": 3,
        },
        "match": {"kind": "frame_query", "frame_kind": "central_beacon", "selector": selector},
    }


def test_observation_idempotency_rejects_cross_resource_source_and_body_collisions_without_leakage():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["observation:create", "evidence:read"], resources=["*"], ttl_seconds=60,
        created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory_with_versioned_index,
        ai_auth_store=auth,
    )
    app.state.ai_control_service.module_service.sessions["ms-sta"] = {
        **app.state.ai_control_service.module_service.sessions["ms-cco"],
        "session_id": "ms-sta", "port_identity": {"mapping_id": "sta-main"},
    }
    client = TestClient(app)

    cases = [
        (_module_cursor_observation(), _module_cursor_observation(session_id="ms-sta")),
        (_module_cursor_observation(), _listener_cursor_observation()),
        (_listener_cursor_observation(selector="all"), _listener_cursor_observation(selector="first")),
    ]
    for number, (first_request, conflicting_request) in enumerate(cases):
        headers = {**_auth_header(token), "Idempotency-Key": f"collision-{number}"}
        created = client.post("/api/ai/v1/observations", json=first_request, headers=headers)
        conflict = client.post("/api/ai/v1/observations", json=conflicting_request, headers=headers)

        assert created.status_code == 202
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "幂等键与既有请求不一致"
        assert conflict.json()["code"] == "409"
        assert "operation_id" not in conflict.json()
        assert "result" not in conflict.json()
        assert "artifact_id" not in conflict.text


def test_observation_idempotency_checks_existing_resource_authorization_before_returning_a_replay():
    auth = AuthorizationStore()
    _, cco_token = auth.create_grant(
        scopes=["observation:create", "evidence:read"], resources=["cco-main"], ttl_seconds=60,
        created_by="human",
    )
    _, listener_token = auth.create_grant(
        scopes=["observation:create"], resources=["listener-main"], ttl_seconds=60,
        created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory_with_versioned_index,
        ai_auth_store=auth,
    )
    client = TestClient(app)
    headers = {"Idempotency-Key": "authorization-replay"}

    created = client.post(
        "/api/ai/v1/observations", json=_module_cursor_observation(),
        headers={**headers, **_auth_header(cco_token)},
    )
    denied = client.post(
        "/api/ai/v1/observations", json=_listener_cursor_observation(),
        headers={**headers, **_auth_header(listener_token)},
    )

    assert created.status_code == 202
    assert denied.status_code == 403
    assert "operation_id" not in denied.json()
    assert "artifact_id" not in denied.text


def test_flash_api_requires_scope_and_authorized_firmware_root():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["module_flash:execute", "evidence:read"],
        resources=["cco-main"], ttl_seconds=60, created_by="human",
        firmware_roots=["/tmp/allowed"],
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    client = TestClient(app)

    forbidden = client.post(
        "/api/ai/v1/flash-operations",
        json={"session_id": "ms-cco", "bin_path": "/tmp/outside/fw.bin"},
        headers=_auth_header(token),
    )
    assert forbidden.status_code == 403

    created = client.post(
        "/api/ai/v1/flash-operations",
        json={
            "session_id": "ms-cco", "bin_path": "/tmp/allowed/fw.bin",
            "client_request_id": "flash-api-1",
        },
        headers=_auth_header(token),
    )
    assert created.status_code == 202
    assert created.json()["state"] == "waiting"


def test_matched_observation_exposes_registered_artifact_only_to_authorized_resource():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["observation:create", "evidence:read"], resources=["cco-main"],
        ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    client = TestClient(app)
    created = client.post(
        "/api/ai/v1/observations",
        json={
            "source": "module_log", "target": {"session_id": "ms-cco"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "literal", "value": "artifact marker"},
        },
        headers=_auth_header(token),
    )
    operation_id = created.json()["operation_id"]
    app.state.ai_control_service.module_service.lines.append(
        {"seq": 2, "ts": "t2", "dir": "RX", "text": "artifact marker seen"}
    )
    operation = client.get(
        f"/api/ai/v1/operations/{operation_id}/wait?timeout_seconds=0",
        headers=_auth_header(token),
    ).json()
    artifact_id = operation["result"]["log"]["artifact_id"]
    assert artifact_id.startswith("art-")
    content = client.get(f"/api/ai/v1/artifacts/{artifact_id}/content", headers=_auth_header(token))
    assert content.status_code == 200
    assert content.json()["content"]["snippet"][0]["seq"] == 1


def test_grant_management_requires_local_human_admin_key():
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_admin_key="human-admin-key",
    )
    client = TestClient(app)
    body = {
        "scopes": ["status:read"], "resources": ["cco-main"],
        "ttl_seconds": 60, "reason": "manual diagnosis",
    }
    assert client.post("/api/ai/v1/admin/grants", json=body).status_code == 403
    created = client.post(
        "/api/ai/v1/admin/grants", json=body,
        headers={"X-Workbench-Admin-Key": "human-admin-key"},
    )
    assert created.status_code == 201
    grant_id = created.json()["grant"]["grant_id"]
    bearer = created.json()["token"]
    listing = client.get(
        "/api/ai/v1/admin/grants", headers={"X-Workbench-Admin-Key": "human-admin-key"},
    )
    assert bearer not in listing.text
    revoked = client.post(
        f"/api/ai/v1/admin/grants/{grant_id}/revoke",
        headers={"X-Workbench-Admin-Key": "human-admin-key"},
    )
    assert revoked.status_code == 200
    assert client.get("/api/ai/v1/status", headers=_auth_header(bearer)).status_code == 401

def test_listener_stop_requires_scope_and_can_force_stop_active_observation():
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["listener:stop", "observation:create", "evidence:read"],
        resources=["listener-main"], ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory_with_versioned_index,
        ai_auth_store=auth,
    )
    client = TestClient(app)
    observation = client.post(
        "/api/ai/v1/observations",
        json={
            "source": "listener", "target": {"mapping_id": "listener-main"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "frame_query", "frame_kind": "central_beacon"},
        },
        headers=_auth_header(token),
    )
    assert observation.status_code == 202
    assert client.post("/api/ai/v1/listener/stop", headers=_auth_header(token)).status_code == 409
    stopped = client.post(
        "/api/ai/v1/listener/stop", json={"force": True}, headers=_auth_header(token),
    )
    assert stopped.status_code == 200
    assert stopped.json()["listener"]["state"] == "idle"
