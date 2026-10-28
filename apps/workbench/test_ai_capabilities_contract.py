"""能力清单「人话描述」契约测试：summary/requires 全填、可序列化、向后兼容。"""
from __future__ import annotations

from workbench.ai_auth import V2_CAPABILITY_SCOPES
from workbench.ai_contracts import AccessContext, AccessZone, Capability
from workbench.ai_v2_api import (
    _CALL_EXAMPLES,
    _CAPABILITY_REQUIRES,
    _CAPABILITY_SUMMARIES,
    capability_snapshot,
)


class _StubControl:
    """只提供 capability_snapshot 需要的源服务探针，不触任何真实串口。"""

    module_service = None
    listener_service = object()
    simcon_service = object()
    simeter_service = object()


def _snapshot():
    return capability_snapshot(
        context=AccessContext(zone=AccessZone.LOCAL_FULL, actor="local:loopback"),
        control=_StubControl(),
        grant=None,
    )


def test_capability_new_fields_are_additive_and_optional():
    """纯加法：旧 payload（无 summary/requires）仍可反序列化，新字段有默认值。"""
    legacy = Capability(name="capabilities.read", allowed=True,
                        resources=[], reason=None, call_examples=[])
    assert legacy.summary == ""
    assert legacy.requires == []

    parsed = Capability.model_validate({
        "name": "capabilities.read", "allowed": True, "resources": [],
        "reason": None, "call_examples": [],
    })
    assert parsed.summary == ""
    assert parsed.requires == []


def test_all_eleven_capabilities_carry_summary_and_requires():
    """11 项能力逐项校验：summary 非空且 ≤120 字、requires 是 list[str]。"""
    snapshot = _snapshot()
    caps = {item.name: item for item in snapshot.capabilities}

    assert len(caps) == 11
    assert set(caps) == set(V2_CAPABILITY_SCOPES)
    for name, item in caps.items():
        assert item.summary.strip(), f"{name} 缺 summary"
        assert len(item.summary) <= 120, f"{name} summary 超过 120 字"
        assert isinstance(item.requires, list), f"{name} requires 不是 list"
        assert all(isinstance(text, str) and text.strip() for text in item.requires)
        # 既有字段：11 项都应有最小调用链示例
        assert item.call_examples, f"{name} 缺 call_examples"


def test_summary_tables_cover_every_capability_name():
    """防止以后新增能力只加名字、漏填描述（表键必须与能力名集合一致）。"""
    names = set(V2_CAPABILITY_SCOPES)
    assert set(_CAPABILITY_SUMMARIES) == names
    assert set(_CAPABILITY_REQUIRES) == names
    assert set(_CALL_EXAMPLES) == names


def test_descriptions_state_the_session_dependency():
    """flash/send/stop 必须写明依赖 ensure 返回的 session_id，AI 才不会直接裸调。"""
    for name in ("flash_jobs.create", "module_actions.send", "module_actions.stop"):
        requires = _CAPABILITY_REQUIRES[name]
        assert any("session_id" in text for text in requires), name
    assert "session_id" in _CAPABILITY_SUMMARIES["module_actions.ensure"]


def test_snapshot_serialises_with_new_fields_and_keeps_revision():
    snapshot = _snapshot()
    payload = snapshot.model_dump(mode="json")

    assert payload["capability_revision"] == "ai-v2-p1"
    for item in payload["capabilities"]:
        assert isinstance(item["summary"], str) and item["summary"]
        assert isinstance(item["requires"], list)
