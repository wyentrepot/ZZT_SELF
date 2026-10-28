"""afn/fn 写法前置校验：codec + 直连 simcon API + AI v2/v1 四个入口。

背景：afn 写成 "10H"（协议库口语记法）时，旧行为是 202 收单成功，后台构帧
才抛 ValueError，step 结果记为 result=fail、sent_hex=""、reason="构帧失败:
..."，与「设备不响应」同形，污染用例结论。现在必须在**建 job 之前** 422。

本文件同时覆盖 workbench 的 AI 面（仓库级 conftest 已把 apps/ 加进 sys.path）；
全部用假 simcon 执行核心，不开串口、不发帧。
"""
from __future__ import annotations

import json

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from sim_concentrator.api import create_simcon_app
from sim_concentrator.scenario_codec import (
    ScenarioParamError,
    normalise_afn,
    normalise_fn,
    validate_send,
    validate_steps,
)
from workbench.ai_auth import AuthorizationStore
from workbench.ai_operations import AIControlService
from workbench.ai_store import OperationStore
from workbench.app import create_workbench_app
from workbench.test_ai_operations import FakeModuleService


# ---------------------------------------------------------------------------
# 1. 共享校验函数：合法/非法判定与可读报错
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("10", 0x10),          # 十六进制写法，语义必须保持 0x10
    ("0A", 0x0A),
    ("0x10", 0x10),        # 既有写法保留兼容
    (16, 16), (0, 0), (255, 255),
])
def test_normalise_afn_keeps_legal_semantics(value, expected):
    assert normalise_afn(value) == expected


@pytest.mark.parametrize("value", ["10H", "1Q", "", None, 300, -1, True])
def test_normalise_afn_rejects_illegal_with_writable_hint(value):
    with pytest.raises(ScenarioParamError) as excinfo:
        normalise_afn(value)
    message = str(excinfo.value)
    assert ("格式非法" in message or "为空" in message or "超出范围" in message)
    assert "合法写法" in message


@pytest.mark.parametrize("value,expected", [
    ("230", 230), ("F230", 230), ("f230", 230), ("F1", 1),
    (230, 230), (0, 0), (255, 255),
])
def test_normalise_fn_keeps_legal_semantics(value, expected):
    assert normalise_fn(value) == expected


@pytest.mark.parametrize("value", ["10H", "1Q", "", None, 300, "0A", "F"])
def test_normalise_fn_rejects_illegal_with_writable_hint(value):
    with pytest.raises(ScenarioParamError) as excinfo:
        normalise_fn(value)
    message = str(excinfo.value)
    assert ("格式非法" in message or "为空" in message or "超出范围" in message)
    assert "合法写法" in message


def test_validate_steps_reports_the_offending_field_path():
    with pytest.raises(ScenarioParamError) as excinfo:
        validate_steps([
            {"send": {"afn": "10", "fn": "2"}},
            {"send": {"afn": "10H", "fn": "2"}},
        ])
    message = str(excinfo.value)
    assert "steps[1].send.afn" in message
    assert "合法写法" in message


def test_validate_send_ignores_missing_keys_so_codec_reports_them():
    # 缺 afn/fn 仍由 build_send 报「send 缺 afn」，前置校验不抢语义。
    validate_send({})
    validate_send(None)


# ---------------------------------------------------------------------------
# 2. 直连 simcon 面：build / verify / step
# ---------------------------------------------------------------------------
@pytest.fixture
def simcon_client():
    return TestClient(create_simcon_app())


def _flatten(body) -> str:
    return json.dumps(body, ensure_ascii=False)


def test_build_rejects_bad_afn_with_422_and_example(simcon_client):
    response = simcon_client.post(
        "/api/simcon/build", json={"afn": "10H", "fn": "2", "params": {}})
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "格式非法" in text and "合法写法" in text


def test_build_rejects_bad_fn_with_422_and_example(simcon_client):
    response = simcon_client.post(
        "/api/simcon/build", json={"afn": "03", "fn": "1Q", "params": {}})
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "格式非法" in text and "合法写法" in text


def test_build_legal_inputs_keep_the_exact_same_frame_bytes(simcon_client):
    # 改动前实测输出（2026-10-28）：afn "10" / 16 / "0x10" 均为同一串字节。
    expected = "68 12 00 43 00 00 00 00 00 01 10 02 00 00 00 00 56 16"
    for afn in ("10", 16, "0x10"):
        response = simcon_client.post(
            "/api/simcon/build", json={"afn": afn, "fn": "2", "params": {}})
        assert response.status_code == 200, (afn, response.text)
        assert response.json()["hex"] == expected
    # F 前缀 fn 的取值语义不变：F10 -> 10（DT 同前）
    fn_prefixed = simcon_client.post(
        "/api/simcon/build", json={"afn": "03", "fn": "F10", "params": {}})
    assert fn_prefixed.status_code == 200
    assert fn_prefixed.json()["hex"] == "68 0F 00 43 00 00 00 00 00 01 03 02 01 4A 16"


def test_direct_verify_rejects_bad_step_afn_before_touching_serial(simcon_client):
    response = simcon_client.post("/api/simcon/verify", json={
        "id": "bad-verify",
        "steps": [{"name": "s1", "send": {"afn": "10H", "fn": "2"}}],
    })
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "steps[0].send.afn" in text
    assert "合法写法" in text


def test_direct_step_rejects_bad_afn_before_touching_serial(simcon_client):
    response = simcon_client.post(
        "/api/simcon/step", json={"send": {"afn": "10H", "fn": "2"}})
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "格式非法" in text and "合法写法" in text


# ---------------------------------------------------------------------------
# 3. AI 面：v2 verification-runs / v1 simcon verify+step（建 job 之前拦）
# ---------------------------------------------------------------------------
class _FakeSimconService:
    """假执行核心：记录调用，永不碰串口。"""

    def __init__(self):
        self.verify_calls = []
        self.step_calls = []

    def verify(self, request):
        self.verify_calls.append(request)
        return {"task_id": request.get("id", "t"),
                "summary": {"verdict": "pass", "total": 1, "pass": 1, "fail": 0}}

    def step(self, payload):
        self.step_calls.append(payload)
        return {"run_id": "manual-1", "step": {"result": "pass"}}


def _module_factory():
    app = FastAPI()
    app.state.module_serial_service = FakeModuleService()
    return app


def _plain_subapp():
    return FastAPI()


@pytest.fixture
def ai_client(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["simcon:verify", "simcon:send", "simcon:read"],
        resources=["*"], ttl_seconds=60, created_by="human",
    )
    fake = _FakeSimconService()
    control = AIControlService(simcon_service=fake, store=OperationStore())
    app = create_workbench_app(
        ai_control_service=control,
        ai_auth_store=auth,
        module_log_factory=_module_factory,
        simcon_factory=_plain_subapp,
        listener_factory=_plain_subapp,
    )
    return TestClient(app), {"Authorization": f"Bearer {token}"}, control, fake


def test_v2_verification_run_rejects_bad_afn_before_creating_a_job(ai_client):
    client, headers, control, fake = ai_client
    response = client.post("/api/ai/v2/verification-runs", headers=headers, json={
        "task": {"id": "bad", "steps": [{"send": {"afn": "10H", "fn": "2"}}]},
        "client_request_id": "bad-afn-1",
    })
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "格式非法" in text and "合法写法" in text
    # 关键：不许留下一个 failed job，也没进到执行核心
    assert control.store.list_operations() == []
    assert fake.verify_calls == []


def test_v2_verification_run_still_accepts_legal_afn(ai_client):
    client, headers, control, _fake = ai_client
    response = client.post("/api/ai/v2/verification-runs", headers=headers, json={
        "task": {"id": "ok", "steps": [{"send": {"afn": "10", "fn": "2"}}]},
        "client_request_id": "ok-afn-1",
    })
    assert response.status_code == 202, response.text
    assert response.json()["job_id"]
    kinds = sorted(item["kind"] for item in control.store.list_operations())
    # v2 父 job + 底层 simcon_verify operation 各一条，证明确实建了任务
    assert kinds == ["simcon_verify", "verification_run"]


def test_v1_verify_rejects_bad_step_afn_before_creating_an_operation(ai_client):
    client, headers, control, fake = ai_client
    response = client.post("/api/ai/v1/simcon/verify", headers=headers, json={
        "id": "bad-v1", "steps": [{"send": {"afn": "10H", "fn": "2"}}],
    })
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "格式非法" in text and "合法写法" in text
    assert control.store.list_operations() == []
    assert fake.verify_calls == []


def test_v1_step_rejects_bad_afn_before_creating_an_operation(ai_client):
    client, headers, control, fake = ai_client
    response = client.post("/api/ai/v1/simcon/step", headers=headers,
                           json={"send": {"afn": "10H", "fn": "2"}})
    assert response.status_code == 422
    text = _flatten(response.json())
    assert "格式非法" in text and "合法写法" in text
    assert control.store.list_operations() == []
    assert fake.step_calls == []
