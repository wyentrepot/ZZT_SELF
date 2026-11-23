"""P1-1 模型层测试（REQS v3 §10.2 / PLAN P1-1）。

覆盖点：
- 缺 ``action`` 的 BlockSpec、``blocks`` 为空的 CaseSpec → ValidationError（不静默跳过）
- ``steps[]`` 每项必须有 ``do`` 与 ``expect``（UI 展示口径）
- JSON 里写 ``assert`` 要能落到 ``assert_``，且 ``model_dump(by_alias=True)`` 还原成 ``assert``
- 事实三态常量与 ``Health`` / ``Capability`` 契约
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from case_runner.core.models import (
    FACT_NO,
    FACT_STATES,
    FACT_UNKNOWN,
    FACT_YES,
    BlockSpec,
    Capability,
    CaseSpec,
    CaseStep,
    FactSpec,
    Health,
    SuiteSpec,
)


def _block(**over) -> dict:
    data = {
        "id": "nwk.onnet_count",
        "title": "查询在网数",
        "action": {"afn": "10H", "fn": "F1"},
    }
    data.update(over)
    return data


def _case(**over) -> dict:
    data = {
        "id": "nwk_join",
        "title": "档案→组网",
        "steps": [{"no": 1, "do": "下发 10H-F1", "expect": "在网数 >= 1"}],
        "blocks": ["nwk.onnet_count"],
    }
    data.update(over)
    return data


# --- BlockSpec ---------------------------------------------------------------


def test_block_spec_defaults_and_action() -> None:
    block = BlockSpec(**_block())
    assert block.id == "nwk.onnet_count"
    assert block.action == {"afn": "10H", "fn": "F1"}
    assert block.needs == []
    assert block.provides == []
    assert block.assert_ == []
    assert block.cleanup == []
    assert block.tags == []


def test_block_spec_missing_action_raises() -> None:
    data = _block()
    del data["action"]
    with pytest.raises(ValidationError):
        BlockSpec(**data)


def test_block_spec_assert_alias_roundtrip() -> None:
    block = BlockSpec(**_block(**{"assert": [{"kind": "field", "path": "body.onnet_count", "op": ">=", "value": 1}]}))
    assert block.assert_[0]["path"] == "body.onnet_count"
    dumped = block.model_dump(by_alias=True)
    assert "assert" in dumped and "assert_" not in dumped
    assert dumped["assert"][0]["value"] == 1


def test_block_spec_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        BlockSpec(**_block(unexpected_field=1))


def test_block_spec_empty_id_rejected() -> None:
    with pytest.raises(ValidationError):
        BlockSpec(**_block(id=""))


# --- FactSpec ----------------------------------------------------------------


def test_fact_spec_requires_probe() -> None:
    fact = FactSpec(id="archive.contains", title="档案含地址", probe="archive.contains")
    assert fact.probe == "archive.contains"
    assert fact.params_schema == {}
    with pytest.raises(ValidationError):
        FactSpec(id="archive.contains", title="档案含地址")
    with pytest.raises(ValidationError):
        FactSpec(id="archive.contains", title="档案含地址", probe="", extra=1)


# --- CaseStep / CaseSpec -----------------------------------------------------


def test_case_step_requires_do_and_expect() -> None:
    assert CaseStep(no=1, do="a", expect="b").no == 1
    with pytest.raises(ValidationError):
        CaseStep(no=1, do="", expect="b")
    with pytest.raises(ValidationError):
        CaseStep(no=1, do="a", expect="")
    with pytest.raises(ValidationError):
        CaseStep(no=0, do="a", expect="b")


def test_case_spec_steps_must_be_non_empty_and_well_formed() -> None:
    case = CaseSpec(**_case())
    assert case.steps[0].expect == "在网数 >= 1"
    with pytest.raises(ValidationError):
        CaseSpec(**_case(steps=[]))
    with pytest.raises(ValidationError):
        CaseSpec(**_case(steps=[{"no": 1, "do": "只有动作"}]))
    with pytest.raises(ValidationError):
        CaseSpec(**_case(steps=[{"no": 1, "expect": "只有预期"}]))


def test_case_spec_empty_blocks_raises() -> None:
    with pytest.raises(ValidationError):
        CaseSpec(**_case(blocks=[]))


def test_case_spec_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        CaseSpec(**_case(frame_hex="7E7E"))


def test_case_spec_keeps_block_order() -> None:
    case = CaseSpec(**_case(blocks=["a.first", "b.second", "c.third"]))
    assert case.blocks == ["a.first", "b.second", "c.third"]


# --- SuiteSpec ---------------------------------------------------------------


def test_suite_spec_defaults() -> None:
    suite = SuiteSpec(id="smoke", name="冒烟", case_ids=["c1"])
    assert suite.stop_on_fail is False
    assert suite.notes == ""
    with pytest.raises(ValidationError):
        SuiteSpec(id="smoke", name="冒烟", case_ids=[])


# --- 事实三态 / Health / Capability ------------------------------------------


def test_fact_state_constants() -> None:
    assert (FACT_YES, FACT_NO, FACT_UNKNOWN) == ("yes", "no", "unknown")
    assert FACT_STATES == frozenset({"yes", "no", "unknown"})
    # unknown 必须与 yes 不同的是非值，绝不能被当作 pass
    assert FACT_UNKNOWN != FACT_YES


def test_health_is_frozen_dataclass() -> None:
    health = Health(ok=True)
    assert health.ok is True
    assert health.reason == ""
    with pytest.raises(Exception):
        health.ok = False  # type: ignore[misc]
    assert Health(ok=False, reason="串口桥不可用").reason == "串口桥不可用"


def test_capability_protocol_is_structural() -> None:
    class FakeSimcon:
        name = "simcon"

        def probe(self) -> Health:
            return Health(ok=False, reason="服务未启动")

    capability = FakeSimcon()
    assert isinstance(capability, Capability)
    assert capability.probe().ok is False

    class NotACapability:
        name = "nope"

    assert not isinstance(NotACapability(), Capability)
