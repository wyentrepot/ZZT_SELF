"""观测 / 期望求值器测试（REQS-0044 §4.2「判据归属」/ TODO P1）。

本文件是**新增**的守门（不改任何既有测试），固定三件事：

1. **观测模型**（``core.expect.Observation``）：只表达"看到了什么 / 是否取全 /
   没拿到时的原因"，**不含**任何"通过 / 失败"字样（AC-1）。
2. **求值器铁律**（``core.expect.evaluate``）：
   - 期望成立 → ``pass``；期望不成立 → ``fail``；
   - **观测不完整 → ``unknown``（不是 pass）**；
   - **没拿到观测 → ``skipped``（台架原因）/ ``unknown``（其余原因），绝不是 pass**。
3. **双轨兼容**（``executor._step_verdict``）：同一条用例分别走旧路径（块自判）与新路径
   （块只报观测 + 步骤声明 expect），四态结论、断点字段、计数**完全一致**。

为什么"观测不完整"必须是 unknown 而不是 fail / pass：``docs/blocks/case-流程-单一档案抄表.md``
§①b 原话"查不全 → 不完整，**不得**折算成'含'或'不含'"；组网翻页只取到一页就按部分数
判达标，正是 0044 §6.3 R-1 的假 PASS 形态（"差一个"判成达标）。
"""

from __future__ import annotations

import pytest
from test_automation.models import ASSERTION_OUTCOMES

from case_runner.core.executor import (
    BLOCK_TO_OUTCOME,
    BlockResult,
    CaseRunResult,
    RunContext,
    coerce_block_return,
    execute_case,
)
from case_runner.core.expect import (
    ENVIRONMENT_REASON_CODES,
    EXPECT_OPS,
    OBSERVATION_FIELDS,
    OBS_BENCH_UNREACHABLE,
    OBS_GATE_BUSY,
    OBS_INCOMPLETE,
    OBS_INVALID_PARAM,
    OBS_REASON_LABELS,
    OBS_SERIAL_BUSY,
    OBS_TIMEOUT,
    Observation,
    coerce_observation,
    describe_expects,
    evaluate,
    normalize_expects,
    step_state,
)
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    OUTCOME_BLOCKED,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    BlockSpec,
    CaseSpec,
    CaseStep,
    StepSpec,
)
from case_runner.core.registry import clear_registries, register_block


@pytest.fixture(autouse=True)
def _isolate():
    """每个用例一份干净的注册表（与 test_executor.py 同一套隔离口径）。"""

    clear_registries()
    yield
    clear_registries()


def spec(block_id: str = "b1", **over) -> BlockSpec:
    data = {"id": block_id, "title": f"块 {block_id}", "action": {"semantic": "fake"}}
    data.update(over)
    return BlockSpec(**data)


def make_case(step: StepSpec, case_id: str = "c1") -> CaseSpec:
    return CaseSpec(
        id=case_id,
        title="观测/期望双轨",
        steps=[CaseStep(no=1, do="做一次交互", expect="看观测")],
        plan=[step],
    )


def run_one(step: StepSpec, *, block_specs=None) -> CaseRunResult:
    """跑一条一步用例（块执行体由调用方先用 ``register_block`` 装好）。"""

    ctx = RunContext(run_id="run-1", block_specs=dict(block_specs or {}))
    return execute_case(make_case(step), ctx)


# --------------------------------------------------------------------------- #
# 1. 观测模型：三件套齐全，且**不含**结论字样
# --------------------------------------------------------------------------- #


def test_observation_schema_is_three_pieces_only() -> None:
    """观测四件套固定：values / complete / reason_code / detail（改它等于改契约）。"""

    assert OBSERVATION_FIELDS == ("values", "complete", "reason_code", "detail")
    obs = Observation(
        values={"节点总数量": 12, "在网从节点数": 11, "门槛": 12},
        complete=True,
        detail="翻了 1 页",
    )
    assert obs.got is True
    assert obs.reason_code == ""
    assert obs.to_dict() == {
        "values": {"节点总数量": 12, "在网从节点数": 11, "门槛": 12},
        "complete": True,
        "reason_code": "",
        "detail": "翻了 1 页",
    }


def test_observation_contains_no_verdict_wording() -> None:
    """AC-1：观测模型里不许出现"通过 / 失败 / pass / fail"（块只观测，不判定）。"""

    haystack = [*OBSERVATION_FIELDS, *OBS_REASON_LABELS.keys(), *OBS_REASON_LABELS.values()]
    lowered = " ".join(haystack).lower()
    for forbidden in ("通过", "失败", "pass", "fail"):
        assert forbidden not in lowered, f"观测模型里出现了结论字样 {forbidden!r}：{haystack}"

    # 观测对象本身也不带 state / outcome 这类结论字段
    dumped = set(Observation().to_dict())
    assert dumped == set(OBSERVATION_FIELDS)
    assert not ({"state", "outcome", "verdict"} & dumped)


def test_observation_complete_defaults_false_and_bool_is_parsed_strictly() -> None:
    """``complete`` 缺省 False（未声明 = 不完整）；字符串 "false" 绝不能被读成 True。"""

    assert Observation().complete is False
    assert coerce_observation({"values": {"a": 1}}) is not None
    assert coerce_observation({"values": {"a": 1}}).complete is False
    assert coerce_observation({"values": {"a": 1}, "complete": "false"}).complete is False
    assert coerce_observation({"values": {"a": 1}, "complete": "true"}).complete is True
    assert coerce_observation({"values": {"a": 1}, "complete": True}).complete is True


def test_coerce_observation_tolerates_block_shapes() -> None:
    """块怎么写都能收下：``values`` / 单项 ``value`` / ``reason`` 别名 / 非 mapping 降级。"""

    assert coerce_observation(None) is None

    single = coerce_observation({"value": 1234.56, "complete": True})
    assert single.values == {"value": 1234.56}

    alias = coerce_observation({"reason": "serial_busy"})
    assert alias.reason_code == OBS_SERIAL_BUSY, "reason 本身就是原因码时按码收"

    human = coerce_observation({"reason": "串口桥未运行"})
    assert human.reason_code == ""
    assert "串口桥未运行" in human.detail

    weird = coerce_observation(42)
    assert weird.complete is False, "形状不规范 = 不完整（绝不抬高成可判定观测）"
    assert weird.values == {"value": 42}


def test_observation_reason_labels_cover_all_codes() -> None:
    """原因码与中文标签一一对应（报告直接用这一份口径）。"""

    assert set(OBS_REASON_LABELS) == {
        OBS_SERIAL_BUSY,
        OBS_BENCH_UNREACHABLE,
        OBS_GATE_BUSY,
        OBS_TIMEOUT,
        OBS_INVALID_PARAM,
        OBS_INCOMPLETE,
    }
    assert ENVIRONMENT_REASON_CODES == {OBS_SERIAL_BUSY, OBS_BENCH_UNREACHABLE, OBS_GATE_BUSY}
    assert Observation(reason_code=OBS_TIMEOUT).reason_label == "等待超时（全程无应答）"


# --------------------------------------------------------------------------- #
# 2. 期望模型：声明式、词表固定
# --------------------------------------------------------------------------- #


def test_expect_vocabulary_is_frozen() -> None:
    """op 词表是契约（增删 op 必须同步改这里与 ``core/expect.py`` 模块头）。"""

    assert EXPECT_OPS == {
        "complete",
        "got",
        "not_got",
        "eq",
        "ne",
        "ge",
        "gt",
        "le",
        "lt",
        "eq_field",
        "ne_field",
        "ge_field",
        "gt_field",
        "le_field",
        "lt_field",
        "contains",
        "not_contains",
        "has_value",
        "present",
        "absent",
    }


def test_expect_must_be_declarative_not_code() -> None:
    """期望是声明式清单：代码字符串在加载期就被 pydantic 挡掉；坏 op 也在加载期报错。"""

    assert normalize_expects({"op": "complete"}) == [{"op": "complete"}]
    assert normalize_expects([{"op": "complete"}, "x > 1"]) == [{"op": "complete"}]
    assert normalize_expects("observation['x'] > 1") == [], "代码字符串一律不认"
    assert normalize_expects(None) == []

    with pytest.raises(ValueError):
        StepSpec(id="s1", block="b1", expect="x > 1")
    with pytest.raises(ValueError, match="不在词表里"):
        StepSpec(id="s1", block="b1", expect=[{"op": "约等于"}])
    with pytest.raises(ValueError, match="必须给 field"):
        StepSpec(id="s1", block="b1", expect=[{"op": "eq", "value": 1}])


def test_step_declares_expect_is_dual_track_switch() -> None:
    """``declares_expect`` 是双轨开关的一半（另一半是"块有没有给观测"）。"""

    assert StepSpec(id="s1", block="b1").declares_expect is False
    assert StepSpec(id="s1", block="b1", expect=[]).declares_expect is False
    assert StepSpec(id="s1", block="b1", expect={}).declares_expect is False
    assert StepSpec(id="s1", block="b1", expect=[{"op": "complete"}]).declares_expect is True
    assert StepSpec(id="s1", block="b1", expect={"op": "got"}).declares_expect is True


def test_describe_expects_is_human_readable() -> None:
    """期望的人话（报告 / CLI 复用，不另写一套）。"""

    text = describe_expects(
        [
            {"op": "complete"},
            {"op": "ge_field", "field": "在网从节点数", "value": "门槛"},
            {"op": "eq", "field": "DI回显", "value": "00010000"},
            {"op": "has_value", "field": "数值"},
        ]
    )
    assert "观测完整" in text
    assert "在网从节点数 ≥ 门槛（观测字段）" in text
    assert "DI回显 = 00010000" in text
    assert "数值 有值且可解" in text


# --------------------------------------------------------------------------- #
# 3. 求值器：四种情形（验收要求逐条覆盖）
# --------------------------------------------------------------------------- #

#: 抄表块（``meter.read_645``）的典型期望：收到帧 + CS 合法 + DI 回显 + 有值且可解
METER_EXPECTS = [
    {"op": "complete"},
    {"op": "eq", "field": "收到上行帧", "value": True},
    {"op": "eq", "field": "CS合法", "value": True},
    {"op": "eq", "field": "DI回显", "value": "00010000"},
    {"op": "has_value", "field": "数值"},
]


def _meter_observation(**over):
    values = {
        "收到上行帧": True,
        "CS合法": True,
        "DI回显": "00010000",
        "数值": 1234.0,
        "单位": "kWh",
    }
    values.update(over)
    return Observation(values=values, complete=True)


def test_expected_holds_gives_pass() -> None:
    """情形一：期望成立 → ``pass``（断言层也随之 pass）。"""

    evaluation = evaluate(_meter_observation(), METER_EXPECTS)
    assert evaluation.state == BLOCK_PASS
    assert evaluation.detail.startswith("期望成立：")
    assert evaluation.unmet == []
    assert evaluation.complete is True

    state, detail = step_state(_meter_observation(), METER_EXPECTS)
    assert (state, detail) == (evaluation.state, evaluation.detail)
    assert evaluate(_meter_observation(), METER_EXPECTS) == evaluation


def test_expected_violated_gives_fail() -> None:
    """情形二：期望不成立 → ``fail``（拿到确切反证，报文点明期望 vs 实际）。"""

    evaluation = evaluate(_meter_observation(DI回显="02010100"), METER_EXPECTS)
    assert evaluation.state == BLOCK_FAIL
    assert "DI回显" in evaluation.detail
    assert "00010000" in evaluation.detail and "02010100" in evaluation.detail
    assert evaluation.unmet, "不成立时要能逐条点名，报告才有话说"


def test_incomplete_observation_is_unknown_never_pass() -> None:
    """情形三：**观测不完整 → unknown**（不是 pass，也不是 fail）。

    已取到的字段恰好全都符合期望也不行：取不全就说不清（flow 文档 §①b）。
    """

    partial = _meter_observation()
    partial.complete = False
    evaluation = evaluate(partial, METER_EXPECTS)
    assert evaluation.state == BLOCK_UNKNOWN
    assert evaluation.state != BLOCK_PASS
    assert "观测不完整" in evaluation.detail and "不判 pass" in evaluation.detail

    # 组网"只取到一页"的形态：读数看似达标，但没取全 → 仍不许 pass
    half_page = Observation(
        values={"在网从节点数": 32, "门槛": 32, "只取到": 32, "总数": 40},
        complete=False,
        detail="只取到 32 / 总数 40",
    )
    expect = [
        {"op": "complete"},
        {"op": "ge_field", "field": "在网从节点数", "value": "门槛"},
    ]
    assert evaluate(half_page, expect).state == BLOCK_UNKNOWN


def test_no_observation_is_skipped_or_unknown_never_pass() -> None:
    """情形四：没拿到观测 → 台架原因 ``skipped``、其余 ``unknown``；**绝不 pass**。"""

    expect = [{"op": "complete"}, {"op": "has_value", "field": "数值"}]

    # 台架不具备条件（串口被占 / 台架不可达 / 闸门忙）→ skipped（聚合处 blocked）
    for code in (OBS_SERIAL_BUSY, OBS_BENCH_UNREACHABLE, OBS_GATE_BUSY):
        evaluation = evaluate(Observation(reason_code=code), expect)
        assert evaluation.state == BLOCK_SKIPPED, code
        assert BLOCK_TO_OUTCOME[evaluation.state] == OUTCOME_BLOCKED
        assert "台架不具备条件" in evaluation.detail
        assert "未拿到观测" in evaluation.detail

    # 说不清（等满无应答 / 参数非法 / 取不全）→ unknown（聚合处 inconclusive）
    for code in (OBS_TIMEOUT, OBS_INVALID_PARAM, OBS_INCOMPLETE):
        evaluation = evaluate(Observation(reason_code=code), expect)
        assert evaluation.state == BLOCK_UNKNOWN, code
        assert BLOCK_TO_OUTCOME[evaluation.state] == OUTCOME_INCONCLUSIVE

    # 块连"为什么没拿到"都没说 → unknown（无证据可比）
    bare = evaluate(None, expect)
    assert bare.state == BLOCK_UNKNOWN
    assert "没有返回观测" in bare.detail


def test_iron_law_no_pass_without_complete_observation() -> None:
    """铁律总表：只要观测"没拿到"或"不完整"，一切期望都不可能落 pass。"""

    expects = METER_EXPECTS
    variants = (
        [None, Observation()]
        + [Observation(reason_code=code) for code in OBS_REASON_LABELS]
        + [Observation(values={"收到上行帧": True}, complete=False)]
    )
    for observation in variants:
        evaluation = evaluate(observation, expects)
        assert evaluation.state != BLOCK_PASS, f"{observation!r} 竟然判 pass"


def test_negative_expectation_not_got() -> None:
    """负例："期望一帧都没有"（表未入网 / 地址错）—— 这才是拿到反证时该有的 fail 方向。"""

    negative = [{"op": "not_got", "reason": [OBS_TIMEOUT]}]

    assert evaluate(Observation(reason_code=OBS_TIMEOUT), negative).state == BLOCK_PASS
    # 拿到了观测 = 负例不成立（真失败）
    assert evaluate(_meter_observation(), negative).state == BLOCK_FAIL
    # 原因不符（例如串口被占，而不是等待超时）= 这条负例没验成 → 说不清
    assert evaluate(Observation(reason_code=OBS_SERIAL_BUSY), negative).state == BLOCK_UNKNOWN
    # 只声明"没拿到"、不限定原因 → 任何没拿到都算成立
    assert evaluate(Observation(reason_code=OBS_SERIAL_BUSY), [{"op": "not_got"}]).state == (
        BLOCK_PASS
    )


def test_no_expects_means_no_judgement() -> None:
    """没有判据不判决：``expect`` 为空 / 写坏 → unknown（绝不替调用方宣布通过）。"""

    for empty in ([], {}, None, "code here", [{}, {"no_op": True}]):
        evaluation = evaluate(_meter_observation(), empty)
        assert evaluation.state == BLOCK_UNKNOWN, empty
        assert evaluation.state != BLOCK_PASS
    # 完全没声明 → 明确写"不判决"；声明了但写坏（缺 op）→ "无法求值"
    assert "不判决" in evaluate(_meter_observation(), []).detail
    assert "无法求值" in evaluate(_meter_observation(), [{"no_op": True}]).detail


def test_unevaluable_item_is_unknown_but_real_violation_wins() -> None:
    """无法求值 → unknown；但已拿到确切反证时仍判 fail（fail 优先于 unknown）。"""

    # 观测里没有该字段 → 这条判据没验成
    unknown = evaluate(
        Observation(values={"CS合法": True}, complete=True),
        [{"op": "eq", "field": "DI回显", "value": "00010000"}],
    )
    assert unknown.state == BLOCK_UNKNOWN
    assert "没有字段" in unknown.detail

    # 未知 op → unknown（写错 op 不许被当成"没问题"）
    assert evaluate(_meter_observation(), [{"op": "similar"}]).state == BLOCK_UNKNOWN

    # 一条不成立 + 一条无法求值 → fail（拿到反证就不该被"另一条没求值"盖成说不清）
    mixed = evaluate(
        Observation(values={"DI回显": "02010100"}, complete=True),
        [
            {"op": "eq", "field": "DI回显", "value": "00010000"},
            {"op": "eq", "field": "不存在的字段", "value": 1},
        ],
    )
    assert mixed.state == BLOCK_FAIL


def test_complete_op_failure_is_unknown_not_fail() -> None:
    """期望"观测完整"而实际不完整 = 取数不全（说不清），不是产品失败。"""

    observation = Observation(values={"a": 1}, complete=False, reason_code=OBS_INCOMPLETE)
    evaluation = evaluate(observation, [{"op": "complete"}])
    assert evaluation.state == BLOCK_UNKNOWN


@pytest.mark.parametrize(
    ("actual_value", "item", "expected_state"),
    [
        # 值比较
        ({"DI回显": "00010000"}, {"op": "eq", "field": "DI回显", "value": "00010000"}, BLOCK_PASS),
        ({"DI回显": "02010100"}, {"op": "eq", "field": "DI回显", "value": "00010000"}, BLOCK_FAIL),
        ({"DI回显": "02010100"}, {"op": "ne", "field": "DI回显", "value": "00010000"}, BLOCK_PASS),
        # 数值比较（含"门槛由块回传"的口径）
        ({"在网": 12, "门槛": 12}, {"op": "ge_field", "field": "在网", "value": "门槛"}, BLOCK_PASS),
        ({"在网": 11, "门槛": 12}, {"op": "ge_field", "field": "在网", "value": "门槛"}, BLOCK_FAIL),
        ({"在网": 11, "门槛": 12}, {"op": "lt_field", "field": "在网", "value": "门槛"}, BLOCK_PASS),
        ({"在网": 3}, {"op": "ge", "field": "在网", "value": 3}, BLOCK_PASS),
        ({"在网": 2}, {"op": "gt", "field": "在网", "value": 3}, BLOCK_FAIL),
        ({"在网": 3}, {"op": "le", "field": "在网", "value": 3}, BLOCK_PASS),
        # 数值比较遇到非数量 → 无法求值
        ({"在网": "十二"}, {"op": "ge", "field": "在网", "value": 3}, BLOCK_UNKNOWN),
        # 有值且可解（§8.1-4：不校验数值真值）
        ({"数值": 1234.0}, {"op": "has_value", "field": "数值"}, BLOCK_PASS),
        ({"数值": None}, {"op": "has_value", "field": "数值"}, BLOCK_FAIL),
        ({"数值": "  "}, {"op": "has_value", "field": "数值"}, BLOCK_FAIL),
        ({"数值": []}, {"op": "has_value", "field": "数值"}, BLOCK_FAIL),
        # 容器包含 / 不包含（查档："含该地址" / "不含该地址"）
        ({"档案表": ["129078563412"]}, {"op": "contains", "field": "档案表", "value": "129078563412"}, BLOCK_PASS),
        ({"档案表": ["111111111111"]}, {"op": "contains", "field": "档案表", "value": "129078563412"}, BLOCK_FAIL),
        ({"档案表": ["111111111111"]}, {"op": "not_contains", "field": "档案表", "value": "129078563412"}, BLOCK_PASS),
        # 有没有该字段（区分"字段缺失"与"字段值为 null"）
        ({"数值": None}, {"op": "present", "field": "数值"}, BLOCK_PASS),
        ({}, {"op": "present", "field": "数值"}, BLOCK_FAIL),
        ({}, {"op": "absent", "field": "数值"}, BLOCK_PASS),
        ({"数值": 1}, {"op": "absent", "field": "数值"}, BLOCK_FAIL),
        # bool 与数字不互通（True == 1 在 Python 里成立，但语义不同）
        ({"CS合法": 1}, {"op": "eq", "field": "CS合法", "value": True}, BLOCK_FAIL),
    ],
)
def test_value_ops(actual_value, item, expected_state) -> None:
    """值级 op 的逐个口径（一张表看全，报告与文档共用）。"""

    observation = Observation(values=dict(actual_value), complete=True)
    assert evaluate(observation, [item]).state == expected_state


# --------------------------------------------------------------------------- #
# 4. 执行器：双轨兼容（同一条用例两条道，结论一致）
# --------------------------------------------------------------------------- #

#: 双轨对照表：``(场景名, 旧块返回, 新块返回的观测, 步骤期望, 期望四态)``
#:
#: 旧路径 = 块自己判（``{"state": ...}``，无观测）；新路径 = 块只报观测 + 步骤声明 expect。
#: 两条道的四态结论必须一致 —— 这是"迁移期两套口径并存"不翻车的唯一保证（§6.4）。
DUAL_TRACK_CASES = [
    (
        "抄到了一项可解的数",
        {"state": BLOCK_PASS, "detail": "DI 回显一致、数值可解"},
        {
            "observation": {
                "values": {"收到上行帧": True, "CS合法": True, "DI回显": "00010000", "数值": 1234.0},
                "complete": True,
            }
        },
        METER_EXPECTS,
        OUTCOME_PASS,
    ),
    (
        "回了帧但 DI 不符",
        {"state": BLOCK_FAIL, "detail": "DI 回显不符：期望 00010000"},
        {
            "observation": {
                "values": {"收到上行帧": True, "CS合法": True, "DI回显": "02010100", "数值": 220.1},
                "complete": True,
            }
        },
        METER_EXPECTS,
        OUTCOME_FAIL,
    ),
    (
        "串口被占（台架不具备条件）",
        {"state": BLOCK_SKIPPED, "detail": "串口被占：端口被其他会话占用"},
        {"observation": {"reason_code": OBS_SERIAL_BUSY, "detail": "端口被其他会话占用"}},
        METER_EXPECTS,
        OUTCOME_BLOCKED,
    ),
    (
        "等满预算一帧没有（说不清）",
        {"state": BLOCK_UNKNOWN, "detail": "等了 30 秒共 6 次尝试，始终没有上行帧"},
        {
            "observation": {
                "reason_code": OBS_TIMEOUT,
                "detail": "等了 30 秒共 6 次尝试，始终没有上行帧",
            }
        },
        METER_EXPECTS,
        OUTCOME_INCONCLUSIVE,
    ),
    (
        "组网只取到一页（观测不完整）",
        {"state": BLOCK_UNKNOWN, "detail": "只取到 32 / 总数 40，观测不完整"},
        {
            "observation": {
                "values": {"在网从节点数": 32, "门槛": 32, "只取到": 32, "总数": 40},
                "complete": False,
                "detail": "只取到 32 / 总数 40",
            }
        },
        [{"op": "complete"}, {"op": "ge_field", "field": "在网从节点数", "value": "门槛"}],
        OUTCOME_INCONCLUSIVE,
    ),
]


@pytest.mark.parametrize(
    ("scenario", "old_return", "new_return", "expects", "want_verdict"), DUAL_TRACK_CASES
)
def test_dual_track_same_case_same_conclusion(
    scenario, old_return, new_return, expects, want_verdict
) -> None:
    """同一条用例分别走旧路径与新路径：四态结论、断点字段、计数**完全一致**。

    旧路径 = 块自己判（``{"state": ...}``，无观测）；新路径 = 块只报观测 + 步骤声明
    expect。两条道跑的是**同一个用例骨架**（同 id / 同块 / 同一步），只有"谁说通过"不同。
    """

    results: dict[str, CaseRunResult] = {}
    for label, block_return, step_expect in (
        ("旧路径", old_return, []),
        ("新路径", new_return, expects),
    ):
        register_block("meter.read_645", lambda params, c, r=block_return: r)
        results[label] = run_one(
            StepSpec(id="s4", block="meter.read_645", expect=step_expect, on_fail="continue")
        )
        clear_registries()

    old, new = results["旧路径"], results["新路径"]
    assert old.verdict == want_verdict, f"{scenario}：旧路径结论不合预期"
    assert new.verdict == want_verdict, f"{scenario}：新路径结论不合预期"
    assert old.verdict == new.verdict, f"{scenario}：两条道结论不一致"
    assert [b.state for b in old.blocks] == [b.state for b in new.blocks], scenario
    assert old.counts == new.counts, f"{scenario}：分态计数不一致"
    assert old.failed_step == new.failed_step, f"{scenario}：断点（fail）不一致"
    assert old.failed_block == new.failed_block, scenario
    assert old.blocked_step == new.blocked_step, f"{scenario}：断点（blocked）不一致"
    assert old.blocked_block == new.blocked_block, scenario
    assert old.blocks[0].outcome == new.blocks[0].outcome, scenario

    # 断言层永远三态：``blocked`` 只活在 verdict 层（D2-4 红线）
    for result in (old, new):
        for assertion in result.assertions:
            assert assertion.outcome in ASSERTION_OUTCOMES, scenario
    # 两条道的**结论**一致，但新路径额外留下观测与期望（可复盘），旧路径没有
    assert old.blocks[0].observation is None, scenario
    assert new.blocks[0].observation is not None or new_return.get("observation") is None, scenario


def test_dual_track_old_path_detail_and_assertion_untouched() -> None:
    """旧路径逐字不变：detail / 断言 message / 观测字段都按块原样透出。"""

    register_block("b1", lambda params, c: {"state": BLOCK_PASS, "detail": "在网数 3"})
    result = run_one(StepSpec(id="b1", block="b1"))

    record = result.blocks[0]
    assert record.state == BLOCK_PASS
    assert record.detail == "在网数 3"
    assert record.assertion.message == "在网数 3"
    assert record.assertion.outcome == "pass"
    assert record.observation is None, "旧路径不产生观测"
    assert record.to_dict()["observation"] is None


def test_dual_track_reserved_keys_still_injected() -> None:
    """两个保留键 ``_assert`` / ``_cleanup`` 在两条道上都照旧注入（语义不碰）。"""

    seen: dict[str, dict] = {}

    def old_block(params, ctx):
        seen["old"] = dict(params)
        return {"state": BLOCK_PASS}

    def new_block(params, ctx):
        seen["new"] = dict(params)
        return {"observation": {"values": {"x": 1}, "complete": True}}

    block_specs = {
        "b1": spec("b1", **{"assert": [{"type": "inner_645_addr"}]}, cleanup=[{"action": "del"}])
    }

    register_block("b1", old_block)
    run_one(StepSpec(id="b1", block="b1"), block_specs=block_specs)
    clear_registries()

    register_block("b1", new_block)
    run_one(
        StepSpec(id="b1", block="b1", expect=[{"op": "eq", "field": "x", "value": 1}]),
        block_specs=block_specs,
    )

    assert seen["old"]["_assert"] == [{"type": "inner_645_addr"}]
    assert seen["old"]["_cleanup"] == [{"action": "del"}]
    assert seen["new"]["_assert"] == [{"type": "inner_645_addr"}]
    assert seen["new"]["_cleanup"] == [{"action": "del"}]


def test_new_path_ignores_block_state_judgement() -> None:
    """判据归调用方：块自己判 pass，但期望不成立 → 步骤照样 fail（§4.2 的核心）。"""

    register_block(
        "b1",
        lambda params, c: {
            "state": BLOCK_PASS,  # 旧口径的块内判定
            "detail": "块自己认为通过",
            "observation": {"values": {"在网从节点数": 11, "门槛": 12}, "complete": True},
        },
    )
    result = run_one(
        StepSpec(
            id="s2",
            block="b1",
            expect=[{"op": "ge_field", "field": "在网从节点数", "value": "门槛"}],
        )
    )
    assert result.verdict == OUTCOME_FAIL
    assert "在网从节点数" in result.blocks[0].detail
    assert result.blocks[0].assertion.outcome == "fail"
    # 断言里留下期望与观测，事后可复盘（expected/actual 都能落盘）
    assert result.blocks[0].assertion.expected == [
        {"op": "ge_field", "field": "在网从节点数", "value": "门槛"}
    ]
    assert result.blocks[0].assertion.actual == {"在网从节点数": 11, "门槛": 12}


def test_same_observation_serves_opposite_expectations() -> None:
    """AC-2：同一份观测，一条期望"含"、一条期望"不含"，两条都通过（块无需改）。"""

    observation = {
        "observation": {"values": {"档案表": ["129078563412"]}, "complete": True},
    }
    register_block("archive.query", lambda params, c: observation)

    for expect, want in (
        ([{"op": "contains", "field": "档案表", "value": "129078563412"}], OUTCOME_PASS),
        ([{"op": "not_contains", "field": "档案表", "value": "129078563412"}], OUTCOME_FAIL),
        ([{"op": "not_contains", "field": "档案表", "value": "000000000000"}], OUTCOME_PASS),
    ):
        # 同一条用例的两次运行，只是期望不同（清库用例 / 建档用例）
        result = run_one(StepSpec(id="s1", block="archive.query", expect=expect))
        assert result.verdict == want, expect


def test_observation_only_block_without_expect_stays_unknown() -> None:
    """双轨安全阀：块已按新口径只报观测，但步骤还没写期望 → unknown（不是 pass）。"""

    register_block("b1", lambda params, c: Observation(values={"x": 1}, complete=True))
    result = run_one(StepSpec(id="b1", block="b1"))
    assert result.verdict == OUTCOME_INCONCLUSIVE
    assert result.blocks[0].state == BLOCK_UNKNOWN
    assert "只返回观测" in result.blocks[0].detail
    assert result.blocks[0].observation is not None, "观测仍要留档，供报告复盘"


def test_old_block_with_expect_still_uses_old_path() -> None:
    """迁移期另一种组合：数据先补了 expect，但块还没迁移（不报观测）→ 旧路径照跑。

    这条保证 task-2（迁块）与 task-3（补期望）**谁先落地都不炸**。
    """

    register_block("b1", lambda params, c: {"state": BLOCK_PASS, "detail": "块内判据"})
    result = run_one(StepSpec(id="b1", block="b1", expect=[{"op": "complete"}]))
    assert result.verdict == OUTCOME_PASS
    assert result.blocks[0].detail == "块内判据"
    assert result.blocks[0].observation is None


def test_mixed_paths_aggregate_by_priority_unchanged() -> None:
    """一旧一新混在一条用例里：聚合仍按 ``fail > blocked > inconclusive > pass``。"""

    register_block("old.fail", lambda params, c: {"state": BLOCK_FAIL, "detail": "旧块判失败"})
    register_block(
        "new.pass",
        lambda params, c: {"observation": {"values": {"x": 1}, "complete": True}},
    )
    case = CaseSpec(
        id="c-mixed",
        title="混跑",
        steps=[CaseStep(no=1, do="旧块", expect="看"), CaseStep(no=2, do="新块", expect="看")],
        plan=[
            StepSpec(id="s1", block="old.fail", on_fail="continue"),
            StepSpec(
                id="s2",
                block="new.pass",
                on_fail="continue",
                expect=[{"op": "eq", "field": "x", "value": 1}],
            ),
        ],
    )
    result = execute_case(case, RunContext(run_id="run-mixed"))
    assert [b.state for b in result.blocks] == [BLOCK_FAIL, BLOCK_PASS]
    assert result.verdict == OUTCOME_FAIL
    assert result.failed_step == "s1"


def test_new_path_fail_triggers_stop_on_fail_downstream_skipped() -> None:
    """新路径的 fail 同样触发"失败即停"：下游记 skipped（聚合 blocked），断点在失败那步。"""

    register_block(
        "b.fail",
        lambda params, c: {"observation": {"values": {"x": 2}, "complete": True}},
    )
    register_block("b.next", lambda params, c: {"state": BLOCK_PASS})
    case = CaseSpec(
        id="c-stop",
        title="失败即停",
        steps=[CaseStep(no=1, do="第一步", expect="看"), CaseStep(no=2, do="第二步", expect="看")],
        plan=[
            StepSpec(id="s1", block="b.fail", expect=[{"op": "eq", "field": "x", "value": 1}]),
            StepSpec(id="s2", block="b.next"),
        ],
    )
    result = execute_case(case, RunContext(run_id="run-stop"), stop_on_fail=True)
    assert [b.state for b in result.blocks] == [BLOCK_FAIL, BLOCK_SKIPPED]
    assert result.failed_step == "s1"
    assert result.verdict == OUTCOME_FAIL
    assert "前序块" in result.blocks[1].detail


def test_fact_write_back_follows_effective_state() -> None:
    """``provides`` 的取值回写跟**有效步骤态**走（新路径 pass 才能回写，fail 不许）。"""

    block_specs = {"b1": spec("b1", provides=["archive.landed"])}

    def make_block(ok: bool):
        return lambda params, c: {
            "facts": {"archive.landed": "yes" if ok else "no"},
            "observation": {"values": {"档案表": ["129078563412"]}, "complete": True},
        }

    expects = [{"op": "contains", "field": "档案表", "value": "129078563412"}]

    register_block("b1", make_block(True))
    ctx = RunContext(run_id="run-fact", block_specs=block_specs)
    result = execute_case(make_case(StepSpec(id="s1", block="b1", expect=expects)), ctx)
    assert result.verdict == OUTCOME_PASS
    assert ctx.facts_ctx.peek("archive.landed").state == "yes"

    clear_registries()
    register_block("b1", make_block(True))
    failing_expects = [{"op": "contains", "field": "档案表", "value": "000000000000"}]
    ctx2 = RunContext(run_id="run-fact2", block_specs=block_specs)
    result2 = execute_case(make_case(StepSpec(id="s1", block="b1", expect=failing_expects)), ctx2)
    assert result2.verdict == OUTCOME_FAIL
    assert ctx2.facts_ctx.peek("archive.landed") is None, "步骤没通过就不许回写事实"


def test_coerce_block_return_accepts_observation_shapes() -> None:
    """块返回形态兼容：``Observation`` 实例 / ``{"observation": {...}}`` / 与 state 并存。"""

    direct = coerce_block_return(Observation(values={"x": 1}, complete=True))
    assert direct.state == BLOCK_UNKNOWN
    assert direct.observation is not None

    mapping = coerce_block_return({"observation": {"values": {"x": 1}, "complete": True}})
    assert mapping.state == BLOCK_UNKNOWN
    assert mapping.observation.values == {"x": 1}

    both = coerce_block_return(
        {"state": BLOCK_FAIL, "observation": {"values": {"x": 1}, "complete": True}}
    )
    assert both.state == BLOCK_FAIL, "并存时 state 生效（旧路径），观测一并带上"
    assert both.observation is not None


def test_observation_is_recorded_in_result_payload() -> None:
    """观测要能落盘复盘：``BlockRecord.to_dict()['observation']`` 是 JSON 形状。"""

    register_block(
        "b1",
        lambda params, c: {
            "observation": {
                "values": {"在网从节点数": 1, "门槛": 1},
                "complete": True,
                "detail": "翻了 1 页",
            }
        },
    )
    result = run_one(
        StepSpec(
            id="s2",
            block="b1",
            expect=[{"op": "ge_field", "field": "在网从节点数", "value": "门槛"}],
        )
    )
    payload = result.to_dict()["blocks"][0]["observation"]
    assert payload == {
        "values": {"在网从节点数": 1, "门槛": 1},
        "complete": True,
        "reason_code": "",
        "detail": "翻了 1 页",
    }
    assert result.to_dict()["steps"][0]["outcome"] == OUTCOME_PASS


def test_block_result_observation_field_is_coerced() -> None:
    """``BlockResult(observation=dict)`` 也能用（块里更顺手），执行器统一归一。"""

    result = BlockResult(state=BLOCK_PASS, observation={"values": {"x": 1}})
    assert isinstance(result.observation, Observation)
    assert result.observation.complete is False, "缺省不完整（绝不替块宣布取全）"
