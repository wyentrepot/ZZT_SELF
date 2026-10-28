"""N-1a/N-1b：v1/v2 烧录白名单统一与「凭什么被允许」审计。

统一口径（两平面共用 ``resolve_flash_authorization``）：grant.firmware_roots
优先 → ``WORKBENCH_AI_FIRMWARE_ROOTS`` 兜底 → 两者都空 403 → 路径不在白名单
403。校验通过后由 ``record_flash_authorization`` 往 audit 写
``authorization_source``（grant / env / local_full_env）+ actor + bin_path + root。
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from workbench.ai_auth import AuthorizationStore
from workbench.ai_operations import AIControlService
from workbench.app import create_workbench_app
from workbench.test_ai_operations import FakeModuleService


class _CompletingModuleService(FakeModuleService):
    """flash_session 立即完成，避免 v2 后台 monitor 线程在测试里常驻。"""

    def flash_session(self, session_id, bin_path, slot=0, baud_plan=None, no_reboot_after=False):
        session = self.sessions[session_id]
        session["flash"] = {"flashing": False, "phase": "done", "message": bin_path}
        return {"session_id": session_id, "flash": dict(session["flash"])}


def _module_factory():
    app = FastAPI()
    app.state.module_serial_service = _CompletingModuleService()
    return app


def _listener_factory():
    return FastAPI()


def _plain_simcon_factory():
    return FastAPI()


@pytest.fixture(autouse=True)
def _isolated_flash_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
    monkeypatch.delenv("WORKBENCH_LOCAL_FULL_ACCESS", raising=False)
    monkeypatch.delenv("WORKBENCH_AI_FIRMWARE_ROOTS", raising=False)


def _app(*, auth: AuthorizationStore):
    return create_workbench_app(
        ai_control_service=AIControlService(module_service=_CompletingModuleService()),
        ai_auth_store=auth,
        module_log_factory=_module_factory,
        simcon_factory=_plain_simcon_factory,
        listener_factory=_listener_factory,
    )


def _grant_with_token(*, firmware_roots=()) -> tuple[AuthorizationStore, str]:
    auth = AuthorizationStore()
    _, token = auth.create_grant(
        scopes=["module_flash:execute", "status:read"], resources=["cco-main"],
        ttl_seconds=60, created_by="human", firmware_roots=list(firmware_roots),
    )
    return auth, token


def _bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post_v1(client: TestClient, token: str | None, path, request_id: str):
    return client.post(
        "/api/ai/v1/flash-operations",
        json={"session_id": "ms-cco", "bin_path": str(path), "client_request_id": request_id},
        headers=_bearer(token),
    )


def _post_v2(client: TestClient, token: str | None, path, request_id: str):
    return client.post(
        "/api/ai/v2/flash-jobs",
        json={"session_id": "ms-cco", "bin_path": str(path), "client_request_id": request_id},
        headers=_bearer(token),
    )


def _flash_audits(app) -> list[dict]:
    return [
        entry for entry in app.state.ai_control_service.store.audit_entries()
        if entry["action"] == "flash.authorization"
    ]


def _firmware(tmp_path, name: str = "app.bin"):
    root = tmp_path / "firmware"
    root.mkdir(exist_ok=True)
    firmware = root / name
    firmware.write_bytes(b"fake")
    return root, firmware


def test_grant_roots_win_on_both_planes_and_audit_source_grant(tmp_path, monkeypatch):
    """① grant 有 firmware_roots：v1/v2 都通过，source=grant（env 不参与）。"""
    root, firmware = _firmware(tmp_path)
    monkeypatch.setenv("WORKBENCH_AI_FIRMWARE_ROOTS", str(tmp_path / "env-root"))
    auth, token = _grant_with_token(firmware_roots=[str(root)])
    app = _app(auth=auth)
    client = TestClient(app)

    v1 = _post_v1(client, token, firmware, "v1-grant")
    v2 = _post_v2(client, token, firmware, "v2-grant")

    assert v1.status_code == v2.status_code == 202
    assert v1.json()["state"] == "succeeded"
    # v2 受理后异步执行（running/succeeded 均可能），本用例只关心「已放行 + 审计口径」
    assert v2.json()["job_id"].startswith("job-")

    audits = _flash_audits(app)
    assert len(audits) == 2
    details = [json.loads(entry["result"]) for entry in audits]
    assert [item["authorization_source"] for item in details] == ["grant", "grant"]
    assert {item["firmware_root"] for item in details} == {str(root.resolve())}
    assert {item["bin_path"] for item in details} == {str(firmware.resolve())}
    assert all(entry["actor"].startswith("ai:") for entry in audits)
    assert [entry["operation_id"] for entry in audits] == [
        v1.json()["operation_id"], "op-" + v2.json()["job_id"].removeprefix("job-"),
    ]


def test_env_fallback_sources_env_for_v1_and_local_full_env_for_v2(tmp_path, monkeypatch):
    """② 无 grant roots、有 env：v1 标 env；v2 本机免 token 时标 local_full_env。"""
    root, firmware = _firmware(tmp_path, "env.bin")
    monkeypatch.setenv("WORKBENCH_AI_FIRMWARE_ROOTS", str(root))
    # local_full 在 create_workbench_app 时读取，须在建 app 前设置
    monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
    auth, token = _grant_with_token()
    app = _app(auth=auth)
    client = TestClient(app)

    v1 = _post_v1(client, token, firmware, "v1-env")
    v2 = _post_v2(client, None, firmware, "v2-env")

    assert v1.status_code == v2.status_code == 202
    audits = _flash_audits(app)
    assert [json.loads(entry["result"])["authorization_source"] for entry in audits] == [
        "env", "local_full_env",
    ]
    assert audits[0]["actor"].startswith("ai:")
    assert audits[1]["actor"] == "local:loopback"


def test_both_planes_reject_when_no_roots_are_configured(tmp_path):
    """③ grant 与 env 都没有 roots：两平面同样 403，且不落授权审计。"""
    _, firmware = _firmware(tmp_path)
    auth, token = _grant_with_token()
    app = _app(auth=auth)
    client = TestClient(app)

    v1 = _post_v1(client, token, firmware, "v1-noroots")
    v2 = _post_v2(client, token, firmware, "v2-noroots")

    assert v1.status_code == v2.status_code == 403
    assert v1.json()["detail"] == v2.json()["detail"] == "当前授权未配置允许烧录目录"
    assert _flash_audits(app) == []


def test_both_planes_reject_path_outside_roots(tmp_path):
    """④ 路径不在白名单：两平面同样 403，且不落授权审计。"""
    root, _ = _firmware(tmp_path)
    outside = tmp_path / "outside" / "app.bin"
    auth, token = _grant_with_token(firmware_roots=[str(root)])
    app = _app(auth=auth)
    client = TestClient(app)

    v1 = _post_v1(client, token, outside, "v1-outside")
    v2 = _post_v2(client, token, outside, "v2-outside")

    assert v1.status_code == v2.status_code == 403
    assert v1.json()["detail"] == v2.json()["detail"] == "固件路径不在当前授权的允许目录内"
    assert _flash_audits(app) == []


@pytest.mark.parametrize(
    ("grant_roots", "env_root", "inside", "expected_status"),
    [
        pytest.param(True, False, True, 202, id="grant-inside"),
        pytest.param(True, False, False, 403, id="grant-outside"),
        pytest.param(False, True, True, 202, id="env-inside"),
        pytest.param(False, True, False, 403, id="env-outside"),
        pytest.param(False, False, True, 403, id="no-roots"),
    ],
)
def test_v1_and_v2_reach_the_same_conclusion(
    monkeypatch, tmp_path, grant_roots, env_root, inside, expected_status,
):
    """⑤ 同一组输入（roots/path）下 v1 与 v2 结论与拒绝文案必须一致。"""
    root, firmware = _firmware(tmp_path)
    if env_root:
        monkeypatch.setenv("WORKBENCH_AI_FIRMWARE_ROOTS", str(root))
    auth, token = _grant_with_token(firmware_roots=[str(root)] if grant_roots else ())
    client = TestClient(_app(auth=auth))
    path = firmware if inside else (tmp_path / "elsewhere" / "app.bin")

    v1 = _post_v1(client, token, path, f"v1-{grant_roots}-{env_root}-{inside}")
    v2 = _post_v2(client, token, path, f"v2-{grant_roots}-{env_root}-{inside}")

    assert v1.status_code == expected_status
    assert v2.status_code == expected_status
    if expected_status != 202:
        assert v1.json()["detail"] == v2.json()["detail"]
