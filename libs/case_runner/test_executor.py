"""P1-4 执行器测试（REQS v3 §10.5 KE-3 / PLAN P1-4）。

铁律：
- 结论一律落 ``test_automation.models.AssertionResult``（outcome ∈ pass|fail|inconclusive）
- ``unknown`` 与 ``skipped`` 永不计入 PASS 计数（绝不假 PASS）
- ``stop_on_fail`` 时首块非 pass → 下游全 ``skipped``
"""

from __future__ import annotations

import pytest
from test_automation.models import AssertionResult

from case_runner.core.executor import CaseRunResult, RunContext, execute_case
from case_runner.core.models import BlockSpec, CaseSpec, CaseStep
from case_runner.core.planner import FACT_UNKNOWN, FACT_YES
from case_runner.core.registry import (
    clear_registries,
    register_block,
    register_fact,
)


@pytest.fixture(autouse=True)
def _isolate():
    clear_registries()
    yield
    clear_registries()


def spec(block_id: str, **over) -> BlockSpec:
    data = {"id": block_id, "title": f"块 {block_id}", "action": {"semantic": "fake"}}
    data.update(over)
    return BlockSpec(**data)


def make_case(blocks, needs=None, asserts=None, stop=None) -> CaseSpec:
    return CaseSpec(
        id="nwk_join",
        title="档案→组网",
        steps=[CaseStep(no=1, do="下发", expect="回帧")],
        needs=list(needs or []),
        blocks=list(blocks),
        asserts=list(asserts or []),
    )


def ctx(**over) -> RunContext:
    data = {"run_id": "run-42"}
    data.update(over)
    return RunContext(**data)


# --- A/B 基本结论 ------------------------------------------------------------


def test_single_pass_block_verdict_pass() -> None:
    register_block("b1", lambda params, c: {"state": "pass", "detail": "在网数 3"})
    result = execute_case(make_case(["b1"]), ctx())
    assert isinstance(result, CaseRunResult)
    assert result.verdict == "pass"
    assert result.failed_block == ""
    assert result.blocks[0].state == "pass"
    assert result.blocks[0].detail == "在网数 3"


def test_single_fail_block_verdict_fail_and_failed_block() -> None:
    register_block("b1", lambda params, c: {"state": "pass"})
    register_block("b2", lambda params, c: {"state": "fail", "detail": "00H-F1 未确认"})
    result = execute_case(make_case(["b1", "b2"]), ctx())
    assert result.verdict == "fail"
    assert result.failed_block == "b2"
    assert [b.state for b in result.blocks] == ["pass", "fail"]


# --- C stop_on_fail ----------------------------------------------------------


def test_stop_on_fail_skips_downstream() -> None:
    register_block("b1", lambda params, c: {"state": "fail", "detail": "首块挂"})
    register_block("b2", lambda params, c: {"state": "pass"})
    register_block("b3", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1", "b2", "b3"]), ctx(), stop_on_fail=True)
    assert [b.state for b in result.blocks] == ["fail", "skipped", "skipped"]
    assert result.failed_block == "b1"
    assert "b1" in result.blocks[1].detail
    assert result.counts["pass"] == 0


def test_without_stop_on_fail_rest_still_runs() -> None:
    register_block("b1", lambda params, c: {"state": "fail"})
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1", "b2"]), ctx(), stop_on_fail=False)
    assert [b.state for b in result.blocks] == ["fail", "pass"]
    assert result.counts["pass"] == 1
    assert result.verdict == "fail"


# --- D 绝不假 PASS -----------------------------------------------------------


def test_unknown_and_skipped_not_counted_as_pass() -> None:
    register_block("b1", lambda params, c: {"state": "pass"})
    register_block("b2", lambda params, c: {"state": "unknown", "detail": "台架不在线"})
    register_fact("bench.simeter_online", lambda params: FACT_UNKNOWN)
    result = execute_case(
        make_case(["b1", "b2"], needs=["bench.simeter_online"]), ctx()
    )
    # 用例前置事实 unknown → 全部 skipped，pass 计数必须为 0
    assert result.counts["pass"] == 0
    assert [b.state for b in result.blocks] == ["skipped", "skipped"]
    assert result.verdict == "inconclusive"
    assert result.passed_blocks() == []


def test_partial_unknown_verdict_is_inconclusive() -> None:
    register_block("b1", lambda params, c: {"state": "pass"})
    register_block("b2", lambda params, c: {"state": "unknown", "detail": "探测不到"})
    result = execute_case(make_case(["b1", "b2"]), ctx())
    assert [b.state for b in result.blocks] == ["pass", "unknown"]
    assert result.counts["pass"] == 1
    assert result.counts["unknown"] == 1
    assert result.verdict == "inconclusive"


def test_all_unknown_gives_inconclusive_not_pass() -> None:
    register_block("b1", lambda params, c: {"state": "unknown"})
    result = execute_case(make_case(["b1"]), ctx())
    assert result.verdict == "inconclusive"
    assert result.counts == {"pass": 0, "fail": 0, "skipped": 0, "unknown": 1, "total": 1}


# --- E AssertionResult 复用 ---------------------------------------------------


@pytest.mark.parametrize(
    ("block_return", "state", "outcome"),
    [
        ({"state": "pass"}, "pass", "pass"),
        ({"state": "fail"}, "fail", "fail"),
        ({"state": "unknown"}, "unknown", "inconclusive"),
        ({"state": "skipped", "detail": "依赖缺失"}, "skipped", "inconclusive"),
        (True, "pass", "pass"),
        (False, "fail", "fail"),
        (None, "unknown", "inconclusive"),
        ("pass", "pass", "pass"),
    ],
)
def test_block_result_maps_to_assertion_result(block_return, state, outcome) -> None:
    register_block("b1", lambda params, c: block_return)
    result = execute_case(make_case(["b1"]), ctx(run_id="run-99"))
    block = result.blocks[0]
    assert block.state == state
    assert isinstance(block.assertion, AssertionResult)
    assert block.assertion.outcome == outcome
    assert block.assertion.run_id == "run-99"
    assert block.assertion.assertion_id == "nwk_join.b1"
    assert result.assertions and result.assertions[0] is block.assertion


def test_explicit_assertion_result_is_adopted() -> None:
    given = AssertionResult(
        run_id="inner", assertion_id="custom.a", outcome="fail", message="字段不等"
    )
    register_block("b1", lambda params, c: given)
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].state == "fail"
    assert result.blocks[0].assertion is given


# --- 异常 / 未注册块 ----------------------------------------------------------


def test_block_exception_is_fail_not_pass() -> None:
    def boom(params, c):
        raise RuntimeError("串口桥未就绪")

    register_block("b1", boom)
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].state == "fail"
    assert "串口桥未就绪" in result.blocks[0].detail
    assert result.verdict == "fail"


def test_unregistered_block_is_unknown_not_pass() -> None:
    result = execute_case(make_case(["ghost.block"]), ctx())
    assert result.blocks[0].state == "unknown"
    assert "ghost.block" in result.blocks[0].detail
    assert result.counts["pass"] == 0
    assert result.verdict == "inconclusive"


# --- 事实：块 provides 满足下游 needs -----------------------------------------


def test_provided_fact_satisfies_downstream() -> None:
    specs = {
        "b1": spec("b1", provides=["sta.meter_addr_known"]),
        "b2": spec("b2", needs=["sta.meter_addr_known"]),
    }
    register_block(
        "b1",
        lambda params, c: {"state": "pass", "facts": {"sta.meter_addr_known": FACT_YES}},
    )
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1", "b2"]), ctx(block_specs=specs))
    assert [b.state for b in result.blocks] == ["pass", "pass"]
    assert result.verdict == "pass"


def test_deferred_fact_is_not_probed_before_provider_runs() -> None:
    calls: list[str] = []

    def probe(params):
        calls.append("probed")
        return FACT_UNKNOWN

    register_fact("sta.meter_addr_known", probe)
    specs = {
        "b1": spec("b1", provides=["sta.meter_addr_known"]),
        "b2": spec("b2", needs=["sta.meter_addr_known"]),
    }
    register_block(
        "b1",
        lambda params, c: {"state": "pass", "facts": {"sta.meter_addr_known": FACT_YES}},
    )
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1", "b2"]), ctx(block_specs=specs))
    assert calls == [], "被前序块 provides 的事实不得提前探测（会把 unknown 写进缓存）"
    assert result.verdict == "pass"


def test_provider_without_value_leaves_downstream_skipped() -> None:
    specs = {
        "b1": spec("b1", provides=["sta.meter_addr_known"]),
        "b2": spec("b2", needs=["sta.meter_addr_known"]),
    }
    register_block("b1", lambda params, c: {"state": "pass"})  # 未回传事实值
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1", "b2"]), ctx(block_specs=specs))
    assert [b.state for b in result.blocks] == ["pass", "skipped"]
    assert "sta.meter_addr_known" in result.blocks[1].detail
    assert result.verdict == "inconclusive"


def test_block_needs_fact_no_skips_and_reports_unmet() -> None:
    register_fact("archive.contains", lambda params: {"state": "no", "detail": "档案无此地址"})
    specs = {"b1": spec("b1", needs=["archive.contains"])}
    register_block("b1", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1"]), ctx(block_specs=specs))
    assert result.blocks[0].state == "skipped"
    assert "archive.contains" in result.blocks[0].detail
    assert result.blocked_block == "b1"
    assert result.unmet_facts["archive.contains"]["state"] == "no"
    assert result.verdict == "inconclusive"


# --- 参数 / ctx 传递 ---------------------------------------------------------


def test_block_receives_action_params_and_ctx() -> None:
    seen: dict = {}

    def block(params, c):
        seen["params"] = params
        seen["ctx"] = c
        return {"state": "pass"}

    register_block("b1", block)
    specs = {"b1": BlockSpec(id="b1", title="t", action={"afn": "10H", "fn": "F1", "params": {"n": 1}})}
    run_ctx = ctx(block_specs=specs)
    execute_case(make_case(["b1"]), run_ctx)
    assert seen["params"]["afn"] == "10H"
    assert seen["params"]["params"] == {"n": 1}
    assert seen["ctx"] is run_ctx


def test_runtime_params_override_action_params() -> None:
    seen: dict = {}

    def block(params, c):
        seen.update(params)
        return {"state": "pass"}

    register_block("meter.addr_probe", block)
    specs = {"meter.addr_probe": spec("meter.addr_probe", action={"semantic": "fake", "params": {"addr": ""}})}
    run_ctx = ctx(
        block_specs=specs,
        block_params={"meter.addr_probe": {"params": {"addr": "00000001"}}},
    )
    execute_case(make_case(["meter.addr_probe"]), run_ctx)
    assert seen["params"] == {"addr": "00000001"}
    assert seen["semantic"] == "fake"


def test_ctx_must_be_run_context_or_none() -> None:
    register_block("b1", lambda params, c: {"state": "pass"})
    assert execute_case(make_case(["b1"]), None).verdict == "pass"
    with pytest.raises(TypeError):
        execute_case(make_case(["b1"]), {"run_id": "r"})


# --- to_dict ----------------------------------------------------------------


def test_to_dict_contract() -> None:
    register_fact("bench.simcon_online", lambda params: FACT_YES)
    register_fact("archive.contains", lambda params: {"state": "no", "detail": "档案无此地址"})
    specs = {
        "b1": spec("b1", provides=["sta.meter_addr_known"]),
        "b2": spec("b2", needs=["archive.contains"]),
    }
    register_block(
        "b1",
        lambda params, c: {"state": "pass", "facts": {"sta.meter_addr_known": FACT_YES}},
    )
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(
        make_case(
            ["b1", "b2"],
            needs=["bench.simcon_online"],
            asserts=[{"kind": "onnet", "value": 1}],
        ),
        ctx(block_specs=specs),
    )
    data = result.to_dict()
    assert data["case_id"] == "nwk_join"
    assert data["run_id"] == "run-42"
    assert data["verdict"] == "inconclusive", "用例级断言未求值 → 不得 pass"
    assert data["failed_block"] == ""
    assert data["blocked_block"] == "b2"
    assert data["unmet_facts"]["archive.contains"] == {
        "state": "no",
        "detail": "档案无此地址",
        "evidence": [],
    }
    assert [b["block_id"] for b in data["blocks"]] == ["b1", "b2"]
    assert [b["state"] for b in data["blocks"]] == ["pass", "skipped"]
    assert set(data["blocks"][0]) >= {"block_id", "state", "detail", "facts", "outcome"}
    assert data["blocks"][0]["facts"] == {
        "bench.simcon_online": {"state": "yes", "detail": "", "evidence": []},
        "sta.meter_addr_known": {"state": "yes", "detail": "", "evidence": []},
    }
    assert data["blocks"][0]["outcome"] == "pass"
    assert data["blocks"][1]["outcome"] == "inconclusive"
    assert "archive.contains" in data["blocks"][1]["detail"]
    assert data["counts"] == {"pass": 1, "fail": 0, "skipped": 1, "unknown": 0, "total": 2}
    import json

    json.dumps(data, ensure_ascii=False)  # 必须可 JSON 序列化（落盘 RU-2）


def test_case_level_asserts_need_evaluator_and_never_fake_pass() -> None:
    register_block("b1", lambda params, c: {"state": "pass"})
    case = make_case(["b1"], asserts=[{"kind": "onnet_count", "op": ">=", "value": 1}])
    without = execute_case(case, ctx())
    assert without.verdict == "inconclusive", "用例声明的 asserts 未求值时不得判 pass"
    # 块断言在前、用例级断言在后
    assert without.assertions[0].outcome == "pass"
    assert without.assertions[-1].outcome == "inconclusive"
    assert "未提供" in without.assertions[-1].message or "未求值" in without.assertions[-1].message

    def evaluator(decl, c):
        return AssertionResult(
            run_id=c.run_id, assertion_id="case.assert.0", outcome="pass", message="在网数达标"
        )

    with_eval = execute_case(case, ctx(assert_evaluator=evaluator))
    assert with_eval.verdict == "pass"


# --- F 能力层的 {"ok": true} 不是判定结论（假 PASS 通道已封） ------------------
#
# 能力层所有动作（handle_hex / verify / status …）都带 {"ok": true}，它只表示
# 「服务把这次请求处理成功了」，不表示「设备测试通过」。块若写
# ``return cap.verify(...)``，绝不能被折算成 PASS（REQS RU-3 绝不假 PASS）。


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},
        {"ok": False},
        {"passed": True},
        {"passed": False},
        {"ok": True, "verified": True, "detail": "服务处理成功"},
        {"ok": True, "passed": True},
    ],
)
def test_capability_ok_and_passed_are_not_verdicts(payload) -> None:
    from case_runner.core.executor import coerce_block_return

    coerced = coerce_block_return(payload)
    assert coerced.state == "unknown", f"{payload} 只说明服务成功，不得折算成 pass/fail"
    assert coerced.state not in ("pass", "fail")


def test_capability_ok_true_block_is_unknown_not_pass() -> None:
    """块直接 `return cap.handle_hex(...)` 的典型写法：服务 ok，但测试没判据。"""
    register_block(
        "b1", lambda params, c: {"ok": True, "reply_hex": "6816", "action": None}
    )
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].state == "unknown"
    assert result.counts["pass"] == 0
    assert result.verdict == "inconclusive"
    assert result.passed_blocks() == []


def test_capability_ok_false_block_is_unknown_not_fail() -> None:
    """``ok: false`` 只说明服务/传输没成功，也不能替设备判 fail。"""
    register_block("b1", lambda params, c: {"ok": False, "error": "unreachable"})
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].state == "unknown"
    assert result.counts["fail"] == 0
    assert result.verdict == "inconclusive"


def test_passed_key_is_not_a_verdict() -> None:
    register_block("b1", lambda params, c: {"passed": True, "detail": "看起来过了"})
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].state == "unknown"
    assert result.counts["pass"] == 0


def test_explicit_state_and_outcome_still_authoritative() -> None:
    """保留通道：显式 state / outcome 仍是唯一判据，且不被 ok 干扰。"""
    from case_runner.core.executor import coerce_block_return

    assert coerce_block_return({"state": "pass"}).state == "pass"
    assert coerce_block_return({"state": "fail"}).state == "fail"
    assert coerce_block_return({"outcome": "pass"}).state == "pass"
    assert coerce_block_return({"outcome": "fail"}).state == "fail"
    assert coerce_block_return({"outcome": "inconclusive"}).state == "unknown"
    assert coerce_block_return({"ok": True, "state": "fail"}).state == "fail"
    assert coerce_block_return({"ok": False, "state": "pass"}).state == "pass"

    register_block("b1", lambda params, c: {"ok": False, "outcome": "pass"})
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].state == "pass"
    assert result.verdict == "pass"


# --- G 块级 assert / cleanup 必须传进执行体（声明与实现不许漂移） --------------


def test_block_receives_assert_and_cleanup_from_spec() -> None:
    seen: dict = {}

    def block(params, c):
        seen.update(params)
        return {"state": "pass"}

    register_block("reset.sta", block)
    asserts = [
        {"kind": "frame_field", "field": "地址域", "value": "000000000001"},
        {"kind": "onnet", "op": ">=", "value": 1},
    ]
    cleanups = [{"afn": "11H", "fn": 2, "params": {"action": "delete", "addr": "000000000001"}}]
    specs = {
        "reset.sta": BlockSpec(
            id="reset.sta", title="复位 STA", action={"semantic": "reboot"},
            assert_=asserts, cleanup=cleanups,
        )
    }
    execute_case(make_case(["reset.sta"]), ctx(block_specs=specs))

    assert seen["_assert"] == asserts, "JSON 里声明的 assert 必须原样交给执行体"
    assert seen["_cleanup"] == cleanups
    assert seen["_assert"] is not asserts and seen["_cleanup"] is not cleanups, (
        "交给执行体的应是副本：块改了也不能污染 BlockSpec"
    )
    assert seen["semantic"] == "reboot"


def test_block_params_cannot_override_reserved_assert_cleanup() -> None:
    seen: dict = {}

    def block(params, c):
        seen.update(params)
        return {"state": "pass"}

    register_block("b1", block)
    asserts = [{"kind": "frame_field", "field": "地址域", "value": "000000000001"}]
    cleanups = [{"afn": "11H", "fn": 2}]
    specs = {
        "b1": BlockSpec(
            id="b1", title="t", action={"semantic": "fake"},
            assert_=asserts, cleanup=cleanups,
        )
    }
    run_ctx = ctx(
        block_specs=specs,
        block_params={
            "b1": {
                "_assert": [{"hacked": True}],
                "_cleanup": [],
                "params": {"n": 2},
            }
        },
    )
    execute_case(make_case(["b1"]), run_ctx)

    assert seen["_assert"] == asserts, "保留键优先级最高，ctx.block_params 覆盖不掉"
    assert seen["_cleanup"] == cleanups
    assert seen["params"] == {"n": 2}, "普通键仍按覆盖语义生效"


def test_reserved_keys_present_even_without_declarations() -> None:
    seen: dict = {}
    register_block("b1", lambda params, c: (seen.update(params), {"state": "pass"})[1])
    execute_case(make_case(["b1"]), ctx())
    assert seen["_assert"] == [] and seen["_cleanup"] == []


# --- H provides 回写按参数指纹落缓存（带参事实不跨参数泄漏） --------------------


def test_provides_without_explicit_params_uses_declared_params() -> None:
    specs = {"b1": spec("b1", provides=["archive.contains"])}
    register_block(
        "b1",
        lambda params, c: {
            "state": "pass",
            "facts": {"archive.contains": {"state": FACT_YES, "detail": "块已建档"}},
        },
    )
    run = ctx(block_specs=specs)
    run.facts_ctx.params["archive.contains"] = {"addr": "A"}
    execute_case(make_case(["b1"]), run)

    assert run.facts_ctx.peek("archive.contains").state == FACT_YES   # 声明参数 A 命中
    run.facts_ctx.params["archive.contains"] = {"addr": "B"}
    assert run.facts_ctx.peek("archive.contains") is None             # 参数 B 不得命中


def test_provides_with_explicit_params_does_not_leak_to_other_params() -> None:
    """块明确说「这是 B 地址的结论」→ A 地址的缓存必须仍是未探测。"""
    specs = {"b1": spec("b1", provides=["archive.contains"])}
    register_block(
        "b1",
        lambda params, c: {
            "state": "pass",
            "facts": {
                "archive.contains": {
                    "state": FACT_YES,
                    "detail": "B 已建档",
                    "params": {"addr": "B"},
                }
            },
        },
    )
    run = ctx(block_specs=specs)
    run.facts_ctx.params["archive.contains"] = {"addr": "A"}
    execute_case(make_case(["b1"]), run)

    run.facts_ctx.params["archive.contains"] = {"addr": "B"}
    assert run.facts_ctx.peek("archive.contains").state == FACT_YES
    run.facts_ctx.params["archive.contains"] = {"addr": "A"}
    assert run.facts_ctx.peek("archive.contains") is None, "A 的结论不得由 B 的建档推出"


# --- 步骤实例化（REQS-0042 D3-1）：同块多次 / 各自参数 / 每步失败处理 --------


def plan_case(plan, blocks=None, needs=None) -> CaseSpec:
    """带 ``plan`` 的用例（``blocks`` 可选，两者都给以 plan 为准）。"""
    return CaseSpec(
        id="archive_multi",
        title="多档下发",
        steps=[CaseStep(no=1, do="下发", expect="回帧")],
        blocks=list(blocks or []),
        plan=list(plan),
        needs=list(needs or []),
    )


def _recorder_block(seen: list[dict], state: str | dict = "pass"):
    """假块：把每次收到的 params 记下来，按需返回结论。"""

    def executor(params, c):
        seen.append(dict(params))
        if isinstance(state, list):
            return state[len(seen) - 1]
        return {"state": state}

    return executor


def test_plan_steps_run_in_declared_order() -> None:
    seen: list[str] = []

    def block(name: str):
        def executor(params, c):
            seen.append(name)
            return {"state": "pass"}

        return executor

    register_block("a", block("a"))
    register_block("b", block("b"))
    result = execute_case(plan_case([{"id": "s2", "block": "b"}, {"id": "s1", "block": "a"}]), ctx())
    assert seen == ["b", "a"]
    assert [record.step_id for record in result.blocks] == ["s2", "s1"]
    assert [record.block_id for record in result.blocks] == ["b", "a"]
    assert result.verdict == "pass"


def test_step_params_do_not_leak_between_same_block_steps() -> None:
    """同块两次实例化，各带不同 meter_addr：两次下发参数必须不同（核心诉求）。"""
    seen: list[dict] = []
    register_block("archive.add", _recorder_block(seen))
    specs = {
        "archive.add": BlockSpec(
            id="archive.add",
            title="下发档案",
            action={"semantic": "fake", "params": {"meter_addr": "默认地址"}},
        )
    }
    result = execute_case(
        plan_case(
            [
                {"id": "add_a", "block": "archive.add", "params": {"meter_addr": "000000000011"}},
                {"id": "add_b", "block": "archive.add", "params": {"meter_addr": "000000000022"}},
            ]
        ),
        ctx(block_specs=specs),
    )
    assert [call.get("meter_addr") for call in seen] == ["000000000011", "000000000022"]
    # 结果里能看到每步解析后的实际参数（D4-1 参数冻结的数据基础）
    assert [record.params.get("meter_addr") for record in result.blocks] == [
        "000000000011",
        "000000000022",
    ]
    assert result.verdict == "pass"


def test_run_level_block_params_beat_step_params() -> None:
    """D3-2 优先级：run 级 ``--params``（ctx.block_params）> step.params > 块声明。"""
    seen: list[dict] = []
    register_block("archive.add", _recorder_block(seen))
    specs = {
        "archive.add": BlockSpec(
            id="archive.add", title="t", action={"semantic": "fake", "params": {"meter_addr": "声明"}}
        )
    }
    execute_case(
        plan_case(
            [{"id": "add_a", "block": "archive.add", "params": {"meter_addr": "步骤"}}]
        ),
        ctx(
            block_specs=specs,
            block_params={"archive.add": {"meter_addr": "运行期"}},
        ),
    )
    assert seen[0]["meter_addr"] == "运行期"


def test_step_params_beat_declared_action_params() -> None:
    seen: list[dict] = []
    register_block("archive.add", _recorder_block(seen))
    specs = {
        "archive.add": BlockSpec(
            id="archive.add", title="t", action={"semantic": "fake", "params": {"meter_addr": "声明"}}
        )
    }
    execute_case(
        plan_case(
            [{"id": "add_a", "block": "archive.add", "params": {"meter_addr": "步骤"}}]
        ),
        ctx(block_specs=specs),
    )
    assert seen[0]["meter_addr"] == "步骤"


def test_ctx_step_params_override_step_spec_params() -> None:
    """``RunContext.step_params`` 按 step_id 生效（同块多次互不覆盖的关键）。"""
    seen: list[dict] = []
    register_block("archive.add", _recorder_block(seen))
    execute_case(
        plan_case(
            [
                {"id": "add_a", "block": "archive.add", "params": {"meter_addr": "A"}},
                {"id": "add_b", "block": "archive.add", "params": {"meter_addr": "B"}},
            ]
        ),
        ctx(step_params={"add_b": {"meter_addr": "B2"}}),
    )
    assert [call.get("meter_addr") for call in seen] == ["A", "B2"]


def test_on_fail_continue_runs_downstream() -> None:
    register_block("b1", lambda params, c: {"state": "fail", "detail": "首步挂"})
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(
        plan_case(
            [
                {"id": "s1", "block": "b1", "on_fail": "continue"},
                {"id": "s2", "block": "b2"},
            ]
        ),
        ctx(),
    )
    assert [record.state for record in result.blocks] == ["fail", "pass"]
    assert result.counts["pass"] == 1
    assert result.verdict == "fail"


def test_on_fail_stop_skips_downstream_and_names_step() -> None:
    register_block("b1", lambda params, c: {"state": "fail", "detail": "首步挂"})
    register_block("b2", lambda params, c: {"state": "pass"})
    result = execute_case(
        plan_case(
            [
                {"id": "s1", "block": "b1", "on_fail": "stop"},
                {"id": "s2", "block": "b2"},
            ]
        ),
        ctx(),
    )
    assert [record.state for record in result.blocks] == ["fail", "skipped"]
    assert "s1" in result.blocks[1].detail or "b1" in result.blocks[1].detail
    assert result.blocks[1].step_id == "s2"


def test_legacy_blocks_case_inherits_run_level_stop_on_fail() -> None:
    """老用例（无 plan）行为不变：stop_on_fail=False 时后续照跑。"""
    register_block("b1", lambda params, c: {"state": "fail"})
    register_block("b2", lambda params, c: {"state": "pass"})
    no_stop = execute_case(make_case(["b1", "b2"]), ctx())
    stopped = execute_case(make_case(["b1", "b2"]), ctx(), stop_on_fail=True)
    assert [b.state for b in no_stop.blocks] == ["fail", "pass"]
    assert [b.state for b in stopped.blocks] == ["fail", "skipped"]


def test_plan_step_without_on_fail_inherits_run_level_default() -> None:
    """plan 里没写 on_fail → 继承 run/方案级 stop_on_fail（而不是硬按 stop）。"""
    register_block("b1", lambda params, c: {"state": "fail"})
    register_block("b2", lambda params, c: {"state": "pass"})
    case = plan_case([{"id": "s1", "block": "b1"}, {"id": "s2", "block": "b2"}])
    assert [b.state for b in execute_case(case, ctx()).blocks] == ["fail", "pass"]
    assert [b.state for b in execute_case(case, ctx(), stop_on_fail=True).blocks] == [
        "fail",
        "skipped",
    ]


def test_block_record_and_result_expose_step_id() -> None:
    register_block("b1", lambda params, c: {"state": "pass"})
    case = plan_case([{"id": "only", "block": "b1"}])
    data = execute_case(case, ctx()).to_dict()
    assert data["blocks"][0]["step_id"] == "only"
    # 未显式声明 on_fail → 记录的是**继承后**的生效策略（run 级 stop_on_fail=False）
    assert data["steps"] == [
        {"step_id": "only", "block_id": "b1", "state": "pass", "outcome": "pass",
         "on_fail": "continue"}
    ]
    assert execute_case(case, ctx()).blocks[0].assertion.assertion_id == "archive_multi.only"
    # 显式声明时记录声明值
    declared = execute_case(
        plan_case([{"id": "only", "block": "b1", "on_fail": "stop"}]), ctx()
    )
    assert declared.to_dict()["steps"][0]["on_fail"] == "stop"


def test_legacy_assertion_id_and_step_id_unchanged() -> None:
    """老用例的断言 id 仍为 ``用例.块``，step_id 回填成块 id（逐字兼容）。"""
    register_block("b1", lambda params, c: {"state": "pass"})
    result = execute_case(make_case(["b1"]), ctx())
    assert result.blocks[0].step_id == "b1"
    assert result.blocks[0].assertion.assertion_id == "nwk_join.b1"
    assert result.to_dict()["steps"][0]["step_id"] == "b1"


def test_failed_step_and_blocked_step_point_at_the_right_instance() -> None:
    """同块两步：第 1 步 pass、第 2 步 fail → 断点必须指向**第 2 步**（不能只给块 id）。"""
    register_block(
        "archive.add",
        lambda params, c: {"state": "pass"}
        if params.get("meter_addr") == "A"
        else {"state": "fail", "detail": "第二块表无应答"},
    )
    result = execute_case(
        plan_case(
            [
                {"id": "add_a", "block": "archive.add", "params": {"meter_addr": "A"}},
                {"id": "add_b", "block": "archive.add", "params": {"meter_addr": "B"}},
            ]
        ),
        ctx(),
    )
    data = result.to_dict()
    assert data["failed_block"] == "archive.add"
    assert data["failed_step"] == "add_b"
    assert data["blocked_step"] == ""


def test_blocked_step_when_gated_fact_missing() -> None:
    register_fact("bench.simcon_online", lambda params: "no")
    specs = {
        "archive.add": BlockSpec(
            id="archive.add",
            title="t",
            action={"semantic": "x"},
            needs=["bench.simcon_online"],
        )
    }
    result = execute_case(
        plan_case([{"id": "add_a", "block": "archive.add"}]), ctx(block_specs=specs)
    )
    data = result.to_dict()
    assert data["blocked_block"] == "archive.add"
    assert data["blocked_step"] == "add_a"
