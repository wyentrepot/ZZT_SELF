"""REQS-0021 P1：v2 capabilities HTTP/OpenAPI/访问边界测试。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from workbench.ai_auth import AuthorizationStore
from workbench.app import create_workbench_app
from workbench.test_ai_operations import CapturingTraceService
from workbench.test_ai_operations import FakeModuleService
from workbench.test_ai_operations import FakeListenerLogService, FakeListenerService


def _module_factory():
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    return app


def _listener_factory():
    return FastAPI()


def _app(*, auth: AuthorizationStore | None = None):
    return create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_auth_store=auth,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_v2_capabilities_local_full_is_typed_and_does_not_leak_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = _app()
    client = TestClient(app)

    response = client.get("/api/ai/v2/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["access"]["zone"] == "local_full"
    assert body["capability_revision"] == "ai-v2-p1"
    assert body["capabilities"] and all(item["allowed"] for item in body["capabilities"])
    assert "token" not in response.text.lower()
    assert "/tmp/cco.log" not in response.text

    operation = app.openapi()["paths"]["/api/ai/v2/capabilities"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CapabilitySnapshot"
    }


def test_v2_loopback_without_flag_requires_a_bearer_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.delenv("WORKBENCH_LOCAL_FULL_ACCESS", raising=False)

    response = TestClient(_app()).get("/api/ai/v2/capabilities")

    assert response.status_code == 401
    assert response.json()["code"] == "401"
    assert {"code", "message", "details", "request_id", "detail"} <= set(response.json())


def test_v2_remote_peer_cannot_spoof_loopback_with_forwarded_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    client = TestClient(_app(), client=("192.168.1.20", 50001))

    response = client.get(
        "/api/ai/v2/capabilities",
        headers={"Host": "localhost", "X-Forwarded-For": "127.0.0.1"},
    )

    assert response.status_code == 401


def test_v2_remote_grant_is_lan_scoped_and_only_lists_authorised_capabilities(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["status:read", "module_session:ensure"], resources=["cco-main"],
        ttl_seconds=60, created_by="human",
    )
    client = TestClient(_app(auth=auth), client=("192.168.1.20", 50001))

    response = client.get("/api/ai/v2/capabilities", headers=_bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body["access"] == {"zone": "lan_scoped", "actor": body["access"]["actor"]}
    assert body["access"]["actor"].startswith("ai:grant-")
    assert {item["name"] for item in body["capabilities"]} == {
        "capabilities.read", "module_actions.ensure"
    }
    assert body["resource_aliases"] == [{"alias": "cco-main", "source": "module_log"}]
    assert set(body["source_health"]) == {"module_log", "listener", "simcon"}
    assert body["source_health"]["module_log"]["available"] is True


def test_v2_remote_grant_cannot_advertise_a_capability_for_the_wrong_resource_type(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["status:read", "simcon:verify"], resources=["cco-main"],
        ttl_seconds=60, created_by="human",
    )
    client = TestClient(_app(auth=auth), client=("192.168.1.20", 50001))

    response = client.get("/api/ai/v2/capabilities", headers=_bearer(token))

    assert response.status_code == 200
    assert {item["name"] for item in response.json()["capabilities"]} == {"capabilities.read"}


def test_v2_resource_aliases_reject_paths_and_physical_serial_handles(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    module_app = _module_factory()
    sessions = module_app.state.module_serial_service.sessions
    sessions["ms-cco"]["port_identity"]["mapping_id"] = r"C:\\secret\\capture.log"
    sessions["ms-tty"] = {
        **sessions["ms-cco"],
        "session_id": "ms-tty",
        "port_identity": {"mapping_id": "COM8"},
    }
    sessions["ms-unix"] = {
        **sessions["ms-cco"],
        "session_id": "ms-unix",
        "port_identity": {"mapping_id": "/var/log/module.log"},
    }
    sessions["ms-unc"] = {
        **sessions["ms-cco"],
        "session_id": "ms-unc",
        "port_identity": {"mapping_id": r"\\server\share\capture.log"},
    }
    sessions["ms-tty-usb"] = {
        **sessions["ms-cco"],
        "session_id": "ms-tty-usb",
        "port_identity": {"mapping_id": "ttyUSB0"},
    }
    app = create_workbench_app(
        module_log_factory=lambda: module_app,
        listener_factory=_listener_factory,
    )

    response = TestClient(app).get("/api/ai/v2/capabilities")

    assert response.status_code == 200
    assert response.json()["resource_aliases"] == []


def test_v2_capabilities_audit_uses_actor_but_never_the_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["status:read"], resources=["cco-main"], ttl_seconds=60, created_by="human",
    )
    app = _app(auth=auth)
    response = TestClient(app, client=("192.168.1.20", 50001)).get(
        "/api/ai/v2/capabilities", headers=_bearer(token),
    )

    assert response.status_code == 200
    entry = app.state.ai_control_service.audit_entries(["*"])[-1]
    assert entry["action"] == "ai_v2.capabilities.read"
    assert entry["actor"].startswith("ai:grant-")
    assert token not in str(entry)


def test_v2_registration_does_not_change_v1_auth_or_human_admin_protection(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(
        module_log_factory=_module_factory,
        listener_factory=_listener_factory,
        ai_admin_key="human-admin-key",
    )
    remote = TestClient(app, client=("192.168.1.20", 50001))

    assert remote.get("/api/ai/v1/status").status_code == 401
    assert remote.post(
        "/api/ai/v1/admin/grants",
        json={"scopes": ["status:read"], "resources": ["cco-main"], "ttl_seconds": 60},
        headers={"X-Workbench-Admin-Key": "human-admin-key"},
    ).status_code == 403


class _ListenerBundle:
    def __init__(self):
        self.serial_service = FakeListenerService()
        self.log_service = FakeListenerLogService()


def _listener_bundle_factory():
    bundle = _ListenerBundle()
    app = FastAPI()
    app.state.serial_service = bundle.serial_service
    app.state.log_service = bundle.log_service
    return app


def _historical_investigation():
    return {
        "observations": [
            {
                "source": "module_log",
                "target": {"session_id": "ms-cco"},
                "window": {"mode": "cursor_range", "start_seq": 1, "end_seq": 1},
                "match": {"kind": "literal", "value": "boot"},
            },
            {
                "source": "listener",
                "target": {"mapping_id": "listener-main", "index_id": "idx-listener-test"},
                "window": {"mode": "cursor_range", "index_id": "idx-listener-test",
                           "start_frame_id": 1, "end_frame_id": 3},
                "match": {"kind": "frame_query", "frame_kind": "central_beacon", "selector": "first"},
            },
        ],
        "client_request_id": "p2-historical-1",
    }


def test_v2_investigation_fans_out_sources_and_returns_bounded_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(module_log_factory=_module_factory,
                               listener_factory=_listener_bundle_factory)
    client = TestClient(app)

    created = client.post("/api/ai/v2/investigations", json=_historical_investigation())

    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert job_id.startswith("job-")
    result = client.get(f"/api/ai/v2/jobs/{job_id}", params={"wait_seconds": 0})
    assert result.status_code == 200
    body = result.json()
    assert body["job_id"] == job_id
    assert body["job_state"] in {"running", "succeeded"}

    evidence = client.get(f"/api/ai/v2/jobs/{job_id}/evidence", params={"level": "L1"})
    assert evidence.status_code == 200
    assert evidence.json()["level"] == "L1"
    assert len(evidence.json()["summary"]) <= 3072
    assert {item["source"] for item in evidence.json()["refs"]} >= {"module_log", "listener"}


def test_v2_investigation_preserves_historical_listener_index_id_across_l2_and_l3(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(module_log_factory=_module_factory,
                               listener_factory=_listener_bundle_factory)
    client = TestClient(app)
    created = client.post("/api/ai/v2/investigations", json=_historical_investigation())
    job_id = created.json()["job_id"]

    l2 = client.get(f"/api/ai/v2/jobs/{job_id}/evidence", params={"level": "L2"})
    l3 = client.get(f"/api/ai/v2/jobs/{job_id}/evidence", params={"level": "L3"})

    assert l2.status_code == l3.status_code == 200
    assert any(ref.get("index_id") == "idx-listener-test" for ref in l2.json()["refs"])
    assert any(ref.get("index_id") == "idx-listener-test" for ref in l3.json()["refs"])
    assert len(l2.content) <= 16 * 1024
    assert all(set(item) <= {"source", "evidence_id", "raw_ref", "index_id", "frame_id", "correlation"}
               for item in l3.json()["refs"])
    assert "raw_hex" not in l3.text


def test_v2_live_not_seen_is_inconclusive_and_job_read_does_not_refresh(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(module_log_factory=_module_factory,
                               listener_factory=_listener_bundle_factory)
    client = TestClient(app)
    body = {
        "observations": [{
            "source": "module_log", "target": {"session_id": "ms-cco"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "not_seen", "matcher": {"kind": "literal", "value": "never"}},
        }],
    }
    created = client.post("/api/ai/v2/investigations", json=body)
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    first = client.get(f"/api/ai/v2/jobs/{job_id}", params={"wait_seconds": 0})
    second = client.get(f"/api/ai/v2/jobs/{job_id}", params={"wait_seconds": 0})
    assert first.status_code == second.status_code == 200
    assert first.json()["job_state"] == second.json()["job_state"]
    assert first.json().get("verdict") != "pass"


class _WriteModuleService(FakeModuleService):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.stops = []

    def write_text_session(self, session_id, text, append_newline=True):
        self.writes.append((session_id, text, append_newline))
        return {"session_id": session_id, "sent": text}

    def write_session(self, session_id, data_hex):
        self.writes.append((session_id, data_hex))
        return {"session_id": session_id, "sent_hex": data_hex}

    def stop_session(self, session_id, force=False):
        self.stops.append((session_id, force))
        self.sessions[session_id]["state"] = "idle"
        return {"session_id": session_id, "state": "idle"}


class _WriteSimconService:
    def __init__(self):
        self.requests = []

    def verify(self, request):
        self.requests.append(request)
        return {"passed": True, "steps": 1}


def _write_app(module, *, simcon=None, auth=None):
    from workbench.ai_operations import AIControlService

    control = AIControlService(module_service=module, simcon_service=simcon)
    return create_workbench_app(
        ai_control_service=control,
        ai_auth_store=auth,
        listener_factory=_listener_factory,
    )


def test_v2_module_action_ensure_returns_owned_and_does_not_close_reused_session(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    module = _WriteModuleService()
    client = TestClient(_write_app(module))

    response = client.post("/api/ai/v2/module-actions", json={
        "action": "ensure", "mapping_id": "cco-main", "client_request_id": "ensure-1",
    })

    assert response.status_code == 202
    assert response.json()["job_state"] == "succeeded"
    assert response.json()["verdict"] is None
    assert response.json()["result"]["owned"] is False
    assert module.stops == []


def test_v2_module_action_send_is_idempotent_and_payload_collision_is_409(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    module = _WriteModuleService()
    client = TestClient(_write_app(module))
    body = {"action": "send", "session_id": "ms-cco", "text": "status", "client_request_id": "send-1"}

    first = client.post("/api/ai/v2/module-actions", json=body)
    replay = client.post("/api/ai/v2/module-actions", json=body)
    collision = client.post("/api/ai/v2/module-actions", json={**body, "text": "reset"})

    assert first.status_code == replay.status_code == 202
    assert first.json()["job_id"] == replay.json()["job_id"]
    assert collision.status_code == 409
    assert module.writes == [("ms-cco", "status", True)]


def test_v2_verification_run_has_null_verdict_and_is_not_cancellable(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    simcon = _WriteSimconService()
    app = _write_app(_WriteModuleService(), simcon=simcon)
    client = TestClient(app)

    created = client.post("/api/ai/v2/verification-runs", json={
        "task": {"scenario_id": "smoke"}, "client_request_id": "verify-1",
    })
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert created.json()["verdict"] is None
    cancelled = client.post(f"/api/ai/v2/jobs/{job_id}/cancel")
    assert cancelled.status_code == 409
    assert simcon.requests == [{"scenario_id": "smoke"}]


def test_v2_flash_job_requires_firmware_root_and_is_not_cancellable(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    firmware_root = tmp_path / "firmware"
    firmware_root.mkdir()
    firmware = firmware_root / "fw.bin"
    firmware.write_bytes(b"fake")
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["status:read", "module_flash:execute", "observation:create"], resources=["cco-main"], ttl_seconds=60,
        created_by="human", firmware_roots=[str(firmware_root)],
    )
    module = _WriteModuleService()
    client = TestClient(_write_app(module, auth=auth), client=("192.168.1.20", 50001))
    body = {"session_id": "ms-cco", "bin_path": str(firmware), "client_request_id": "flash-1"}

    created = client.post("/api/ai/v2/flash-jobs", json=body, headers=_bearer(token))
    assert created.status_code == 202
    assert created.json()["verdict"] is None
    cancelled = client.post(
        f"/api/ai/v2/jobs/{created.json()['job_id']}/cancel", headers=_bearer(token),
    )
    assert cancelled.status_code == 409

    denied = client.post(
        "/api/ai/v2/flash-jobs",
        json={**body, "bin_path": str(tmp_path / "outside.bin"), "client_request_id": "flash-2"},
        headers=_bearer(token),
    )
    assert denied.status_code == 403


def test_v2_underlying_simcon_operation_rejects_cancel():
    from workbench.ai_operations import AIControlService, SessionBusy

    control = AIControlService(simcon_service=_WriteSimconService())
    operation = control.store.create(
        "simcon_verify", "ai:test", {"target": {"mapping_id": "simcon"}},
    )
    with pytest.raises(SessionBusy, match="不能由观察任务取消接口中断"):
        control.cancel_operation(operation["operation_id"])


# ---------------------------------------------------------------------------
# REQS-0022 Phase 2：v2 listener evidence L1/L2/L3 分层投影
# ---------------------------------------------------------------------------

class _TraceBundle:
    def __init__(self):
        self.serial_service = FakeListenerService()
        self.log_service = FakeListenerLogService()
        self.trace_service = CapturingTraceService()


def _trace_bundle_factory():
    bundle = _TraceBundle()
    app = FastAPI()
    app.state.serial_service = bundle.serial_service
    app.state.log_service = bundle.log_service
    app.state.trace_service = bundle.trace_service
    return app


def _trace_investigation():
    return {
        "observations": [
            {
                "source": "listener",
                "target": {"mapping_id": "listener-main", "index_id": "idx-listener-test"},
                "window": {"mode": "time_range", "start": "10:00:00.000", "end": "10:05:00.000"},
                "match": {
                    "kind": "trace_query", "scope": "round",
                    "feature": {"app_id": "0003", "msg_seq": "1EC2", "dst_tei": "087",
                                "nid": "00947F69"},
                    "directions": ["downlink", "uplink", "ack"],
                },
                "completion": {"match_count": 1},
            },
        ],
        "client_request_id": "p2-trace-1",
    }


def _create_trace_job(client) -> str:
    created = client.post("/api/ai/v2/investigations", json=_trace_investigation())
    assert created.status_code == 202, created.text
    return created.json()["job_id"]


def test_v2_listener_evidence_is_layered_l1_no_raw_hex_l2_projections(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(module_log_factory=_module_factory,
                               listener_factory=_trace_bundle_factory)
    client = TestClient(app)
    job_id = _create_trace_job(client)

    l1 = client.get(f"/api/ai/v2/jobs/{job_id}/evidence", params={"level": "L1"})
    l2 = client.get(f"/api/ai/v2/jobs/{job_id}/evidence", params={"level": "L2"})

    assert l1.status_code == 200
    assert l2.status_code == 200
    # L1 不返回完整帧、不含 raw_hex，只给范围摘要
    assert "raw_hex" not in l1.text
    l1_body = l1.json()
    l1_items = [item for item in l1_body["items"] if item["source"] == "listener"]
    assert l1_items
    l1_data = l1_items[0]["data"]
    assert "frames" not in l1_data and "matches" not in l1_data
    assert "flow_groups" in l1_data
    assert "correlation_status" in l1_data
    assert "frame_type_counts" in l1_data
    assert "direction_counts" in l1_data
    # L2 含截图同类字段（帧类型 / NID / 源目的 / 方向）与 ref
    l2_body = l2.json()
    l2_items = [item for item in l2_body["items"] if item["source"] == "listener"]
    assert l2_items
    data = l2_items[0]["data"]
    frames = data.get("frames") or []
    assert frames
    assert {"frm_type", "nid", "src", "dst", "direction", "ref"} <= set(frames[0])
    assert frames[0]["ref"] == "listener:idx-listener-test:1"


def test_v2_listener_evidence_l3_accepts_job_refs_and_rejects_foreign(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(module_log_factory=_module_factory,
                               listener_factory=_trace_bundle_factory)
    client = TestClient(app)
    job_id = _create_trace_job(client)

    ok = client.get(
        f"/api/ai/v2/jobs/{job_id}/evidence",
        params={"level": "L3", "ref": "listener:idx-listener-test:1"},
    )
    foreign = client.get(
        f"/api/ai/v2/jobs/{job_id}/evidence",
        params={"level": "L3", "ref": "listener:idx-listener-other:1"},
    )
    malformed = client.get(
        f"/api/ai/v2/jobs/{job_id}/evidence",
        params={"level": "L3", "ref": "listener:not-a-frame"},
    )

    assert ok.status_code == 200
    assert "raw_hex" in ok.text
    assert foreign.status_code == 403
    assert malformed.status_code == 422


class _MinutePeriodsLogService(FakeListenerLogService):
    def __init__(self):
        super().__init__()
        self.minute_calls = []

    def list_task_minute_periods(self, task_no, period_minutes=None, cco_tei="001",
                                 nid="", start_time="", end_time=""):
        self.minute_calls.append({
            "task_no": task_no, "cco_tei": cco_tei, "nid": nid,
            "start_time": start_time, "end_time": end_time,
        })
        return {
            "task_no": str(task_no), "source": "manual", "derived_period_minutes": None,
            "periods": [{
                "period_start": 0, "period_end": 900000, "report_count": 1,
                "description": "10:15:00 - 10:30:00",
                "reports": [{
                    "frame_id": 7, "log_time": "10:15:01.000",
                    "freeze_time": "10:14:00", "response_result": 0,
                    "source_mac": "mac-1", "source_tei": "001",
                    "report_count": 1, "data_length": 120,
                    "application_error": None, "application_raw": "AB12",
                    "data_status": "ok",
                }],
            }],
            "unconfigured_report_count": 0, "unconfigured_reports": [],
        }


def _minute_periods_bundle_factory():
    class _Bundle:
        def __init__(self):
            self.serial_service = FakeListenerService()
            self.log_service = _MinutePeriodsLogService()

    bundle = _Bundle()
    app = FastAPI()
    app.state.serial_service = bundle.serial_service
    app.state.log_service = bundle.log_service
    return app


def test_v2_listener_minute_periods_l2_exposes_freeze_time(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    app = create_workbench_app(module_log_factory=_module_factory,
                               listener_factory=_minute_periods_bundle_factory)
    client = TestClient(app)
    created = client.post("/api/ai/v2/investigations", json={
        "observations": [{
            "source": "listener",
            "target": {"mapping_id": "listener-main", "index_id": "idx-listener-test"},
            "window": {"mode": "time_range", "start": "10:00:00.000", "end": "10:30:00.000"},
            "match": {"kind": "minute_periods", "task_no": 1, "cco_tei": "001", "nid": "00947F69"},
            "completion": {"match_count": 1},
        }],
        "client_request_id": "p2-minute-1",
    })
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]

    l2 = client.get(f"/api/ai/v2/jobs/{job_id}/evidence", params={"level": "L2"})

    assert l2.status_code == 200
    items = [item for item in l2.json()["items"] if item["source"] == "listener"]
    assert items
    frames = items[0]["data"]["frames"]
    assert frames
    assert frames[0]["ref"] == "listener:idx-listener-test:7"
    assert frames[0]["log_time"] == "10:15:01.000"
    assert frames[0]["freeze_time"] == "10:14:00"
    assert "response_result" in frames[0]
