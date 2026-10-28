"""simcon 统一准入闸门测试：verify / step / open / close 互斥 + 409 持有者信息 + lease 端点。

全部走假执行核心（复用 test_ai_simcon 的 FakeSimconCore），不打开串口、不发任何帧。
"""
from __future__ import annotations

import threading
import time

import pytest

from workbench.test_ai_simcon import FakeSimconCore, _auth_header, _client


class LeaseSimconCore(FakeSimconCore):
    """在既有 Fake 上补两点：可阻塞的 step（制造「step 在跑」）、可失败的 verify。"""

    def __init__(self):
        super().__init__()
        self.step_entered = threading.Event()
        self.step_release = threading.Event()

    def verify(self, task):
        if task.get("explode"):
            self.verify_calls.append(task)
            raise RuntimeError("底层串口写失败")
        return super().verify(task)

    def step(self, payload):
        if payload.get("block"):
            self.step_calls.append(payload)
            self.step_entered.set()
            assert self.step_release.wait(timeout=5), "被阻塞的 step 未在测试内释放"
            return {"run_id": "manual-block", "step": {"result": "pass"}, "session_id": "sc-1"}
        return super().step(payload)


@pytest.fixture(autouse=True)
def _isolated_ai_control_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.delenv("WORKBENCH_LOCAL_FULL_ACCESS", raising=False)
    monkeypatch.delenv("WORKBENCH_VERIFY_TIMEOUT_S", raising=False)


def _start_slow_verify(client, headers, *, request_id="slow"):
    """起一个卡在底层 verify 里的任务：闸门在返回 202 前就已登记持有者。"""
    response = client.post(
        "/api/ai/v1/simcon/verify",
        json={"id": request_id, "slow": True, "steps": []}, headers=headers,
    )
    assert response.status_code == 202
    return response.json()["operation_id"]


def _finish_verify(client, headers, core, operation_id):
    core._slow.set()
    waited = client.get(f"/api/ai/v1/operations/{operation_id}/wait?timeout_seconds=5",
                        headers=headers)
    assert waited.status_code == 200
    assert waited.json()["state"] == "succeeded"


def _lease(client, headers):
    response = client.get("/api/ai/v2/simcon/lease", headers=headers)
    assert response.status_code == 200
    return response.json()


def _wait_until_released(client, headers, timeout=2.0):
    """终态写入与持有权释放之间有一瞬窗口，轮询到空闲为止（lease 端点本身不取闸门）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _lease(client, headers)["held"]:
            return
        time.sleep(0.01)
    raise AssertionError("终态后闸门仍未释放")


class TestUnifiedGate:
    def test_verify_blocks_step_open_close_with_holder_info(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        operation_id = _start_slow_verify(client, headers)
        try:
            for path, body in (
                ("/api/ai/v1/simcon/step", {"send": {"afn": "00", "fn": 1}}),
                ("/api/ai/v1/simcon/open", {"mapping_id": "simcon"}),
                ("/api/ai/v1/simcon/close", None),
            ):
                blocked = client.post(path, json=body, headers=headers)
                assert blocked.status_code == 409, path
                detail = blocked.json()["detail"]
                assert "actor=ai:" in detail, detail
                assert "动作=verify" in detail, detail
                assert "已运行" in detail, detail
                assert f"operation={operation_id}" in detail, detail
            # 被拒的 step 根本没有碰执行核心（没有插帧的机会）
            assert core.step_calls == []
            assert _lease(client, headers)["holder"]["kind"] == "verify"
        finally:
            _finish_verify(client, headers, core, operation_id)

    def test_step_blocks_verify(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        captured: dict = {}

        def run_step():
            captured["response"] = client.post(
                "/api/ai/v1/simcon/step",
                json={"block": True, "send": {"afn": "00", "fn": 1, "params": {}}},
                headers=headers,
            )

        worker = threading.Thread(target=run_step, daemon=True)
        worker.start()
        try:
            assert core.step_entered.wait(timeout=5), "step 未进入执行核心"
            blocked = client.post("/api/ai/v1/simcon/verify",
                                  json={"id": "t2", "steps": []}, headers=headers)
            assert blocked.status_code == 409
            detail = blocked.json()["detail"]
            assert "动作=step" in detail, detail
            assert "actor=ai:" in detail, detail
            assert "operation=op-" in detail, detail
        finally:
            core.step_release.set()
            worker.join(timeout=5)
        assert not worker.is_alive()
        assert captured["response"].status_code == 200

        accepted = client.post("/api/ai/v1/simcon/verify",
                               json={"id": "t3", "steps": []}, headers=headers)
        assert accepted.status_code == 202
        waited = client.get(
            f"/api/ai/v1/operations/{accepted.json()['operation_id']}/wait?timeout_seconds=5",
            headers=headers,
        )
        assert waited.json()["state"] == "succeeded"

    def test_gate_released_after_normal_completion(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        operation_id = _start_slow_verify(client, headers)
        _finish_verify(client, headers, core, operation_id)
        _wait_until_released(client, headers)

        step = client.post("/api/ai/v1/simcon/step",
                           json={"send": {"afn": "00", "fn": 1, "params": {}}}, headers=headers)
        assert step.status_code == 200
        opened = client.post("/api/ai/v1/simcon/open", json={}, headers=headers)
        assert opened.status_code == 200
        closed = client.post("/api/ai/v1/simcon/close", headers=headers)
        assert closed.status_code == 200
        assert _lease(client, headers)["held"] is False

    def test_gate_released_after_exception(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)

        failed = client.post("/api/ai/v1/simcon/verify",
                             json={"id": "x", "explode": True, "steps": []}, headers=headers)
        assert failed.status_code == 202
        waited = client.get(
            f"/api/ai/v1/operations/{failed.json()['operation_id']}/wait?timeout_seconds=5",
            headers=headers,
        )
        assert waited.json()["state"] == "error"
        assert "底层串口写失败" in str(waited.json().get("error") or "")
        _wait_until_released(client, headers)

        ok = client.post("/api/ai/v1/simcon/step",
                         json={"send": {"afn": "00", "fn": 1, "params": {}}}, headers=headers)
        assert ok.status_code == 200

        # step 自身异常（串口被占）同样不许把持有权留在桌上
        busy = client.post("/api/ai/v1/simcon/step", json={"boom": True}, headers=headers)
        assert busy.status_code == 409
        assert _lease(client, headers)["held"] is False
        again = client.post("/api/ai/v1/simcon/step",
                            json={"send": {"afn": "00", "fn": 1, "params": {}}}, headers=headers)
        assert again.status_code == 200

    def test_concurrent_verify_still_409(self):
        """原有 verify 撞 verify 行为不回归：仍是 409，且现在带持有者信息。"""
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        operation_id = _start_slow_verify(client, headers, request_id="first")
        try:
            second = client.post("/api/ai/v1/simcon/verify",
                                 json={"id": "second", "steps": []}, headers=headers)
            assert second.status_code == 409
            assert "动作=verify" in second.json()["detail"]
            assert len(core.verify_calls) == 1
        finally:
            _finish_verify(client, headers, core, operation_id)


class TestLeaseEndpoint:
    def test_idle(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        body = _lease(client, _auth_header(token))
        assert body == {"held": False, "holder": None, "policy": "no_queue_reject"}

    def test_held_shows_actor_kind_and_elapsed(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core)
        headers = _auth_header(token)
        operation_id = _start_slow_verify(client, headers)
        try:
            time.sleep(0.2)  # 单调时钟走一点，elapsed_seconds 必须 > 0
            body = _lease(client, headers)
            assert body["held"] is True
            assert body["policy"] == "no_queue_reject"
            holder = body["holder"]
            assert holder["actor"].startswith("ai:")
            assert holder["kind"] == "verify"
            assert holder["operation_id"] == operation_id
            assert holder["elapsed_seconds"] > 0
            assert holder["thread"] == "ai-simcon-verify"
            assert holder["started_at"]
            # 查一次不影响在跑任务：闸门仍被占，step 仍 409
            blocked = client.post("/api/ai/v1/simcon/step",
                                  json={"send": {"afn": "00", "fn": 1}}, headers=headers)
            assert blocked.status_code == 409
        finally:
            _finish_verify(client, headers, core, operation_id)

    def test_requires_simcon_read_scope(self):
        core = LeaseSimconCore()
        client, token, _ = _client(core, scopes=["simcon:verify"])
        headers = _auth_header(token)
        assert client.get("/api/ai/v2/simcon/lease").status_code == 401
        denied = client.get("/api/ai/v2/simcon/lease", headers=headers)
        assert denied.status_code == 403
        assert "simcon:read" in denied.json()["detail"]
