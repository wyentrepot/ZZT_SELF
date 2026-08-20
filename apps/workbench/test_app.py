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
    @app.get("/api/module-serial/version")
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
    # 异步执行：POST 立即返回 running，轮询直到终态
    assert run["status"] in ("running", "passed", "failed")
    import time
    for _ in range(100):
        st = client.get(f"/api/run/{run['run_id']}").json()["status"]
        if st in ("passed", "failed", "cancelled", "error"):
            break
        time.sleep(0.02)
    assert st in ("passed", "failed")

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


def test_run_cancel_flow(client, monkeypatch, tmp_path):
    """任务4 取消 Run：POST 启动 → cancel → 终态 cancelled + report 标注被取消。

    用 monkeypatch 让 _run_steps 阻塞（模拟耗时步骤），确保 cancel 时 Run 还在跑。
    """
    import time
    import threading
    from workbench.orchestration.runner import RunExecutor

    # 让 _run_steps 等待一个事件（模拟耗时步骤，期间可取消）
    started = threading.Event()
    release = threading.Event()

    def _slow_steps(self, run, run_input, scenario, cancel_event=None):
        started.set()
        # 等待直到被取消或释放
        while not (cancel_event is not None and cancel_event.is_set()):
            if release.wait(timeout=0.02):
                break
        if cancel_event is not None and cancel_event.is_set():
            from workbench.orchestration.runner import RunCancelled
            raise RunCancelled(run.run_id)
        from workbench.orchestration.models import Report
        return Report(run_id=run.run_id, verdict="pass")

    monkeypatch.setattr(RunExecutor, "_run_steps", _slow_steps)

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    r = client.post(
        "/api/run",
        json={"scenario_id": "join_anhui", "log_dir": str(log_dir),
              "skip_flash": True, "skip_stimulus": True},
    )
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    # 等 Run 真正进入 _run_steps
    assert started.wait(timeout=2), "Run 应进入执行步骤"

    # 取消
    rc = client.post(f"/api/run/{run_id}/cancel")
    assert rc.status_code == 200
    assert rc.json()["status"] == "cancelling"

    # 轮询直到终态 cancelled
    for _ in range(100):
        st = client.get(f"/api/run/{run_id}").json()["status"]
        if st in ("cancelled", "passed", "failed", "error"):
            break
        time.sleep(0.02)
    assert st == "cancelled", f"期望 cancelled，实际 {st}"

    # report 标注被取消
    rep = client.get(f"/api/run/{run_id}/report").json()
    assert rep["run_id"] == run_id
    assert any(a["id"] == "run.cancelled" for a in rep["assertions"])
    release.set()


def test_run_cancel_non_running_returns_409(client, tmp_path):
    """取消一个不存在的 Run → 404；已完成 Run 不可取消。"""
    r = client.post("/api/run/nonexistent/cancel")
    assert r.status_code == 404


def test_static_assets_complete(client):
    """B-03 静态资源完整性：HTML 引用的 /static/ 资源都能 serve 200。"""
    from workbench.check_assets import check_assets, _referenced_static_paths

    # 打包门禁校验：无缺失/空文件/引用断裂
    assert check_assets() == 0

    # 每个被引用的静态资源经 HTTP 可达
    for rel in _referenced_static_paths():
        rel = rel.split("?", 1)[0]
        if not rel:
            continue
        r = client.get("/static/" + rel)
        assert r.status_code == 200, f"/static/{rel} 应 200，实际 {r.status_code}"


def test_static_all_pages_served(client):
    """B-03：关键页面与子应用页面资源全部 200。"""
    pages = [
        "/static/index.html",
        "/static/app.js",
        "/static/tokens.css",
        "/static/styles.css",
        "/static/workbench.html",
        "/static/pages/listener/index.html",
        "/static/pages/listener/app.js",
        "/static/pages/listener/styles.css",
        "/static/pages/module-serial/module-serial.html",
        "/static/pages/module-serial/module-serial.js",
        "/static/pages/module-serial/styles.css",
    ]
    for p in pages:
        r = client.get(p)
        assert r.status_code == 200, f"{p} 应 200，实际 {r.status_code}"

def test_shell_keeps_lazy_iframes_instead_of_reassigning_one_frame():
    static = Path(__file__).resolve().parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")

    assert 'id="wb-panels"' in html
    assert 'id="wb-frame"' not in html
    assert "framesByPage" in js
    assert "ensureFrame" in js
    # 仅在 ensureFrame 首次创建时赋 src；切页函数不能重设已存在页面。
    assert js.count("frame.src = page.src") == 1
    switch_body = js.split("function switchTab(id)", 1)[1].split("const THEMES", 1)[0]
    assert "frame.src = page.src" not in switch_body


class _RegistryAwareSerialService:
    def __init__(self):
        self.registry = None

    def set_resource_registry(self, registry):
        self.registry = registry


def test_workbench_injects_one_registry_into_listener_and_module_services(tmp_path):
    from fastapi import FastAPI

    module_service = _RegistryAwareSerialService()
    listener_service = _RegistryAwareSerialService()

    def module_factory():
        app = FastAPI()
        app.state.module_serial_service = module_service
        return app

    def listener_factory():
        app = FastAPI()
        app.state.serial_service = listener_service
        app.state.log_service = object()
        return app

    app = create_workbench_app(
        module_log_factory=module_factory,
        listener_factory=listener_factory,
        ai_storage_dir=tmp_path / "ai-control",
    )

    assert app.state.serial_resource_registry is module_service.registry
    assert module_service.registry is listener_service.registry
