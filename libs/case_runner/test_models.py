"""P1-1 模型层测试（REQS v3 §10.2 / PLAN P1-1）。

覆盖点：
- 缺 ``action`` 的 BlockSpec、``blocks`` 为空且无 ``plan`` 的 CaseSpec → ValidationError（不静默跳过）
- 步骤实例化（REQS-0042 D3-1）：``plan: [{id, block, params, on_fail}]`` 解析、
  step id 用例内唯一、``blocks`` / ``plan`` 至少给一个、两者都给以 ``plan`` 为准
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
    StepSpec,
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


# --- StepSpec / CaseSpec.plan（REQS-0042 D3-1 步骤实例化） --------------------


def _plan_case(**over) -> dict:
    """带 ``plan`` 的用例原文（旧写法 ``blocks`` 不出现 = 可选字段）。"""
    data = {
        "id": "archive_multi",
        "title": "多档下发",
        "steps": [{"no": 1, "do": "两次下发档案", "expect": "两块表都进档案"}],
        "plan": [
            {"id": "add_a", "block": "archive.add", "params": {"meter_addr": "000000000011"}},
            {
                "id": "add_b",
                "block": "archive.add",
                "params": {"meter_addr": "000000000022"},
                "on_fail": "continue",
                "note": "第二块表",
            },
        ],
    }
    data.update(over)
    return data


def test_step_spec_defaults() -> None:
    step = StepSpec(id="s1", block="archive.add")
    assert step.params == {}
    assert step.on_fail == "stop"
    assert step.note == ""
    # 没显式写 on_fail → 继承 run/方案级 stop_on_fail（旧用例行为不变的关键）
    assert step.declares_on_fail is False
    assert StepSpec(id="s1", block="b", on_fail="continue").declares_on_fail is True
    # 生效策略：显式声明优先；没声明时跟随 run 级 stop_on_fail（默认值语义）
    assert step.resolved_on_fail(True) == "stop"
    assert step.resolved_on_fail(False) == "continue"
    assert StepSpec(id="s1", block="b", on_fail="continue").resolved_on_fail(True) == "continue"


def test_step_spec_rejects_unknown_on_fail() -> None:
    with pytest.raises(ValidationError):
        StepSpec(id="s1", block="b", on_fail="maybe")


def test_step_spec_rejects_empty_id_or_block() -> None:
    with pytest.raises(ValidationError):
        StepSpec(id="", block="b")
    with pytest.raises(ValidationError):
        StepSpec(id="s1", block="")


def test_step_spec_rejects_reserved_param_keys() -> None:
    """``_assert`` / ``_cleanup`` 是执行器注入的保留键，用例里写会被静默覆盖 → 直接报错。"""
    with pytest.raises(ValidationError):
        StepSpec(id="s1", block="b", params={"_assert": []})
    with pytest.raises(ValidationError):
        StepSpec(id="s1", block="b", params={"_cleanup": []})


def test_case_spec_plan_is_parsed_and_blocks_is_optional() -> None:
    case = CaseSpec(**_plan_case())
    assert case.blocks == []          # D3-1：blocks 改为可选
    assert [step.id for step in case.plan] == ["add_a", "add_b"]
    assert case.plan[0].block == "archive.add"
    assert case.plan[0].params == {"meter_addr": "000000000011"}
    assert case.plan[1].on_fail == "continue"
    assert case.plan[1].note == "第二块表"
    assert case.uses_plan is True


def test_case_spec_requires_blocks_or_plan() -> None:
    """两者都缺 → 报错，且错误信息点名是哪个用例（不能只报一句 ValidationError）。"""
    with pytest.raises(ValidationError) as excinfo:
        CaseSpec(**_plan_case(plan=[]))
    message = str(excinfo.value)
    assert "archive_multi" in message
    assert "blocks" in message and "plan" in message


def test_case_spec_plan_duplicate_step_id_raises() -> None:
    with pytest.raises(ValidationError) as excinfo:
        CaseSpec(
            **_plan_case(
                plan=[
                    {"id": "dup", "block": "archive.add"},
                    {"id": "dup", "block": "archive.add"},
                ]
            )
        )
    message = str(excinfo.value)
    assert "archive_multi" in message
    assert "dup" in message


def test_case_spec_plan_and_blocks_both_given_prefers_plan() -> None:
    """两者都给 → 以 plan 为准（check 里给提示，不是错误）。"""
    case = CaseSpec(**_plan_case(blocks=["meter.read_645"]))
    assert case.blocks == ["meter.read_645"]          # 原样保留（供 check 提示）
    assert [step.block for step in case.effective_steps()] == ["archive.add", "archive.add"]


def test_case_spec_effective_steps_auto_expands_blocks() -> None:
    """老用例（只有 blocks）自动展开成 steps：step id = 块 id，on_fail 不显式声明。"""
    case = CaseSpec(**_case(blocks=["a.first", "b.second", "a.first"]))
    steps = case.effective_steps()
    assert [step.block for step in steps] == ["a.first", "b.second", "a.first"]
    assert [step.id for step in steps] == ["a.first", "b.second", "a.first#2"]
    assert all(step.declares_on_fail is False for step in steps)
    assert case.uses_plan is False
