"""AI 控制面模拟集中器接口测试（/api/ai/v1/simcon/*，全假实现）。"""
from __future__ import annotations

import threading

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from workbench.ai_auth import AuthorizationStore
from workbench.app import create_workbench_app
from workbench.test_ai_operations import FakeModuleService


class FakeSimconCore:
    """模拟 simcon 执行核心（与 module_log 提升的访问器同构）。"""

    def __init__(self):
        self.verify_calls = []
        self.step_calls = []
        self._slow = threading.Event()
        self._frames = {
            "session_id": "sc-1",
            "entries": [{"seq": 1, "dir": "tx", "afn": "00", "fn": "F1",
                         "updown": "down", "run_id": "run-x", "kind": "step_send",
                         "frame_hex": "6800"}],
            "next_after_seq": 1, "matched_total": 1, "has_more": False,
            "counts": {"tx": 1, "rx": 0, "uplink": 0},
        }

    def verify(self, task):
        self.verify_calls.append(task)
        if task.get("slow"):
            assert self._slow.wait(timeout=5)
        return {"task_id": task.get("id", "t"), "summary": {
            "verdict": "pass", "total": 1, "pass": 1, "fail": 0,
        }, "run_id": "run-x", "session_id": "sc-1", "frames_seq": [1, 2]}

    def step(self, payload):
        self.step_calls.append(payload)
        if payload.get("boom"):
            raise RuntimeError("串口 COM19 已被后端会话占用")
        if payload.get("bad"):
            raise ValueError("send 缺 afn")
        return {"run_id": "manual-1", "step": {"result": "pass"}, "session_id": "sc-1"}

    def frames(self, **filters):
        if filters.get("session_id") == "missing":
            raise LookupError("当前没有帧日志会话")
        return dict(self._frames)

    def session(self):
        return {"current": {"session_id": "sc-1"}, "sessions": []}

    def open(self, spec=None):
        return {"open": True, "session_id": "sc-1"}

    def close(self):
        return {"open": False}


def _simcon_module_factory(core):
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    app.state.simcon_run_verify = core.verify
    app.state.simcon_run_step = core.step
    app.state.simcon_frames = core.frames
    app.state.simcon_session = core.session
    app.state.simcon_open = core.open
    app.state.simcon_close_io = core.close
    return app


def _plain_module_factory():
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    return app


def _listener_factory():
    return FastAPI()


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def isolated_ai_control_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))


def _client(core=None, scopes=None, resources=None):
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=scopes or ["simcon:verify", "simcon:send", "simcon:read", "evidence:read"],
        resources=resources or ["*"], ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=(_plain_module_factory if core is None
                            else lambda: _simcon_module_factory(core)),
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )
    return TestClient(app), token, auth


class TestAuthz:
    def test_requires_bearer(self):
        core = FakeSimconCore()
        client, _, _ = _client(core)
        assert client.post("/api/ai/v1/simcon/step", json={"send": {"afn": 0}}).status_code == 401

    def test_resource_scoped_denial(self):
        core = FakeSimconCore()
        client, token, _ = _client(core, resources=["cco-main"])
        r = client.post("/api/ai/v1/simcon/step", json={"send": {"afn": 0}},
                        headers=_auth_header(token))
        assert r.status_code == 403

    def test_missing_simcon_service_returns_503(self):
        client, token, _ = _client(None)
        r = client.post("/api/ai/v1/simcon/step", json={"send": {"afn": 0}},
                        headers=_auth_header(token))
        assert r.status_code == 503


class TestVerifyEndpoint:
    def test_verify_async_flow(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        r = client.post("/api/ai/v1/simcon/verify", json={"id": "t1", "steps": []},
                        headers=_auth_header(token))
        assert r.status_code == 202
        operation_id = r.json()["operation_id"]

        waited = client.get(f"/api/ai/v1/operations/{operation_id}/wait?timeout_seconds=5",
                            headers=_auth_header(token))
        assert waited.status_code == 200
        assert waited.json()["state"] == "succeeded"
        assert waited.json()["result"]["run_id"] == "run-x"
        assert core.verify_calls[0]["id"] == "t1"

    def test_concurrent_verify_rejected_409(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        first = client.post("/api/ai/v1/simcon/verify",
                            json={"id": "slow", "slow": True, "steps": []},
                            headers=_auth_header(token))
        assert first.status_code == 202
        second = client.post("/api/ai/v1/simcon/verify", json={"id": "t2", "steps": []},
                             headers=_auth_header(token))
        assert second.status_code == 409
        core._slow.set()
        client.get(f"/api/ai/v1/operations/{first.json()['operation_id']}/wait?timeout_seconds=5",
                   headers=_auth_header(token))

    def test_idempotent_replay_same_operation(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        first = client.post("/api/ai/v1/simcon/verify",
                            json={"id": "t1", "client_request_id": "req-1", "steps": []},
                            headers=headers)
        replay = client.post("/api/ai/v1/simcon/verify",
                             json={"id": "t1", "client_request_id": "req-1", "steps": []},
                             headers=headers)
        assert replay.json()["operation_id"] == first.json()["operation_id"]


class TestStepEndpoint:
    def test_step_success_and_audit(self):
        core = FakeSimconCore()
        client, token, auth = _client(core, scopes=["simcon:send", "status:read"])
        r = client.post("/api/ai/v1/simcon/step",
                        json={"send": {"afn": "00", "fn": 1, "params": {}}},
                        headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["result"]["step"]["result"] == "pass"

        audit = client.get("/api/ai/v1/audit", headers=_auth_header(token)).json()
        assert any(entry["action"] == "simcon.step" for entry in audit["entries"])

    def test_step_validation_error_422(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        r = client.post("/api/ai/v1/simcon/step", json={"bad": True},
                        headers=_auth_header(token))
        assert r.status_code == 422

    def test_step_serial_busy_409(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        r = client.post("/api/ai/v1/simcon/step", json={"boom": True},
                        headers=_auth_header(token))
        assert r.status_code == 409

    def test_step_idempotent_replay(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        body = {"send": {"afn": "00", "fn": 1, "params": {}}, "client_request_id": "s-1"}
        first = client.post("/api/ai/v1/simcon/step", json=body, headers=headers)
        replay = client.post("/api/ai/v1/simcon/step", json=body, headers=headers)
        assert first.json()["operation_id"] == replay.json()["operation_id"]
        assert len(core.step_calls) == 1


class TestReadEndpoints:
    def test_frames_passthrough(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        r = client.get("/api/ai/v1/simcon/frames", params={"direction": "tx", "afn": "00"},
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["counts"]["tx"] == 1

    def test_frames_without_session_404(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        r = client.get("/api/ai/v1/simcon/frames", params={"session_id": "missing"},
                       headers=_auth_header(token))
        assert r.status_code == 404

    def test_session_endpoint(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        r = client.get("/api/ai/v1/simcon/session", headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["current"]["session_id"] == "sc-1"


class TestSessionLifecycle:
    def test_open_and_close(self):
        core = FakeSimconCore()
        client, token, _ = _client(core)
        opened = client.post("/api/ai/v1/simcon/open", json={}, headers=_auth_header(token))
        assert opened.status_code == 200
        assert opened.json()["session_id"] == "sc-1"
        closed = client.post("/api/ai/v1/simcon/close", headers=_auth_header(token))
        assert closed.status_code == 200
        assert closed.json()["open"] is False
