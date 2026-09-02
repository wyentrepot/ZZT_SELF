"""AI control authorization and operation service tests."""
from __future__ import annotations

import copy

import pytest

from workbench.ai_auth import AuthorizationStore
from workbench.ai_operations import (
    AIControlService,
    InvalidObservation,
    SourceUnavailable,
)


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
        self.indexes = {
            self.index_id: self.frames,
            "idx-listener-other": [
                {
                    "id": 1, "frame_id": 1, "sequence": "other-000001",
                    "log_time": "11:00:01.000",
                    "summary": {"FrmType": "中央信标", "flag": 1},
                    "parse_error": None,
                },
            ],
        }

    def status(self):
        return {
            "state": "idle", "frame_count": len(self.frames),
            "index_id": self.index_id, "source_path": "/tmp/listener.txt",
        }

    def list_indexes(self):
        return {
            "current_index_id": self.index_id,
            "indexes": [
                {"index_id": self.index_id, "kind": "serial", "is_current": True},
                {"index_id": "idx-listener-other", "kind": "serial", "is_current": False},
            ],
        }

    def list_index_frames(self, index_id, **filters):
        if index_id not in self.indexes:
            raise KeyError(index_id)
        rows = list(self.indexes[index_id])
        after_id = filters.get("after_id")
        if after_id is not None:
            rows = [item for item in rows if item["frame_id"] > after_id]
        start_id = filters.get("start_id")
        if start_id is not None:
            rows = [item for item in rows if item["frame_id"] >= start_id]
        end_id = filters.get("end_id")
        if end_id is not None:
            rows = [item for item in rows if item["frame_id"] <= end_id]
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
        total = len(rows)
        rows = rows[offset:offset + limit]
        return {
            "index_id": index_id, "items": rows, "total": total,
            "offset": offset, "limit": limit, "after_id": rows[-1]["frame_id"] if rows else None,
        }

    def get_index_frame(self, index_id, frame_id):
        if index_id not in self.indexes:
            raise KeyError(index_id)
        for frame in self.indexes[index_id]:
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


def _listener_cursor_request(index_id="idx-listener-test", start_frame_id=1, end_frame_id=3):
    return {
        "source": "listener",
        "target": {"mapping_id": "listener-main"},
        "window": {
            "type": "cursor_range",
            "index_id": index_id,
            "start_frame_id": start_frame_id,
            "end_frame_id": end_frame_id,
        },
        "match": {"kind": "frame_query", "frame_kind": "central_beacon", "selector": "all"},
    }


def test_listener_cursor_range_is_closed_and_preserves_composite_deep_links_and_artifact():
    log_service = FakeListenerLogService()
    service = AIControlService(listener_service=FakeListenerService(), log_service=log_service)

    operation = service.create_observation(
        _listener_cursor_request(), actor="ai:grant-test", client_request_id="listener-cursor-1",
    )

    assert operation["state"] == "matched"
    assert operation["result"]["condition_met"] is True
    assert operation["result"]["index"]["index_id"] == "idx-listener-test"
    assert operation["result"]["index"]["start_frame_id"] == 1
    assert operation["result"]["index"]["end_frame_id"] == 3
    assert operation["result"]["snippet"][0]["frame_key"] == {
        "index_id": "idx-listener-test", "frame_id": 1,
    }
    assert [item["frame_key"] for item in operation["result"]["matches"]] == [
        {"index_id": "idx-listener-test", "frame_id": 1},
        {"index_id": "idx-listener-test", "frame_id": 2},
        {"index_id": "idx-listener-test", "frame_id": 3},
    ]
    assert operation["result"]["matches"][-1]["detail_url"].endswith("idx-listener-test/frames/3")
    artifact_id = operation["result"]["artifact_id"]
    assert service.read_artifact(artifact_id)["content"]["index"]["end_frame_id"] == 3
    service.log_service = None
    assert service.create_observation(
        _listener_cursor_request(), actor="ai:grant-test", client_request_id="listener-cursor-1",
    )["operation_id"] == operation["operation_id"]


def test_listener_cursor_range_honours_single_frame_empty_hole_and_index_isolation():
    log_service = FakeListenerLogService()
    log_service.indexes["idx-listener-test"] = [
        log_service.frames[0], log_service.frames[2],
    ]
    service = AIControlService(listener_service=FakeListenerService(), log_service=log_service)

    single = service.create_observation(
        _listener_cursor_request(start_frame_id=1, end_frame_id=1), actor="ai:grant-test",
    )
    empty_hole = service.create_observation(
        _listener_cursor_request(start_frame_id=2, end_frame_id=2), actor="ai:grant-test",
    )
    other_index = service.create_observation(
        _listener_cursor_request(index_id="idx-listener-other", start_frame_id=1, end_frame_id=1),
        actor="ai:grant-test",
    )

    assert [item["frame_key"] for item in single["result"]["matches"]] == [
        {"index_id": "idx-listener-test", "frame_id": 1},
    ]
    assert empty_hole["state"] == "succeeded"
    assert empty_hole["result"]["condition_met"] is False
    assert empty_hole["result"]["matches"] == []
    assert other_index["result"]["matches"][0]["frame_key"] == {
        "index_id": "idx-listener-other", "frame_id": 1,
    }


def test_listener_historical_cursor_result_uses_its_index_source_path_not_current_index(tmp_path):
    from listener.index_registry import ListenerIndexRegistry
    from listener.log_service import LogFileService

    class Parser:
        def parse_summary(self, value):
            return {"simple": {"FrmType": "中央信标"}}

        def parse(self, value):
            return {"full": {"raw": value}}

    first_source = tmp_path / "first.log"
    second_source = tmp_path / "second.log"
    first_source.write_bytes(b"[1][10:00:00.000]7E FF 02 FF 00 7E\r\n")
    second_source.write_bytes(b"[2][11:00:00.000]7E FF 02 FF 00 7E\r\n")
    registry = ListenerIndexRegistry(tmp_path / "indexes")
    log_service = LogFileService(Parser(), tmp_path / "legacy.sqlite3", index_registry=registry)
    try:
        first_index_id = log_service.index_file(first_source)["index_id"]
        log_service.index_file(second_source)
        assert log_service.status()["source_path"] == str(second_source)

        service = AIControlService(listener_service=FakeListenerService(), log_service=log_service)
        operation = service.create_observation(
            _listener_cursor_request(index_id=first_index_id, start_frame_id=1, end_frame_id=1),
            actor="ai:grant-test",
        )

        assert operation["state"] == "matched"
        assert operation["result"]["index"]["index_id"] == first_index_id
        assert operation["result"]["index"]["source_log_path"] == str(first_source)
        assert operation["result"]["matches"][0]["frame_key"] == {
            "index_id": first_index_id, "frame_id": 1,
        }
        artifact_id = operation["result"]["artifact_id"]
        assert service.read_artifact(artifact_id)["content"]["index"]["source_log_path"] == str(first_source)
    finally:
        log_service.close()


def test_listener_current_only_index_keeps_current_status_source_path_but_unknown_history_omits_it():
    log_service = FakeListenerLogService()
    service = AIControlService(listener_service=FakeListenerService(), log_service=log_service)

    current = service.create_observation(
        _listener_cursor_request(start_frame_id=1, end_frame_id=1), actor="ai:grant-test",
    )
    historical = service.create_observation(
        _listener_cursor_request(index_id="idx-listener-other", start_frame_id=1, end_frame_id=1),
        actor="ai:grant-test",
    )

    assert current["result"]["index"]["source_log_path"] == "/tmp/listener.txt"
    assert service.read_artifact(current["result"]["artifact_id"])["content"]["index"][
        "source_log_path"
    ] == "/tmp/listener.txt"
    assert "source_log_path" not in historical["result"]["index"]


def test_operation_store_idempotency_is_atomic_for_same_and_different_fingerprints():
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from workbench.ai_store import IdempotencyConflict, OperationStore

    same_store = OperationStore()
    same_barrier = threading.Barrier(2)

    def same_request():
        same_barrier.wait()
        return same_store.create(
            "observation", "ai:grant", {"source": "module_log"},
            client_request_id="same", idempotency_fingerprint="same-fingerprint",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        same_results = list(executor.map(lambda _: same_request(), range(2)))
    assert {item["operation_id"] for item in same_results} == {same_results[0]["operation_id"]}

    different_store = OperationStore()
    different_barrier = threading.Barrier(2)

    def different_request(fingerprint):
        different_barrier.wait()
        try:
            return different_store.create(
                "observation", "ai:grant", {"source": fingerprint},
                client_request_id="different", idempotency_fingerprint=fingerprint,
            )
        except IdempotencyConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        different_results = list(executor.map(different_request, ("fingerprint-a", "fingerprint-b")))
    assert sum(item == "conflict" for item in different_results) == 1
    assert sum(isinstance(item, dict) for item in different_results) == 1


@pytest.mark.parametrize(
    "observation_request, message",
    [
        (_listener_cursor_request(start_frame_id=3, end_frame_id=1), "start_frame_id 不能大于"),
        (_listener_cursor_request(start_frame_id=-1, end_frame_id=1), "不能为负数"),
        (_listener_cursor_request(start_frame_id=True, end_frame_id=1), "必须是整数"),
        (_listener_cursor_request(start_frame_id="1", end_frame_id=1), "必须是整数"),
        (_listener_cursor_request(start_frame_id=1, end_frame_id=501), "范围过大"),
        (_listener_cursor_request(start_frame_id=1, end_frame_id=4), "超出索引边界"),
        (_listener_cursor_request(index_id="idx-unknown", start_frame_id=1, end_frame_id=1), "不存在"),
        ({
            **_listener_cursor_request(),
            "target": {"mapping_id": "listener-main", "index_id": "idx-listener-other"},
        }, "不一致"),
        ({
            **_listener_cursor_request(),
            "target": {"mapping_id": "listener-main", "capture": "idx-listener-other"},
        }, "不一致"),
        ({
            **_listener_cursor_request(),
            "window": {"type": "cursor_range", "index_id": "idx-listener-test", "start_frame_id": 1},
        }, "必须提供"),
    ],
)
def test_listener_cursor_range_rejects_invalid_or_cross_index_input(observation_request, message):
    service = AIControlService(
        listener_service=FakeListenerService(), log_service=FakeListenerLogService(),
    )

    with pytest.raises(InvalidObservation, match=message):
        service.create_observation(observation_request, actor="ai:grant-test")


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


def test_operation_store_rejects_idempotency_key_collisions_by_fingerprint():
    from workbench.ai_store import IdempotencyConflict, OperationStore

    store = OperationStore()
    first = store.create(
        "observation", "ai:grant", {"source": "module_log"},
        client_request_id="collision", idempotency_fingerprint="fingerprint-a",
    )

    assert store.create(
        "observation", "ai:grant", {"source": "module_log"},
        client_request_id="collision", idempotency_fingerprint="fingerprint-a",
    )["operation_id"] == first["operation_id"]
    with pytest.raises(IdempotencyConflict, match="幂等键与既有请求不一致"):
        store.create(
            "observation", "ai:grant", {"source": "listener"},
            client_request_id="collision", idempotency_fingerprint="fingerprint-b",
        )


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


# ---------------------------------------------------------------------------
# REQS-0022 Phase 1：trace_query 复用既有 TraceService
# ---------------------------------------------------------------------------

def _trace_report_fixture():
    """最小回放报告 fixture：一条 confirmed 流（下行 + 上行两帧投影）。"""
    return {
        "trace_id": "tr-fixture", "mode": "replay", "scope": "round", "feature": {},
        "summary": {"rounds": 1, "meters": 1, "full_chain": 1, "no_ack": 0,
                    "no_response": 0, "denied": 0, "no_confirm": 0, "flows": 1,
                    "bad_frames": 0},
        "rounds": [{
            "round_id": "rd-0000", "cluster_index": 0,
            "start_t": "10:00:00.000", "end_t": "10:00:01.000",
            "msg_seqs": ["0x1EC2"],
            "flows": [{
                "flow_key": "0003:1EC2", "app_id": "0003", "msg_seq": "0x1EC2",
                "via_tei": "087", "stage": "confirmed",
                "frames": [
                    {"frame_id": 1, "log_time": "10:00:00.000", "direction": "downlink",
                     "role": "sent", "frm_type": "终端主动并发抄表", "nid": "00947F69",
                     "src": "001", "dst": "087", "ori_s": "001", "ch_type": None,
                     "app_id": "0003", "msg_seq": "0x1EC2", "flow_dir": 0,
                     "meter_addrs": ["000000000001"]},
                    {"frame_id": 2, "log_time": "10:00:01.000", "direction": "uplink",
                     "role": "response", "frm_type": "终端主动并发抄表", "nid": "00947F69",
                     "src": "087", "dst": "001", "ori_s": "087", "ch_type": None,
                     "app_id": "0003", "msg_seq": "0x1EC2", "flow_dir": 1,
                     "meter_addrs": [{"addr": "000000000001", "denied": False}]},
                ],
            }],
            "meter_table": [], "meters": {"targets": 1, "responded": 1, "denied": 0, "missing": 0},
            "bad_frames": 0,
        }],
        "proxy_graph": [], "artifact_id": None,
    }


class CapturingTraceService:
    """捕获 run_replay 入参的假追踪服务（只测 AI 适配层，不碰真实索引）。"""

    def __init__(self, report=None):
        self.calls = []
        self.report = report if report is not None else _trace_report_fixture()

    def run_replay(self, feature, index_id=None, mode="replay", directions=None):
        self.calls.append({
            "feature": copy.deepcopy(feature),
            "index_id": index_id,
            "mode": mode,
            "directions": list(directions) if directions else None,
        })
        return copy.deepcopy(self.report)


def _trace_query_request(**overrides):
    request = {
        "source": "listener",
        "target": {"index_id": "idx-listener-test"},
        "window": {"mode": "time_range", "start": "10:00:00.000", "end": "10:05:00.000"},
        "match": {
            "kind": "trace_query", "scope": "round",
            "feature": {
                "app_id": "0003", "msg_seq": "1EC2", "frm_type": "终端主动并发抄表",
                "dst_tei": "087", "nid": "00947F69", "channel": "载波",
                "app_raw_contains": "123456789012",
            },
            "directions": ["downlink", "uplink", "ack"],
        },
        "completion": {"match_count": 1},
    }
    request.update(overrides)
    return request


def test_listener_trace_query_passes_feature_verbatim_and_returns_refs():
    tracer = CapturingTraceService()
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
        trace_service=tracer,
    )

    operation = service.create_observation(_trace_query_request(), actor="ai:grant-test")

    assert operation["state"] == "matched"
    assert len(tracer.calls) == 1
    call = tracer.calls[0]
    assert call["feature"]["scope"] == "round"
    # feature 字段原样透传给 TraceService
    assert call["feature"]["feature"]["app_id"] == "0003"
    assert call["feature"]["feature"]["msg_seq"] == "1EC2"
    assert call["feature"]["feature"]["dst_tei"] == "087"
    assert call["feature"]["feature"]["nid"] == "00947F69"
    assert call["feature"]["feature"]["channel"] == "载波"
    assert call["feature"]["feature"]["app_raw_contains"] == "123456789012"
    # 观察窗口映射为 trace 特征窗口
    assert call["feature"]["window"]["mode"] == "time_range"
    assert call["feature"]["window"]["start_time"] == "10:00:00.000"
    assert call["feature"]["window"]["end_time"] == "10:05:00.000"
    assert call["index_id"] == "idx-listener-test"
    assert call["directions"] == ["downlink", "uplink", "ack"]
    # 报告中每个 frame_id 转成稳定 index_id + frame_id 引用
    result = operation["result"]
    assert [m["frame_key"] for m in result["matches"]] == [
        {"index_id": "idx-listener-test", "frame_id": 1},
        {"index_id": "idx-listener-test", "frame_id": 2},
    ]
    assert result["condition_met"] is True
    assert result["trace"]["summary"]["flows"] == 1
    assert result["artifact_id"].startswith("art-")


def test_listener_trace_query_raw_hex_contains_requires_narrowing():
    tracer = CapturingTraceService()
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
        trace_service=tracer,
    )

    # 无 app_id、无 NID、无时间窗/帧 ID 窗口 → 422（收窄门）
    with pytest.raises(InvalidObservation, match="收窄"):
        service.create_observation({
            "source": "listener",
            "match": {"kind": "trace_query", "feature": {"raw_hex_contains": "1234"}},
        }, actor="ai:grant-test")
    assert tracer.calls == []

    # 有 app_id 即视为已收窄，允许通过
    allowed = service.create_observation({
        "source": "listener",
        "match": {"kind": "trace_query", "feature": {"app_id": "0003",
                                                     "raw_hex_contains": "1234"}},
    }, actor="ai:grant-test")
    assert allowed["state"] in ("matched", "succeeded")
    assert tracer.calls[-1]["feature"]["feature"]["raw_hex_contains"] == "1234"


def test_listener_trace_query_requires_trace_service_and_valid_input():
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
    )
    # 无追踪服务 → 503
    with pytest.raises(SourceUnavailable):
        service.create_observation(_trace_query_request(), actor="ai:grant-test")

    tracer = CapturingTraceService()
    with_trace = AIControlService(
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
        trace_service=tracer,
    )
    # 非法 scope / 非法方向 / 非法 feature 字段 → 422
    with pytest.raises(InvalidObservation, match="scope"):
        with_trace.create_observation(
            _trace_query_request(match={"kind": "trace_query", "scope": "batch",
                                        "feature": {"app_id": "0003"}}),
            actor="ai:grant-test",
        )
    with pytest.raises(InvalidObservation, match="directions"):
        with_trace.create_observation(
            _trace_query_request(match={"kind": "trace_query",
                                        "feature": {"app_id": "0003"},
                                        "directions": ["sideways"]}),
            actor="ai:grant-test",
        )
    with pytest.raises(InvalidObservation, match="不支持字段"):
        with_trace.create_observation(
            _trace_query_request(match={"kind": "trace_query",
                                        "feature": {"app_id": "0003", "business_kind": "x"}}),
            actor="ai:grant-test",
        )
    # live 窗口不支持
    with pytest.raises(InvalidObservation, match="time_range 或 cursor_range"):
        with_trace.create_observation(
            _trace_query_request(window={"mode": "live", "start": "now"}),
            actor="ai:grant-test",
        )
    # 缺 app_id 交给 TraceService 特征校验（FeatureError → 422）
    with pytest.raises(InvalidObservation, match="app_id"):
        with_trace.create_observation(
            _trace_query_request(match={"kind": "trace_query", "feature": {}}),
            actor="ai:grant-test",
        )


def test_listener_trace_query_is_idempotent_by_client_request_id():
    tracer = CapturingTraceService()
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
        trace_service=tracer,
    )
    request = _trace_query_request()
    request["client_request_id"] = "trace-query-1"

    first = service.create_observation(request, actor="ai:grant-test", client_request_id="trace-query-1")
    replay = service.create_observation(request, actor="ai:grant-test", client_request_id="trace-query-1")

    assert first["operation_id"] == replay["operation_id"]
    assert len(tracer.calls) == 1


# ---------------------------------------------------------------------------
# REQS-0022 Phase 2：minute_periods 复用既有 list_task_minute_periods
# ---------------------------------------------------------------------------

class _MinutePeriodsLogService(FakeListenerLogService):
    def __init__(self):
        super().__init__()
        self.calls = []

    def list_task_minute_periods(self, task_no, period_minutes=None, cco_tei="001",
                                 nid="", start_time="", end_time=""):
        self.calls.append({
            "task_no": task_no, "period_minutes": period_minutes,
            "cco_tei": cco_tei, "nid": nid, "start_time": start_time, "end_time": end_time,
        })
        return [{
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
        }]


def _minute_periods_request(**overrides):
    request = {
        "source": "listener",
        "target": {"index_id": "idx-listener-test"},
        "window": {"mode": "time_range", "start": "10:00:00.000", "end": "10:30:00.000"},
        "match": {"kind": "minute_periods", "task_no": 1, "cco_tei": "001", "nid": "00947F69"},
        "completion": {"match_count": 1},
    }
    request.update(overrides)
    return request


def test_listener_minute_periods_reuses_list_task_minute_periods():
    log_service = _MinutePeriodsLogService()
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=log_service,
    )

    operation = service.create_observation(_minute_periods_request(), actor="ai:grant-test")

    assert operation["state"] in ("matched", "succeeded")
    assert len(log_service.calls) == 1
    call = log_service.calls[0]
    assert call["task_no"] == 1
    assert call["cco_tei"] == "001"
    assert call["nid"] == "00947F69"
    assert call["start_time"] == "10:00:00.000"
    assert call["end_time"] == "10:30:00.000"
    result = operation["result"]
    # 每个 report 的 frame_id 转成稳定 index_id + frame_id 引用
    report = result["periods"][0]["reports"][0]
    assert report["frame_key"] == {"index_id": "idx-listener-test", "frame_id": 7}
    # L2 同时提供 log_time 与 freeze_time（freeze_time 是分钟归属权威字段）
    assert report["log_time"] == "10:15:01.000"
    assert report["freeze_time"] == "10:14:00"
    assert report["response_result"] == 0
    assert result["artifact_id"].startswith("art-")


def test_listener_minute_periods_requires_valid_input():
    log_service = _MinutePeriodsLogService()
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=log_service,
    )
    # 缺 task_no → 422
    with pytest.raises(InvalidObservation, match="task_no"):
        service.create_observation(
            {"source": "listener", "match": {"kind": "minute_periods", "cco_tei": "001"}},
            actor="ai:grant-test",
        )
    # 非法 cco_tei → 422
    with pytest.raises(InvalidObservation, match="cco_tei"):
        service.create_observation(
            {"source": "listener", "match": {"kind": "minute_periods", "task_no": 1, "cco_tei": "XYZ"}},
            actor="ai:grant-test",
        )
    # live 窗口不支持（分钟采集是历史闭合窗口）
    with pytest.raises(InvalidObservation, match="time_range"):
        service.create_observation(
            _minute_periods_request(window={"mode": "live", "start": "now"}),
            actor="ai:grant-test",
        )
    assert log_service.calls == []


# ---------------------------------------------------------------------------
# REQS-0022 Phase 3：trace_query 适配层计时回归（温热 P95 ≤ 500ms）
# ---------------------------------------------------------------------------

def test_listener_trace_query_warm_p95_under_500ms():
    import time

    tracer = CapturingTraceService()
    service = AIControlService(
        listener_service=FakeListenerService(),
        log_service=FakeListenerLogService(),
        trace_service=tracer,
    )
    durations = []
    for _ in range(20):
        start = time.perf_counter()
        service.create_observation(_trace_query_request(), actor="ai:grant-test")
        durations.append((time.perf_counter() - start) * 1000.0)
    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    assert p95 <= 500.0, f"trace_query 适配层 P95 {p95:.1f}ms 超过 500ms"
