"""REQS-0018：AI 控制面 13762 收发库只读查询接口测试。

覆盖 /api/ai/v1/simcon/store/events|snapshots[/{id}] 与 /listener/minute-periods：
200 结构、401/403/422/503 映射、口径透传。全假实现，不开串口。
"""
from __future__ import annotations

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from workbench.ai_auth import AuthorizationStore
from workbench.ai_store import OperationStore
from workbench.app import create_workbench_app
from workbench.test_ai_operations import FakeListenerLogService, FakeModuleService


class FakeStoreCore:
    """模拟 simcon 执行核心 + REQS-0018 收发库访问器（与 module_log 提升的同构）。"""

    def __init__(self):
        self.snapshots = [
            {"id": 1, "afn": "10", "fn": "F2", "mode": "manual", "total": 2,
             "item_count": 2, "status": "done", "ts": "2026-09-01T10:00:00"},
            {"id": 2, "afn": "03", "fn": "F11", "mode": "auto", "total": 1,
             "item_count": 1, "status": "done", "ts": "2026-09-01T10:01:00"},
        ]
        self.items = [
            {"id": 1, "snapshot_id": 1, "seq_index": 1, "addr": "0001", "payload_json": "{}"},
            {"id": 2, "snapshot_id": 1, "seq_index": 2, "addr": "0002", "payload_json": "{}"},
        ]
        self.events = [
            {"id": 1, "ts": "2026-09-01T10:00:00", "afn": "06", "fn": "F2",
             "event_type": "F1从节点信息", "payload_json": "{}"},
        ]

    def verify(self, task):
        return {"task_id": task.get("id", "t"), "summary": {"verdict": "pass"}}

    def step(self, payload):
        return {"run_id": "manual-1"}

    def frames(self, **filters):
        return {"session_id": "sc-1", "entries": [], "counts": {}}

    def session(self):
        return {"current": None, "sessions": []}

    def open(self, spec=None):
        return {"open": True, "session_id": "sc-1"}

    def close(self):
        return {"open": False}

    # ---- REQS-0018 store 访问器 ----
    def store_snapshots(self, *, afn=None, fn=None, limit=20):
        items = self.snapshots
        if afn:
            items = [x for x in items if x["afn"] == afn]
        if fn:
            items = [x for x in items if x["fn"] == fn]
        return {"items": items[:limit]}

    def store_snapshot_items(self, snapshot_id):
        # 与真实 store 一致：不存在返回空列表（不抛错）
        return {"items": [x for x in self.items if x["snapshot_id"] == snapshot_id]}

    def store_events(self, *, limit=50):
        return {"items": self.events[:limit]}


class FakeMinuteLogService(FakeListenerLogService):
    """带 list_task_minute_periods 的侦听台日志服务（分桶口径与页面同方法）。"""

    def __init__(self):
        super().__init__()
        self.periods = [
            {"period_start": 1000, "period_end": 121000, "report_count": 1,
             "reports": [{"frame_id": 1, "station_key": "0001",
                          "freeze_time": "2026-08-31 14:22:00",
                          "freeze_ok": True, "data_status": "ok"}]},
        ]

    def list_task_minute_periods(self, *, task_no, period_minutes=None, cco_tei="001",
                                 nid="", start_time="", end_time=""):
        if int(task_no) == 999:
            raise ValueError("任务号不存在")
        if int(task_no) == 0:
            raise LookupError("任务不存在")
        return list(self.periods)


def _module_factory(core, with_store=True):
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    app.state.simcon_run_verify = core.verify
    app.state.simcon_run_step = core.step
    app.state.simcon_frames = core.frames
    app.state.simcon_session = core.session
    app.state.simcon_open = core.open
    app.state.simcon_close_io = core.close
    if with_store:
        app.state.simcon_store_snapshots = core.store_snapshots
        app.state.simcon_store_snapshot_items = core.store_snapshot_items
        app.state.simcon_store_events = core.store_events
    return app


def _plain_module_factory():
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    return app


def _listener_factory_with_minute():
    app = FastAPI()
    app.state.log_service = FakeMinuteLogService()
    return app


def _listener_factory_plain():
    return FastAPI()


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def isolated_ai_control_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))


def _client(core=None, scopes=None, resources=None, minute=False, with_store=True):
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=scopes or ["simcon:read", "evidence:read"],
        resources=resources or ["*"], ttl_seconds=60, created_by="human",
    )
    app = create_workbench_app(
        module_log_factory=(_plain_module_factory if core is None
                            else lambda: _module_factory(core, with_store=with_store)),
        listener_factory=(_listener_factory_with_minute if minute
                          else _listener_factory_plain),
        ai_auth_store=auth,
    )
    return TestClient(app), token


class TestStoreEvents:
    def test_events_200(self):
        client, token = _client(FakeStoreCore())
        r = client.get("/api/ai/v1/simcon/store/events", headers=_auth_header(token))
        assert r.status_code == 200
        body = r.json()
        assert body["items"][0]["afn"] == "06"
        assert body["items"][0]["event_type"] == "F1从节点信息"

    def test_events_limit_passthrough(self):
        client, token = _client(FakeStoreCore())
        r = client.get("/api/ai/v1/simcon/store/events", params={"limit": 1},
                       headers=_auth_header(token))
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_events_requires_simcon_read_scope(self):
        client, token = _client(FakeStoreCore(), scopes=["evidence:read"])
        r = client.get("/api/ai/v1/simcon/store/events", headers=_auth_header(token))
        assert r.status_code == 403

    def test_events_missing_token_401(self):
        client, _ = _client(FakeStoreCore())
        assert client.get("/api/ai/v1/simcon/store/events").status_code == 401

    def test_events_without_store_503(self):
        # simcon 核心在、store 访问器未装配 → SimconAIService._store_call → 503
        client, token = _client(FakeStoreCore(), with_store=False)
        r = client.get("/api/ai/v1/simcon/store/events", headers=_auth_header(token))
        assert r.status_code == 503


class TestStoreSnapshots:
    def test_snapshots_200(self):
        client, token = _client(FakeStoreCore())
        r = client.get("/api/ai/v1/simcon/store/snapshots", headers=_auth_header(token))
        assert r.status_code == 200
        assert [x["id"] for x in r.json()["items"]] == [1, 2]

    def test_snapshots_filter(self):
        client, token = _client(FakeStoreCore())
        r = client.get("/api/ai/v1/simcon/store/snapshots",
                       params={"afn": "10", "fn": "F2"}, headers=_auth_header(token))
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1
        assert r.json()["items"][0]["id"] == 1

    def test_snapshot_items_200(self):
        client, token = _client(FakeStoreCore())
        r = client.get("/api/ai/v1/simcon/store/snapshots/1", headers=_auth_header(token))
        assert r.status_code == 200
        assert len(r.json()["items"]) == 2

    def test_snapshot_items_unknown_returns_empty(self):
        # 与真实 store 一致：不存在快照 → 200 + 空 items（无 404）
        client, token = _client(FakeStoreCore())
        r = client.get("/api/ai/v1/simcon/store/snapshots/999", headers=_auth_header(token))
        assert r.status_code == 200
        assert r.json()["items"] == []


class TestMinutePeriods:
    def test_minute_periods_200(self):
        client, token = _client(FakeStoreCore(), minute=True)
        r = client.get("/api/ai/v1/listener/minute-periods", params={"task_no": 1},
                       headers=_auth_header(token))
        assert r.status_code == 200
        body = r.json()
        assert body["periods"][0]["report_count"] == 1
        assert body["periods"][0]["reports"][0]["freeze_time"] == "2026-08-31 14:22:00"

    def test_minute_periods_requires_task_no(self):
        client, token = _client(FakeStoreCore(), minute=True)
        r = client.get("/api/ai/v1/listener/minute-periods", headers=_auth_header(token))
        assert r.status_code == 422

    def test_minute_periods_invalid_task_422(self):
        client, token = _client(FakeStoreCore(), minute=True)
        r = client.get("/api/ai/v1/listener/minute-periods", params={"task_no": 999},
                       headers=_auth_header(token))
        assert r.status_code == 422

    def test_minute_periods_missing_log_service_503(self):
        client, token = _client(FakeStoreCore(), minute=False)
        r = client.get("/api/ai/v1/listener/minute-periods", params={"task_no": 1},
                       headers=_auth_header(token))
        assert r.status_code == 503

    def test_minute_periods_requires_evidence_scope(self):
        client, token = _client(FakeStoreCore(), scopes=["simcon:read"], minute=True)
        r = client.get("/api/ai/v1/listener/minute-periods", params={"task_no": 1},
                       headers=_auth_header(token))
        assert r.status_code == 403

    def test_minute_periods_missing_token_401(self):
        client, _ = _client(FakeStoreCore(), minute=True)
        assert client.get("/api/ai/v1/listener/minute-periods",
                          params={"task_no": 1}).status_code == 401


# ---------------------------------------------------------------------------
# REQS-0003 P0-3：OperationStore.list_operations 只读列表能力（store 侧单元测试）
# ---------------------------------------------------------------------------

def _seed_operation(store, *, kind: str, created_at: str, state: str = "created",
                    error: str | None = None, payload: dict | None = None) -> str:
    operation = store.create(kind, "ai:test", payload or {"marker": "内部 payload 不外泄"})
    if state != "created":
        store.set_state(operation["operation_id"], state, error=error)
    elif error is not None:
        with store._lock:
            store._operations[operation["operation_id"]]["error"] = error
    with store._lock:
        store._operations[operation["operation_id"]]["created_at"] = created_at
    return operation["operation_id"]


class TestListOperations:
    def test_orders_newest_first(self):
        store = OperationStore()
        oldest = _seed_operation(store, kind="investigation",
                                 created_at="2026-01-01T00:00:01+00:00")
        newest = _seed_operation(store, kind="flash_job",
                                 created_at="2026-01-01T00:00:03+00:00")
        middle = _seed_operation(store, kind="verification_run",
                                 created_at="2026-01-01T00:00:02+00:00")

        rows = store.list_operations()

        assert [row["operation_id"] for row in rows] == [newest, middle, oldest]

    def test_filters_kind_and_state(self):
        store = OperationStore()
        flash = _seed_operation(store, kind="flash_job", state="succeeded",
                                created_at="2026-01-01T00:00:01+00:00")
        verify = _seed_operation(store, kind="verification_run", state="error",
                                 error="模拟失败", created_at="2026-01-01T00:00:02+00:00")
        _seed_operation(store, kind="investigation", state="running",
                        created_at="2026-01-01T00:00:03+00:00")

        rows = store.list_operations(kinds={"flash_job", "verification_run"})
        assert {row["operation_id"] for row in rows} == {flash, verify}

        rows = store.list_operations(states={"error"})
        assert [row["operation_id"] for row in rows] == [verify]
        assert rows[0]["error"] == "模拟失败"

        # 空集合是"白名单为空"，不是"不过滤"
        assert store.list_operations(kinds=set()) == []
        assert store.list_operations(states=set()) == []

    def test_since_is_inclusive_and_understands_offsets(self):
        store = OperationStore()
        boundary = _seed_operation(store, kind="investigation",
                                   created_at="2026-05-01T10:00:00+00:00")
        _seed_operation(store, kind="investigation",
                        created_at="2026-05-01T09:59:59+00:00")

        # 同值（含 +08:00 等价的 18:00+08:00）都算 >=，边界包含
        assert [row["operation_id"] for row in store.list_operations(
            since="2026-05-01T10:00:00+00:00")] == [boundary]
        assert [row["operation_id"] for row in store.list_operations(
            since="2026-05-01T18:00:00+08:00")] == [boundary]
        # 晚一微秒就排除
        assert store.list_operations(since="2026-05-01T10:00:00.000001+00:00") == []

    def test_invalid_since_raises_value_error(self):
        store = OperationStore()
        _seed_operation(store, kind="investigation",
                        created_at="2026-05-01T10:00:00+00:00")
        for bad in ("not-a-time", "", "2026-13-01T00:00:00+00:00"):
            with pytest.raises(ValueError):
                store.list_operations(since=bad)

    def test_limit_hard_cap_is_100(self):
        store = OperationStore()
        for index in range(150):
            _seed_operation(store, kind="investigation",
                            created_at=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00")

        assert len(store.list_operations(limit=500)) == 100
        assert len(store.list_operations(limit=101)) == 100
        assert len(store.list_operations()) == 20
        assert len(store.list_operations(limit=3)) == 3

    def test_non_positive_limit_returns_empty(self):
        store = OperationStore()
        _seed_operation(store, kind="investigation",
                        created_at="2026-05-01T10:00:00+00:00")
        assert store.list_operations(limit=0) == []
        assert store.list_operations(limit=-5) == []

    def test_returns_deep_copy_and_summary_only(self):
        store = OperationStore()
        operation = store.create(
            "investigation", "ai:test",
            {"observations": [{"source": "listener", "secret": "不应出现在列表"}]},
        )
        store.set_state(operation["operation_id"], "succeeded", result={"raw": "结果不外泄"})
        with store._lock:
            store._operations[operation["operation_id"]]["created_at"] = (
                "2026-05-01T10:00:00+00:00"
            )
        operation_id = operation["operation_id"]
        before = store.get(operation_id)
        assert before["result"] == {"raw": "结果不外泄"}  # 内部确实存了 result

        rows = store.list_operations()

        assert len(rows) == 1
        row = rows[0]
        assert set(row) == {
            "operation_id", "kind", "actor", "state", "created_at", "updated_at",
            "client_request_id", "error",
        }
        assert "payload" not in row and "result" not in row
        assert "不应出现在列表" not in repr(rows)
        assert "结果不外泄" not in repr(rows)

        row["state"] = "tampered"
        row["error"] = "tampered"
        row["created_at"] = "1970-01-01T00:00:00+00:00"
        after = store.get(operation_id)
        assert after["state"] == before["state"]
        assert after["error"] == before["error"]
        assert after["created_at"] == before["created_at"]
