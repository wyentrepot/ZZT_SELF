"""P1-3 规划器测试（REQS v3 §10.2 事实层铁律 / PLAN P1-3）。

铁律：事实 ``no`` 或 ``unknown`` → 块 ``runnable=False``；``unknown`` 绝不折算成 ``yes``。
同 run 内同一事实只探测一次（缓存）。
"""

from __future__ import annotations

import pytest

from case_runner.core.models import BlockSpec, CaseSpec, CaseStep
from case_runner.core.planner import (
    FACT_NO,
    FACT_UNKNOWN,
    FACT_YES,
    FactValue,
    FactsContext,
    coerce_fact_value,
    plan,
    resolve,
)


def make_case(blocks, needs=None) -> CaseSpec:
    return CaseSpec(
        id="c1",
        title="t",
        steps=[CaseStep(no=1, do="do", expect="expect")],
        blocks=list(blocks),
        needs=list(needs or []),
    )


class FakeFacts:
    """可计数的假探测源：{fact_id: 值或异常}。"""

    def __init__(self, table: dict[str, object]) -> None:
        self.table = dict(table)
        self.calls: list[str] = []

    def __call__(self, fact_id: str, params: dict) -> object:
        self.calls.append(fact_id)
        value = self.table[fact_id]
        if isinstance(value, Exception):
            raise value
        return value


# --- FactValue ---------------------------------------------------------------


def test_fact_value_states_and_serialization() -> None:
    yes = FactValue(state=FACT_YES, detail="在网 3", evidence=["ev-1"])
    assert yes.satisfied is True
    assert FactValue(state=FACT_NO).satisfied is False
    assert FactValue(state=FACT_UNKNOWN).satisfied is False
    assert yes.to_dict() == {"state": "yes", "detail": "在网 3", "evidence": ["ev-1"]}
    with pytest.raises(ValueError):
        FactValue(state="maybe")


# --- coerce: 三态不得被抬高 ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (FactValue(state=FACT_YES), FACT_YES),
        ("yes", FACT_YES),
        ("no", FACT_NO),
        ("unknown", FACT_UNKNOWN),
        (True, FACT_YES),
        (False, FACT_NO),
        (None, FACT_UNKNOWN),
        ({"state": "no", "detail": "档案无此地址"}, FACT_NO),
        ({"state": "unknown"}, FACT_UNKNOWN),
        ("随便一个字符串", FACT_UNKNOWN),
        (0, FACT_UNKNOWN),
        (1, FACT_UNKNOWN),
    ],
)
def test_coerce_fact_value_is_conservative(raw, expected) -> None:
    assert coerce_fact_value(raw).state == expected


def test_coerce_rejects_unknown_state() -> None:
    assert coerce_fact_value({"state": "pass"}).state == FACT_UNKNOWN


# --- resolve -----------------------------------------------------------------


def test_resolve_returns_dict_of_fact_values() -> None:
    probe = FakeFacts({"a": True, "b": "no"})
    ctx = FactsContext(probe=probe)
    got = resolve(["a", "b"], ctx)
    assert set(got) == {"a", "b"}
    assert got["a"].state == FACT_YES
    assert got["b"].state == FACT_NO


def test_resolve_caches_within_run() -> None:
    probe = FakeFacts({"a": True})
    ctx = FactsContext(probe=probe)
    resolve(["a"], ctx)
    resolve(["a", "a"], ctx)
    plan(make_case(["b1"], needs=["a"]), ctx)
    assert probe.calls == ["a"], "同一事实同 run 内只探测一次"


def test_probe_exception_becomes_unknown() -> None:
    probe = FakeFacts({"a": RuntimeError("串口桥炸了")})
    value = resolve(["a"], FactsContext(probe=probe))["a"]
    assert value.state == FACT_UNKNOWN
    assert "串口桥炸了" in value.detail


def test_unregistered_fact_becomes_unknown() -> None:
    ctx = FactsContext(probe=FakeFacts({}))
    value = resolve(["never.registered"], ctx)["never.registered"]
    assert value.state == FACT_UNKNOWN
    assert "never.registered" in value.detail


def test_facts_ctx_set_overrides_cache() -> None:
    probe = FakeFacts({"a": FACT_UNKNOWN})
    ctx = FactsContext(probe=probe)
    assert resolve(["a"], ctx)["a"].state == FACT_UNKNOWN
    ctx.set("a", FactValue(state=FACT_YES, detail="块提供了结论"))
    assert resolve(["a"], ctx)["a"].state == FACT_YES
    assert probe.calls == ["a"]


def test_facts_ctx_refresh_reprobes() -> None:
    probe = FakeFacts({"a": True})
    ctx = FactsContext(probe=probe)
    resolve(["a"], ctx)
    ctx.refresh()
    resolve(["a"], ctx)
    assert probe.calls == ["a", "a"]


# --- plan --------------------------------------------------------------------


def test_plan_all_yes_is_runnable() -> None:
    ctx = FactsContext(probe=FakeFacts({"a": True}))
    planned = plan(make_case(["b1", "b2"], needs=["a"]), ctx)
    assert [p.block_id for p in planned] == ["b1", "b2"]
    assert all(p.runnable for p in planned)
    assert all(p.skip_reason == "" for p in planned)


def test_plan_fact_no_blocks_block_and_downstream() -> None:
    ctx = FactsContext(probe=FakeFacts({"a": "no"}))
    planned = plan(make_case(["b1", "b2", "b3"], needs=["a"]), ctx)
    assert planned[0].runnable is False
    assert "a" in planned[0].skip_reason
    # 下游块一并 skipped，且原因指向断点块
    assert [p.runnable for p in planned] == [False, False, False]
    assert "b1" in planned[1].skip_reason
    assert planned[0].facts["a"].state == FACT_NO


def test_plan_fact_unknown_is_not_runnable() -> None:
    ctx = FactsContext(probe=FakeFacts({"a": FACT_UNKNOWN}))
    planned = plan(make_case(["b1"]), ctx)
    # 用例未声明 needs，但块若声明了 needs 也走同一逻辑；这里覆盖用例级
    assert planned[0].runnable is True  # 无事实依赖 → 可跑
    planned2 = plan(make_case(["b1"], needs=["a"]), ctx)
    assert planned2[0].runnable is False
    assert "a" in planned2[0].skip_reason
    assert planned2[0].facts["a"].state == FACT_UNKNOWN


def test_plan_block_level_needs() -> None:
    """块级 needs（block_specs 注入）同样驱动 runnable。"""
    from case_runner.core.models import BlockSpec

    specs = {
        "b1": BlockSpec(id="b1", title="t", action={"semantic": "x"}, needs=["a"]),
        "b2": BlockSpec(id="b2", title="t", action={"semantic": "x"}),
    }
    ctx = FactsContext(probe=FakeFacts({"a": FACT_UNKNOWN}))
    planned = plan(make_case(["b1", "b2"]), ctx, block_specs=specs)
    assert planned[0].runnable is False
    assert "a" in planned[0].skip_reason
    assert planned[1].runnable is False  # 链式断点
    assert "b1" in planned[1].skip_reason


def test_plan_unknown_never_marks_runnable() -> None:
    """铁律回归：unknown 与 no 一样，绝不 runnable。"""
    for raw in (FACT_UNKNOWN, "no", None, RuntimeError("boom")):
        ctx = FactsContext(probe=FakeFacts({"a": raw}))
        planned = plan(make_case(["b1"], needs=["a"]), ctx)
        assert planned[0].runnable is False
        assert planned[0].facts["a"].state != FACT_YES


# --- 参数化事实：缓存键必须带参数指纹（跨参数假 PASS 通道已封） ----------------
#
# ``archive.contains(addr)`` 是**带参事实**：按 addr 建档后，另一个 addr 的探测
# 若命中同一条缓存，就会把「A 已建档」当成「B 已建档」→ 跨参数假 PASS。


def _counting_probe(answer=FACT_UNKNOWN):
    calls: list[dict] = []

    def probe(fact_id: str, params: dict):
        calls.append(dict(params))
        return answer

    return probe, calls


def test_param_fact_reprobes_for_different_params() -> None:
    probe, calls = _counting_probe(FACT_NO)
    ctx = FactsContext(probe=probe)
    ctx.params["archive.contains"] = {"addr": "000000000001"}
    assert resolve(["archive.contains"], ctx)["archive.contains"].state == FACT_NO

    # 同参数：命中缓存，不重探
    assert resolve(["archive.contains"], ctx)["archive.contains"].state == FACT_NO
    assert ctx.probe_calls == 1

    # 换参数：必须重新探测（旧口径会直接命中缓存 → 假 PASS）
    ctx.params["archive.contains"] = {"addr": "000000000002"}
    assert resolve(["archive.contains"], ctx)["archive.contains"].state == FACT_NO
    assert ctx.probe_calls == 2, "参数不同就是另一条事实，不得复用缓存"
    assert calls == [{"addr": "000000000001"}, {"addr": "000000000002"}]


def test_param_fingerprint_ignores_dict_key_order() -> None:
    probe, calls = _counting_probe(FACT_NO)
    ctx = FactsContext(probe=probe)
    ctx.params["archive.contains"] = {"addr": "000000000001", "count": 1}
    resolve(["archive.contains"], ctx)
    ctx.params["archive.contains"] = {"count": 1, "addr": "000000000001"}
    resolve(["archive.contains"], ctx)
    assert ctx.probe_calls == 1, "同参数字典键序不同仍是同一条事实"
    assert len(calls) == 1


def test_provides_writeback_hits_same_params_only() -> None:
    probe, calls = _counting_probe(FACT_UNKNOWN)
    ctx = FactsContext(probe=probe)

    # 块 provides 回写：参数 B 已建档
    ctx.set("archive.contains", {"state": FACT_YES, "detail": "块已建档"},
            params={"addr": "000000000002"})

    # 同参数 B → 命中
    ctx.params["archive.contains"] = {"addr": "000000000002"}
    assert ctx.peek("archive.contains").state == FACT_YES
    assert resolve(["archive.contains"], ctx)["archive.contains"].state == FACT_YES
    assert ctx.probe_calls == 0

    # 异参数 A → 不命中，重新探测（不得被 B 的结论污染）
    ctx.params["archive.contains"] = {"addr": "000000000001"}
    assert ctx.peek("archive.contains") is None
    value = resolve(["archive.contains"], ctx)["archive.contains"]
    assert value.state == FACT_UNKNOWN
    assert ctx.probe_calls == 1
    assert calls == [{"addr": "000000000001"}]


def test_set_without_params_uses_declared_params_fingerprint() -> None:
    """``set(fact_id, value)`` 缺省按 ``ctx.params[fact_id]`` 的指纹写（同指纹语义）。"""
    probe, calls = _counting_probe(FACT_UNKNOWN)
    ctx = FactsContext(probe=probe)
    ctx.params["archive.contains"] = {"addr": "000000000001"}
    ctx.set("archive.contains", {"state": FACT_YES, "detail": "块已建档"})
    assert ctx.peek("archive.contains").state == FACT_YES

    ctx.params["archive.contains"] = {"addr": "000000000002"}
    assert ctx.peek("archive.contains") is None
    assert resolve(["archive.contains"], ctx)["archive.contains"].state == FACT_UNKNOWN
    assert calls == [{"addr": "000000000002"}]


def test_refresh_single_fact_clears_all_param_variants() -> None:
    probe, calls = _counting_probe(FACT_NO)
    ctx = FactsContext(probe=probe)
    ctx.params["archive.contains"] = {"addr": "A"}
    resolve(["archive.contains"], ctx)
    ctx.params["archive.contains"] = {"addr": "B"}
    resolve(["archive.contains"], ctx)
    assert ctx.probe_calls == 2

    ctx.refresh("archive.contains")
    assert ctx.peek("archive.contains") is None            # 参数 B 的条目已清
    ctx.params["archive.contains"] = {"addr": "A"}
    assert ctx.peek("archive.contains") is None            # 参数 A 的条目也已清
    resolve(["archive.contains"], ctx)
    assert ctx.probe_calls == 3


def test_param_fact_cache_never_leaks_yes_across_params_in_plan() -> None:
    """规划期铁律：A 参数已 yes，不得让 B 参数的块变得可执行。"""
    probe = FakeFacts({"archive.contains": FACT_YES})
    ctx = FactsContext(probe=probe)
    ctx.set("archive.contains", {"state": FACT_YES}, params={"addr": "A"})
    specs = {
        "b1": BlockSpec(id="b1", title="t", action={"semantic": "x"},
                        needs=["archive.contains"]),
    }
    ctx.params["archive.contains"] = {"addr": "B"}
    probe.table["archive.contains"] = FACT_UNKNOWN
    planned = plan(make_case(["b1"]), ctx, block_specs=specs)
    assert planned[0].runnable is False
    assert planned[0].facts["archive.contains"].state == FACT_UNKNOWN
