"""workbench 统一应用挂载/路由测试（FR-6.1/6.2）。

验证：create_workbench_app 工厂、子应用挂载、编排路由、静态外壳。
listener 依赖 C# DLL，测试中注入轻量 stub 工厂避免真实依赖。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.app import create_workbench_app
from workbench.orchestration.store import RunStore


def _stub_listener_factory():
    from fastapi import FastAPI

    app = FastAPI(title="stub-listener")

    @app.get("/api/version")
    async def v():
        return {"app": "listener", "stub": True}

    return app


def _stub_module_log_factory():
    from fastapi import FastAPI

    app = FastAPI(title="stub-module-log")

    @app.get("/api/version")
    async def v():
        return {"app": "module-serial", "stub": True}

    return app


@pytest.fixture()
def client():
    app = create_workbench_app(
        listener_factory=_stub_listener_factory,
        module_log_factory=_stub_module_log_factory,
    )
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["app"] == "workbench"


def test_platform_version(client):
    r = client.get("/api/platform-version")
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "workbench"
    assert body["module_log_mounted"] is True
    assert body["listener_mounted"] is True


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AI 闭环研发验证工作台" in r.text


def test_static_served(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "workbench" in r.text


def test_module_log_mounted(client):
    # 方案①（ADR-18）：子应用经 ASGI 前缀代理挂到 /api/module-serial/*
    r = client.get("/api/module-serial/version")
    assert r.status_code == 200
    assert r.json()["app"] == "module-serial"


def test_listener_mounted(client):
    # 方案①（ADR-18）：子应用经 ASGI 前缀代理挂到 /api/listener/*
    r = client.get("/api/listener/version")
    assert r.status_code == 200
    assert r.json()["app"] == "listener"


def test_scenarios_api(client):
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()}
    assert "minute_collect" in ids


def test_run_api_end_to_end(client, tmp_path):
    """POST /api/run 全链路：假日志 + 跳过激励（无串口）。"""
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "cco.log").write_text(
        "[20260814-10:00:01:000] [RX] 1 | CCO | aps_ioctrl_nwk.c(950) | nwk disc done\n"
        "[20260814-10:00:02:000] [RX] 2 | CCO | aps_ioctrl_nwk.c(950) | onnet cnt = 1\n",
        encoding="utf-8",
    )
    r = client.post(
        "/api/run",
        json={
            "scenario_id": "join_anhui",
            "firmware": {"version": "v2.3.2", "commit": "9f2e1a"},
            "log_dir": str(log_dir),
            "skip_flash": True,
            "skip_stimulus": True,
        },
    )
    assert r.status_code == 200
    run = r.json()
    assert run["run_id"].startswith("run-")
    assert run["status"] in ("passed", "failed")

    # 可回溯：GET /api/run/{id} 与 /report
    r2 = client.get(f"/api/run/{run['run_id']}")
    assert r2.status_code == 200
    assert r2.json()["scenario_id"] == "join_anhui"

    r3 = client.get(f"/api/run/{run['run_id']}/report")
    assert r3.status_code == 200
    rep = r3.json()
    assert rep["run_id"] == run["run_id"]
    assert rep["verdict"] in ("pass", "fail")


def test_run_not_found(client):
    r = client.get("/api/run/nonexistent")
    assert r.status_code == 404


def test_compare_api(client):
    r = client.post(
        "/api/compare",
        json={
            "expected_flow": [
                {"step": "a", "event_type": "evt.a", "within_ms": 30000}
            ],
            "events": [{"type": "evt.a", "time": "10:00:01"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "pass"


def test_feedback_api(client):
    r = client.post(
        "/api/feedback",
        json={
            "flow_compare": {"missing": ["collect.minute.e4"], "verdict": "fail"},
        },
    )
    assert r.status_code == 200
    fb = r.json()
    assert isinstance(fb, list)
    assert any("采集任务" in o["suggestion"] for o in fb)
