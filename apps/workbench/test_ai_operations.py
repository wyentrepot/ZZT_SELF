"""AI control authorization and operation service tests."""
from __future__ import annotations

from workbench.ai_auth import AuthorizationStore
from workbench.ai_operations import AIControlService


class FakeModuleService:
    def __init__(self):
        self.sessions = {
            "ms-cco": {
                "session_id": "ms-cco", "title": "CCO 主模块", "module": "cco",
                "state": "running", "port": "COM8",
                "port_identity": {"mapping_id": "cco-main"},
                "log_file": "/tmp/cco.log",
                "flash": {"flashing": False, "phase": "idle", "message": ""},
            }
        }
        self.lines = [{"seq": 1, "ts": "t1", "dir": "RX", "text": "boot"}]

    def list_sessions(self):
        return list(self.sessions.values())

    def get_session(self, session_id):
        return self.sessions[session_id]

    def create_session(self, title="", module="cco"):
        session_id = "ms-created"
        self.sessions[session_id] = {
            "session_id": session_id, "title": title or "新会话", "module": module,
            "state": "idle", "port": "", "port_identity": {"mapping_id": ""},
            "log_file": None,
            "flash": {"flashing": False, "phase": "idle", "message": ""},
        }
        return self.sessions[session_id]

    def start_session(self, session_id, port, **kwargs):
        session = self.sessions[session_id]
        session.update({"state": "running", "port": port, "log_file": "/tmp/created.log"})
        return session

    def logs_session(self, session_id, after=-1):
        return {"session_id": session_id, "lines": [x for x in self.lines if x["seq"] > after]}

    def flash_session(self, session_id, bin_path, slot=0, baud_plan=None, no_reboot_after=False):
        session = self.sessions[session_id]
        session["flash"] = {
            "flashing": True, "phase": "transfer", "message": bin_path,
            "packet": 0, "total": 1,
        }
        return {"session_id": session_id, "flash": dict(session["flash"])}

    def status(self):
        return {"sessions": self.list_sessions()}


def test_grant_stores_only_token_sha256_digest():
    store = AuthorizationStore()
    grant, token = store.create_grant(
        scopes=["status:read"], resources=["cco-main"], ttl_seconds=60, created_by="human",
    )

    assert token not in str(store.export_grants())
    assert store.authenticate(token, scope="status:read")["grant_id"] == grant["grant_id"]


def test_ensure_reuses_backend_session_and_observation_returns_bounded_snippet():
    service = AIControlService(module_service=FakeModuleService())
    ensured = service.ensure_module_session({"mapping_id": "cco-main", "module": "cco"})
    assert ensured["reused"] is True
    assert ensured["session"]["session_id"] == "ms-cco"

    operation = service.create_observation(
        {
            "source": "module_log",
            "target": {"session_id": "ms-cco"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "literal", "value": "target print"},
            "context": {"before": 1, "after": 1},
        },
        actor="ai:grant-test",
    )
    assert operation["state"] == "waiting"

    service.module_service.lines.append(
        {"seq": 2, "ts": "t2", "dir": "RX", "text": "target print reached"}
    )
    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)
    assert result["state"] == "matched"
    assert result["result"]["log"]["match_lines"] == [2]
    assert result["result"]["snippet"][0]["seq"] == 1


def test_cancel_is_terminal_and_wait_is_bounded():
    service = AIControlService(module_service=FakeModuleService())
    operation = service.create_observation(
        {
            "source": "module_log",
            "target": {"session_id": "ms-cco"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "literal", "value": "not present"},
        },
        actor="ai:grant-test",
    )

    assert service.cancel_operation(operation["operation_id"])["state"] == "cancelled"
    assert service.wait_operation(operation["operation_id"], timeout_seconds=1)["state"] == "cancelled"

class FakeListenerService:
    def __init__(self):
        self.started = False

    def status(self):
        return {
            "state": "running" if self.started else "idle",
            "port": "COM4",
            "baudrate": 115200,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "frame_count": 3,
            "log_file": "/tmp/listener.txt",
            "port_identity": {"mapping_id": "listener-main", "label": "侦听台"},
        }

    def list_available_ports(self):
        return [{
            "device": "COM4", "mapping_id": "listener-main", "online": True,
            "label": "侦听台", "baudrate": 115200, "bytesize": 8,
            "parity": "N", "stopbits": 1,
        }]

    def start(self, **kwargs):
        self.started = True
        return self.status()

    def stop(self):
        self.started = False
        return self.status()


class FakeListenerLogService:
    def __init__(self):
        self.index_id = "idx-listener-test"
        self.frames = [
            {
                "id": 1, "frame_id": 1, "sequence": "000001",
                "log_time": "10:00:01.000",
                "summary": {"FrmType": "中央信标", "flag": 1},
                "parse_error": None,
            },
            {
                "id": 2, "frame_id": 2, "sequence": "000002",
                "log_time": "10:00:30.000",
                "summary": {"FrmType": "中央信标", "flag": 0},
                "parse_error": None,
            },
            {
                "id": 3, "frame_id": 3, "sequence": "000003",
                "log_time": "10:01:02.000",
                "summary": {"FrmType": "中央信标", "flag": 1},
                "parse_error": None,
            },
        ]

    def status(self):
        return {
            "state": "idle", "frame_count": len(self.frames),
            "index_id": self.index_id, "source_path": "/tmp/listener.txt",
        }

    def list_indexes(self):
        return {
            "current_index_id": self.index_id,
            "indexes": [{"index_id": self.index_id, "kind": "serial", "is_current": True}],
        }

    def list_index_frames(self, index_id, **filters):
        if index_id != self.index_id:
            raise KeyError(index_id)
        rows = list(self.frames)
        after_id = filters.get("after_id")
        if after_id is not None:
            rows = [item for item in rows if item["frame_id"] > after_id]
        start_time = filters.get("start_time") or ""
        end_time = filters.get("end_time") or ""
        if start_time:
            rows = [item for item in rows if item["log_time"] >= start_time]
        if end_time:
            rows = [item for item in rows if item["log_time"] <= end_time]
        query = filters.get("query") or ""
        if query:
            rows = [item for item in rows if query in str(item["summary"])]
        limit = int(filters.get("limit", 100))
        offset = int(filters.get("offset", 0))
        rows = rows[offset:offset + limit]
        return {
            "index_id": index_id, "items": rows, "total": len(self.frames),
            "offset": offset, "limit": limit, "after_id": rows[-1]["frame_id"] if rows else None,
        }

    def get_index_frame(self, index_id, frame_id):
        if index_id != self.index_id:
            raise KeyError(index_id)
        for frame in self.frames:
            if frame["frame_id"] == frame_id:
                return {
                    **frame,
                    "index_id": index_id,
                    "raw_hex": "7E 01 7E",
                    "analysis": {"full": {"beacon": {"flag": frame["summary"]["flag"]}}},
                }
        raise KeyError(frame_id)


def test_listener_time_range_observation_returns_versioned_frame_keys():
    listener = FakeListenerService()
    log_service = FakeListenerLogService()
    service = AIControlService(listener_service=listener, log_service=log_service)

    operation = service.create_observation(
        {
            "source": "listener",
            "target": {"index_id": "idx-listener-test"},
            "window": {
                "mode": "time_range",
                "start": "10:00:00",
                "end": "10:01:59",
            },
            "match": {
                "kind": "parsed_frame",
                "frame_kind": "central_beacon",
                "selector": "first_per_minute",
                "where": [{"path": "analysis.full.beacon.flag", "op": "eq", "value": 1}],
            },
        },
        actor="ai:grant-test",
    )

    assert operation["state"] == "matched"
    matches = operation["result"]["matches"]
    assert [item["frame_key"] for item in matches] == [
        {"index_id": "idx-listener-test", "frame_id": 1},
        {"index_id": "idx-listener-test", "frame_id": 3},
    ]
    assert matches[0]["detail_url"].endswith("/idx-listener-test/frames/1")
    assert "index_id=idx-listener-test" in matches[0]["ui_url"]
    assert operation["result"]["artifact_id"].startswith("art-")
    assert service.read_artifact(operation["result"]["artifact_id"])["content"]["matches"][0]["frame_key"] == {
        "index_id": "idx-listener-test", "frame_id": 1,
    }
    assert service.status()["listener"]["index_capability"] == "versioned"


def test_flash_operation_is_idempotent_and_waits_for_the_shared_session_state():
    module = FakeModuleService()
    service = AIControlService(module_service=module)
    request = {
        "session_id": "ms-cco", "bin_path": "/tmp/allowed/fw.bin", "slot": 0,
        "client_request_id": "flash-beacon-1",
    }

    operation = service.flash_module(request, actor="ai:grant-test")
    assert operation["state"] == "waiting"
    assert service.flash_module(request, actor="ai:grant-test")["operation_id"] == operation["operation_id"]

    module.sessions["ms-cco"]["flash"] = {
        "flashing": False, "phase": "done", "message": "complete",
    }
    completed = service.wait_operation(operation["operation_id"], timeout_seconds=0)
    assert completed["state"] == "succeeded"
    assert completed["result"]["session_id"] == "ms-cco"


def test_authorization_grants_persist_without_plaintext_token(tmp_path):
    path = tmp_path / "grants.json"
    store = AuthorizationStore(storage_path=path)
    grant, token = store.create_grant(
        scopes=["status:read"], resources=["cco-main"], ttl_seconds=60, created_by="human",
    )

    assert token not in path.read_text(encoding="utf-8")
    restored = AuthorizationStore(storage_path=path)
    assert restored.authenticate(token, scope="status:read")["grant_id"] == grant["grant_id"]
    restored.revoke(grant["grant_id"])
    assert AuthorizationStore(storage_path=path).export_grants()[0]["revoked_at"] is not None


def test_operation_store_persists_terminal_records_and_interrupts_inflight_work(tmp_path):
    from workbench.ai_store import OperationStore

    path = tmp_path / "operations.json"
    first = OperationStore(storage_path=path)
    pending = first.create("observation", "ai:grant", {"target": {"mapping_id": "cco-main"}})
    completed = first.create("module_send", "ai:grant", {"target": {"mapping_id": "cco-main"}})
    first.set_state(completed["operation_id"], "succeeded", result={"sent": 1})

    restored = OperationStore(storage_path=path)
    assert restored.get(pending["operation_id"])["state"] == "interrupted"
    assert restored.get(completed["operation_id"])["state"] == "succeeded"


def test_registered_artifact_is_persisted_and_never_reads_a_caller_path(tmp_path):
    from workbench.ai_store import OperationStore

    store = OperationStore(storage_path=tmp_path / "operations.json")
    manifest = store.register_artifact(
        operation_id="op-test", resource="cco-main", kind="observation_result",
        content={"snippet": [{"seq": 1, "text": "matched"}]},
    )
    assert "path" not in manifest
    assert store.read_artifact(manifest["artifact_id"])["content"]["snippet"][0]["text"] == "matched"
    assert OperationStore(storage_path=tmp_path / "operations.json").read_artifact(
        manifest["artifact_id"]
    )["manifest"]["resource"] == "cco-main"

def test_corrupt_operation_store_recovers_to_an_empty_writable_store(tmp_path):
    from workbench.ai_store import OperationStore

    path = tmp_path / "operations.json"
    path.write_text("{not-json", encoding="utf-8")
    store = OperationStore(storage_path=path)
    manifest = store.register_artifact(
        operation_id="op-recovered", resource="cco-main", kind="result",
        content={"ok": True},
    )

    assert store.read_artifact(manifest["artifact_id"])["content"] == {"ok": True}


def test_status_reports_backend_serial_resource_handles():
    from shared.serial_resources import SerialResourceRegistry

    registry = SerialResourceRegistry()
    registry.reserve(
        "module:ms-cco", label="模块日志会话 CCO",
        resource_id="cco-main", aliases=("COM8", "/dev/ttyACM0"),
    )
    service = AIControlService(
        module_service=FakeModuleService(),
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
        resource_registry=registry,
    )

    handles = service.status()["serial_handles"]
    assert handles == [{
        "owner_id": "module:ms-cco",
        "owner_label": "模块日志会话 CCO",
        "resource_id": "cco-main",
        "aliases": ["/dev/ttyACM0", "COM8"],
    }]


def test_force_stop_listener_marks_active_listener_observations_source_stopped():
    listener = FakeListenerService()
    service = AIControlService(
        listener_service=listener,
        log_service=FakeListenerLogService(),
    )
    operation = service.create_observation(
        {
            "source": "listener",
            "target": {"mapping_id": "listener-main"},
            "window": {"mode": "live", "start": "now", "timeout_seconds": 60},
            "match": {"kind": "frame_query", "frame_kind": "central_beacon"},
        },
        actor="ai:grant-test",
    )

    from workbench.ai_operations import SessionBusy
    import pytest
    with pytest.raises(SessionBusy):
        service.stop_listener(actor="ai:grant-test")
    stopped = service.stop_listener(actor="ai:grant-test", force=True)

    assert stopped["listener"]["state"] == "idle"
    assert service.get_operation(operation["operation_id"])["state"] == "source_stopped"
