"""用例执行器（REQS v3 §10.5 KE-3 / PLAN P1-4；REQS-0042 D3-1/D3-2 起按「步骤」执行）。

流程：``plan()`` → 逐步 ``get_block(id)(params, ctx)`` → 聚合。

铁律（``test_executor.py`` 守门）：

1. 结论**一律**落 ``test_automation.models.AssertionResult``（``outcome ∈ pass|fail|inconclusive``），
   本模块不新造结论模型。
2. ``unknown`` 与 ``skipped`` **永不**计入 PASS 计数（REQS RU-3「绝不假 PASS」）；
   一个用例只有在**所有步骤都 pass** 时才判 ``pass``。
3. ``stop_on_fail=True`` 时首个非 pass 步骤 → 下游全 ``skipped``；``stop_on_fail=False`` 时
   其余步骤照常执行并各自记录（REQS KE-3）。
4. 块返回值**只认显式结论**（``state`` / ``outcome`` / :class:`BlockResult` /
   :class:`AssertionResult`）；**不认** ``ok`` / ``passed`` —— 能力层的 ``{"ok": true}``
   只代表「服务处理成功」，不代表「设备测试通过」，折算成 PASS 就是假 PASS。

步骤实例化（REQS-0042 D3-1）
===========================
规划单元从「块」变成「步骤」（``StepSpec``）：同一个块可以在一条用例里出现多次，
每次带自己的参数。每条 :class:`BlockRecord` 因此带 ``step_id``（老用例等于块 id）
与 ``params``（该步**实际下发**的参数快照，D4-1 参数冻结的数据基础）。

失败策略：``on_fail`` 与 ``stop_on_fail`` 的关系
================================================
- 步骤**显式**写 ``on_fail``（``plan[].on_fail``）→ 以它为准：
  ``stop`` = 本步非 pass 后下游记 ``skipped``；``continue`` = 下游照跑。
- 步骤**没写** ``on_fail``（含 ``blocks[]`` 自动展开的所有步骤）→ 继承 run 级
  ``stop_on_fail``（用例参数 ``stop_on_fail`` / ``ctx.stop_on_fail``，方案的
  ``stop_on_fail`` 由 CLI 传进来）。
- 即：``stop_on_fail`` 是**默认值**，步骤显式声明是**覆盖**；两者同时出现时以步骤声明为准。

块参数优先级（REQS-0042 D3-2，低 → 高）
======================================
1. 块声明 ``spec.action``（含 ``action.params``，``null`` 已在加载期解析成配置默认）；
2. 步骤声明 ``StepSpec.params``（``plan[].params``）；
3. ``ctx.step_params[step_id]``（运行期**按步骤**覆盖，供 API/S3 快照层用）；
4. ``ctx.block_params[block_id]``（run 级 ``--params``，**最高**；会广播到该块的所有步骤）。

两者关系一句话：**调用方运行期显式给的值最高**（临时改地址复跑），其次才是用例里
为某个步骤写死的值。注意 #4 是「按块」广播的——同一个块被实例化两次时，run 级
``--params`` 对两次都生效；要让两次不同，把差异写进 ``plan[].params``（#2）即可。

块执行体调用约定：``fn(params, ctx)`` —— ``params`` = 块声明 ``action`` 字典（含
``afn``/``fn``/``params`` 或 ``recipe``/``semantic``）按上面的优先级叠加，再叠加两个
**保留键**（优先级最高，任何一层都覆盖不掉）：

- ``params["_assert"]``：块声明的 ``spec.assert_``（JSON 里的 ``assert``）原样副本；
- ``params["_cleanup"]``：块声明的 ``spec.cleanup`` 原样副本。

``ctx`` 就是调用方传入的同一个 :class:`RunContext` 对象（能力句柄放 ``ctx.extras``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from test_automation.models import AssertionResult

from .models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_STATES,
    BLOCK_UNKNOWN,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    OUTCOMES,
    BlockSpec,
    CaseSpec,
)
from .planner import (
    FactValue,
    FactsContext,
    coerce_fact_value,
    describe_facts,
    plan,
    step_label,
)
from .registry import NotFound, get_block

#: 块执行态 → 对外结论（``skipped``/``unknown`` 都是 inconclusive，绝不 pass）
BLOCK_TO_OUTCOME: dict[str, str] = {
    BLOCK_PASS: OUTCOME_PASS,
    BLOCK_FAIL: OUTCOME_FAIL,
    BLOCK_SKIPPED: OUTCOME_INCONCLUSIVE,
    BLOCK_UNKNOWN: OUTCOME_INCONCLUSIVE,
}

#: 注入块执行体的保留参数键（块级 ``assert`` / ``cleanup`` 声明 → 执行体入参）。
#: 保留键优先级最高：``ctx.block_params`` 覆盖不掉，否则运行时能把块声明的判据改掉。
PARAM_ASSERT = "_assert"
PARAM_CLEANUP = "_cleanup"
RESERVED_PARAM_KEYS = (PARAM_ASSERT, PARAM_CLEANUP)

OUTCOME_TO_BLOCK: dict[str, str] = {
    OUTCOME_PASS: BLOCK_PASS,
    OUTCOME_FAIL: BLOCK_FAIL,
    OUTCOME_INCONCLUSIVE: BLOCK_UNKNOWN,
}


# --------------------------------------------------------------------------- #
# 上下文与结果
# --------------------------------------------------------------------------- #


@dataclass
class RunContext:
    """一次用例执行的上下文，同时作为块执行体的第二个入参。

    - ``run_id``：落盘/证据的关联键（``data/case_runs/<run_id>/result.json``）
    - ``facts_ctx``：事实缓存（同 run 内事实只探测一次）
    - ``block_specs``：``{block_id: BlockSpec}``，规划器据此取块级 ``needs``/``provides``
    - ``block_params``：``{block_id: {...}}``，**run 级**参数覆盖（最高优先级）；
      按块广播——同一个块被实例化多次时对每一步都生效（D3-2 第 4 层）
    - ``step_params``：``{step_id: {...}}``，**按步骤**参数覆盖（D3-2 第 3 层）。
      同名块多步骤各自带参数时用它/或用 ``StepSpec.params``；
      这是 0042 D3-1 的兼容落点：老的按块键（``block_params``）语义不变，
      新代码要「只改某一步」就用这个，避免同块多步互相覆盖
    - ``assert_evaluator``：用例级断言求值器 ``(decl, result) -> AssertionResult``；
      **不提供时会用例级断言记为 inconclusive，绝不判 pass**
    - ``extras``：块/能力层自由挂载（能力句柄、HTTP 客户端、device 等）
    """

    run_id: str = ""
    facts_ctx: FactsContext = field(default_factory=FactsContext)
    block_specs: dict[str, BlockSpec] = field(default_factory=dict)
    block_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    step_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    assert_evaluator: Callable[[dict[str, Any], Any], Any] | None = None
    stop_on_fail: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    # 让块执行体可以像 dict 一样挂东西（ctx["handle"] / ctx.extras["handle"]）
    def __getitem__(self, key: str) -> Any:
        return self.extras[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self.extras

    def get(self, key: str, default: Any = None) -> Any:
        return self.extras.get(key, default)


@dataclass
class BlockResult:
    """块执行体推荐返回值（也可返回 AssertionResult / dict / bool / str / None）。"""

    state: str = BLOCK_UNKNOWN
    detail: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    expected: Any = None
    actual: Any = None
    assertion: AssertionResult | None = None

    def __post_init__(self) -> None:
        if self.state not in BLOCK_STATES:
            raise ValueError(
                f"未知块执行态 {self.state!r}，允许：{sorted(BLOCK_STATES)}"
            )


@dataclass
class BlockRecord:
    """单个**步骤**的执行记录（步骤实例化：同一块可有多条记录）。"""

    block_id: str
    state: str
    detail: str = ""
    facts: dict[str, FactValue] = field(default_factory=dict)
    assertion: AssertionResult | None = None
    runnable: bool = True
    step_id: str = ""
    on_fail: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        return BLOCK_TO_OUTCOME.get(self.state, OUTCOME_INCONCLUSIVE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "step_id": self.step_id or self.block_id,
            "state": self.state,
            "outcome": self.outcome,
            "on_fail": self.on_fail,
            "detail": self.detail,
            # D4-1 参数冻结的数据基础：该步实际下发的参数（不含 _assert/_cleanup 保留键）
            "params": dict(self.params),
            "facts": {fid: fv.to_dict() for fid, fv in self.facts.items()},
            "assertion_id": self.assertion.assertion_id if self.assertion else "",
            "message": self.assertion.message if self.assertion else self.detail,
        }


@dataclass
class CaseRunResult:
    """一次用例执行结果（``to_dict()`` 为落盘/API 口径）。"""

    case_id: str
    run_id: str = ""
    blocks: list[BlockRecord] = field(default_factory=list)
    assertions: list[AssertionResult] = field(default_factory=list)
    verdict: str = OUTCOME_INCONCLUSIVE
    failed_block: str = ""
    blocked_block: str = ""
    #: 断点所在的**步骤** id（步骤实例化后同一块可能有多步，块 id 定位不到具体那一步；
    #: 老用例的 step_id 等于块 id，因此这两个字段与上面两个一致）
    failed_step: str = ""
    blocked_step: str = ""
    unmet_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    stop_on_fail: bool = False

    def __post_init__(self) -> None:
        if self.verdict not in OUTCOMES:
            raise ValueError(
                f"未知 verdict {self.verdict!r}，允许：{sorted(OUTCOMES)}"
            )

    @property
    def counts(self) -> dict[str, int]:
        """分态计数；``pass`` 只统计 state == pass 的块（unknown/skipped 永不计入）。"""
        counts = {BLOCK_PASS: 0, BLOCK_FAIL: 0, BLOCK_SKIPPED: 0, BLOCK_UNKNOWN: 0}
        for record in self.blocks:
            counts[record.state] = counts.get(record.state, 0) + 1
        counts["total"] = len(self.blocks)
        return counts

    def passed_blocks(self) -> list[str]:
        return [b.block_id for b in self.blocks if b.state == BLOCK_PASS]

    def step_summaries(self) -> list[dict[str, Any]]:
        """每个步骤的结论摘要（``run --json`` / UI 汇总用；顺序即执行序）。

        同一块被实例化多次时，``blocks[]`` 里会有多条同 ``block_id`` 的记录，
        只有 ``step_id`` 能把它们区分开。
        """

        return [
            {
                "step_id": record.step_id or record.block_id,
                "block_id": record.block_id,
                "state": record.state,
                "outcome": record.outcome,
                "on_fail": record.on_fail,
            }
            for record in self.blocks
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "verdict": self.verdict,
            "failed_block": self.failed_block,
            "blocked_block": self.blocked_block,
            "failed_step": self.failed_step,
            "blocked_step": self.blocked_step,
            "stop_on_fail": self.stop_on_fail,
            "counts": self.counts,
            "unmet_facts": dict(self.unmet_facts),
            "blocks": [b.to_dict() for b in self.blocks],
            "steps": self.step_summaries(),
            "assertions": [a.to_dict() for a in self.assertions],
        }


# --------------------------------------------------------------------------- #
# 返回值归一
# --------------------------------------------------------------------------- #


def _state_from_outcome(outcome: str) -> str:
    return OUTCOME_TO_BLOCK.get(outcome, BLOCK_UNKNOWN)


def coerce_block_return(raw: Any) -> BlockResult:
    """把块返回值**保守地**归一为 :class:`BlockResult`（无法识别 → unknown）。"""
    if isinstance(raw, BlockResult):
        return raw
    if isinstance(raw, AssertionResult):
        return BlockResult(
            state=_state_from_outcome(raw.outcome),
            detail=raw.message,
            evidence_ids=list(raw.evidence_ids),
            expected=raw.expected,
            actual=raw.actual,
            assertion=raw,
        )
    if raw is None:
        return BlockResult(state=BLOCK_UNKNOWN, detail="块未返回结论（None）")
    if isinstance(raw, bool):
        return BlockResult(
            state=BLOCK_PASS if raw else BLOCK_FAIL,
            detail="" if raw else "块返回 False",
        )
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in BLOCK_STATES:
            return BlockResult(state=token, detail=raw)
        if token in OUTCOMES:
            return BlockResult(state=_state_from_outcome(token), detail=raw)
        return BlockResult(
            state=BLOCK_UNKNOWN, detail=f"无法识别的块返回字符串：{raw!r}"
        )
    if isinstance(raw, Mapping):
        detail = str(raw.get("detail") or raw.get("message") or "")
        facts = raw.get("facts") or {}
        if not isinstance(facts, Mapping):
            facts = {}
        evidence = raw.get("evidence_ids") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        state = raw.get("state")
        if state is not None:
            token = str(state).strip().lower()
            if token in BLOCK_STATES:
                return BlockResult(
                    state=token,
                    detail=detail,
                    facts=dict(facts),
                    evidence_ids=list(evidence),
                    expected=raw.get("expected"),
                    actual=raw.get("actual"),
                )
            if token in OUTCOMES:
                return BlockResult(
                    state=_state_from_outcome(token),
                    detail=detail,
                    facts=dict(facts),
                    evidence_ids=list(evidence),
                    expected=raw.get("expected"),
                    actual=raw.get("actual"),
                )
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail=detail or f"无法识别的块 state：{state!r}",
                facts=dict(facts),
            )
        if "outcome" in raw:
            token = str(raw["outcome"]).strip().lower()
            return BlockResult(
                state=_state_from_outcome(token)
                if token in OUTCOMES
                else BLOCK_UNKNOWN,
                detail=detail or f"无法识别的块 outcome：{raw.get('outcome')!r}",
                facts=dict(facts),
                evidence_ids=list(evidence),
            )
        # 只认显式 state / outcome。**不认** ok / passed：
        # 能力层所有动作都带 {"ok": true}（= 服务处理成功），块写
        # ``return cap.verify(...)`` 时折算成 PASS 就是假 PASS（REQS RU-3）。
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail=detail
            or "块返回的 dict 里没有显式 state/outcome 结论字段"
            "（ok/passed 只表示服务处理成功，不构成判定）",
            facts=dict(facts),
        )
    return BlockResult(
        state=BLOCK_UNKNOWN, detail=f"无法识别的块返回类型 {type(raw).__name__}"
    )


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #


def _make_assertion(
    run_id: str,
    assertion_id: str,
    state: str,
    detail: str,
    *,
    expected: Any = None,
    actual: Any = None,
    evidence_ids: Iterable[str] = (),
) -> AssertionResult:
    return AssertionResult(
        run_id=run_id,
        assertion_id=assertion_id,
        outcome=BLOCK_TO_OUTCOME.get(state, OUTCOME_INCONCLUSIVE),
        expected=expected,
        actual=actual,
        evidence_ids=list(evidence_ids),
        message=detail,
    )


def _jsonable(value: Any) -> Any:
    """把参数值归一成可 JSON 序列化的形状（落盘/--json 不能因一个怪对象就炸）。"""

    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _record(
    block_id: str,
    state: str,
    detail: str,
    run_id: str,
    facts: Mapping[str, FactValue] | None = None,
    *,
    assertion_id: str = "",
    runnable: bool = True,
    assertion: AssertionResult | None = None,
    step_id: str = "",
    on_fail: str = "",
    params: Mapping[str, Any] | None = None,
) -> BlockRecord:
    assertion = assertion or _make_assertion(
        run_id, assertion_id or block_id, state, detail
    )
    return BlockRecord(
        block_id=block_id,
        state=state,
        detail=detail,
        facts=dict(facts or {}),
        assertion=assertion,
        runnable=runnable,
        step_id=step_id or block_id,
        on_fail=on_fail,
        params={key: _jsonable(value) for key, value in (params or {}).items()},
    )


#: 断言 id 由「用例 id + 步骤 id」组成，便于结果里回指「断在哪一步的哪条断言」。
#: 老用例（``blocks[]`` 自动展开）的步骤 id 就是块 id，因此仍是 ``用例.块``，逐字兼容。
def _assertion_id(case_id: str, step_id: str) -> str:
    return f"{case_id}.{step_id}"


def _provided_fact_params(value: Any) -> Mapping[str, Any] | None:
    """块回写带参事实时，可用 ``{"state": "yes", "params": {...}}`` 声明参数组。

    没有显式 ``params`` → ``None``（``FactsContext.set`` 按本 run 的声明参数写）。
    """
    if isinstance(value, Mapping):
        params = value.get("params")
        if isinstance(params, Mapping):
            return dict(params)
    return None


def _field_of(item: Any, name: str) -> Any:
    """从 ``PlannedBlock``（dataclass）或普通 dict 取字段（测试/外部装配两种都认）。"""

    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def effective_params(
    spec: BlockSpec | None,
    item: Any,
    ctx: "RunContext",
) -> dict[str, Any]:
    """算出一个步骤**实际下发**的块参数（D3-2 优先级，不含保留键）。

    低 → 高：块声明 ``action`` < 步骤声明 ``StepSpec.params`` <
    ``ctx.step_params[step_id]`` < ``ctx.block_params[block_id]``（run 级 ``--params``）。

    ``item`` 是 :class:`case_runner.core.planner.PlannedBlock`（有 ``block_id`` /
    ``step_id`` / ``params``）；传 dict 也行（测试/外部装配）。保留键 ``_assert`` /
    ``_cleanup`` 由 :func:`_call_params` 单独注入，这里不出现。
    """

    block_id = _field_of(item, "block_id")
    step_id = _field_of(item, "step_id") or block_id
    step_declared = _field_of(item, "params") or {}

    params: dict[str, Any] = dict(spec.action) if spec is not None else {}
    for layer in (
        step_declared,
        ctx.step_params.get(step_id),
        ctx.block_params.get(block_id),
    ):
        if layer:
            params.update(layer)
    return params


def _call_params(
    spec: BlockSpec | None, item: Any, ctx: "RunContext"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 ``(调用参数含保留键, 参数快照不含保留键)``。"""

    params = effective_params(spec, item, ctx)
    snapshot = {key: value for key, value in params.items() if key not in RESERVED_PARAM_KEYS}
    # 保留键最后写入：块级 assert / cleanup 必须进执行体（声明与实现不许漂移），
    # 且任何一层参数覆盖**不得**覆盖它们。给副本，块改了不污染 BlockSpec。
    params[PARAM_ASSERT] = [dict(item_) for item_ in (spec.assert_ if spec else [])]
    params[PARAM_CLEANUP] = [dict(item_) for item_ in (spec.cleanup if spec else [])]
    return params, snapshot


def _run_block(
    case: CaseSpec,
    ctx: RunContext,
    item: Any,
    spec: BlockSpec | None,
    gating_facts: Mapping[str, FactValue],
    *,
    on_fail: str = "",
) -> BlockRecord:
    block_id = _field_of(item, "block_id")
    step_id = _field_of(item, "step_id") or block_id
    assertion_id = _assertion_id(case.id, step_id)
    params, snapshot = _call_params(spec, item, ctx)
    try:
        fn = get_block(block_id)
    except NotFound as exc:
        return _record(
            block_id,
            BLOCK_UNKNOWN,
            f"块执行体未注册，无法判定：{exc}",
            ctx.run_id,
            gating_facts,
            assertion_id=assertion_id,
            runnable=False,
            step_id=step_id,
            on_fail=on_fail,
            params=snapshot,
        )

    try:
        raw = fn(params, ctx)
    except Exception as exc:  # 块炸了 = 没通过，绝不当作 pass
        return _record(
            block_id,
            BLOCK_FAIL,
            f"块执行异常：{type(exc).__name__}: {exc}",
            ctx.run_id,
            gating_facts,
            assertion_id=assertion_id,
            step_id=step_id,
            on_fail=on_fail,
            params=snapshot,
        )

    coerced = coerce_block_return(raw)
    facts: dict[str, FactValue] = dict(gating_facts)
    for fact_id, value in coerced.facts.items():
        facts[fact_id] = coerce_fact_value(value, fact_id=fact_id)

    # 只有 pass 的块才把 provides 的**显式**取值回写事实缓存（绝不凭空造事实）。
    # 带参事实（``archive.contains(addr)``）：块可在取值里带 ``params`` 声明该结论属于
    # 哪组参数；不带则按本 run 为该事实声明的那组参数写（指纹不符下游会重探，不会污染）。
    if coerced.state == BLOCK_PASS and spec is not None:
        for fact_id in spec.provides:
            if fact_id in coerced.facts:
                raw_value = coerced.facts[fact_id]
                ctx.facts_ctx.set(
                    fact_id, raw_value, params=_provided_fact_params(raw_value)
                )

    assertion = coerced.assertion or _make_assertion(
        ctx.run_id,
        assertion_id,
        coerced.state,
        coerced.detail,
        expected=coerced.expected,
        actual=coerced.actual,
        evidence_ids=coerced.evidence_ids,
    )
    return BlockRecord(
        block_id=block_id,
        state=coerced.state,
        detail=coerced.detail,
        facts=facts,
        assertion=assertion,
        runnable=True,
        step_id=step_id,
        on_fail=on_fail,
        params={key: _jsonable(value) for key, value in snapshot.items()},
    )


def _verdict(records: list[BlockRecord]) -> str:
    """用例结论：只有「所有块都 pass」才是 pass。"""
    if not records:
        return OUTCOME_INCONCLUSIVE
    if any(r.state == BLOCK_FAIL for r in records):
        return OUTCOME_FAIL
    if all(r.state == BLOCK_PASS for r in records):
        return OUTCOME_PASS
    return OUTCOME_INCONCLUSIVE


def _as_assertion(
    raw: Any, *, run_id: str, assertion_id: str, fallback_message: str
) -> AssertionResult:
    if isinstance(raw, AssertionResult):
        return raw
    if isinstance(raw, bool):
        return _make_assertion(
            run_id,
            assertion_id,
            BLOCK_PASS if raw else BLOCK_FAIL,
            fallback_message,
            actual=raw,
        )
    if isinstance(raw, Mapping):
        outcome = str(raw.get("outcome", "")).strip().lower()
        if outcome in OUTCOMES:
            return AssertionResult(
                run_id=str(raw.get("run_id") or run_id),
                assertion_id=str(raw.get("assertion_id") or assertion_id),
                outcome=outcome,
                expected=raw.get("expected"),
                actual=raw.get("actual"),
                evidence_ids=list(raw.get("evidence_ids") or []),
                message=str(raw.get("message") or fallback_message),
            )
        return _make_assertion(
            run_id,
            assertion_id,
            BLOCK_UNKNOWN,
            f"{fallback_message}：求值结果缺少合法 outcome（{raw!r}）",
        )
    return _make_assertion(
        run_id,
        assertion_id,
        BLOCK_UNKNOWN,
        f"{fallback_message}：求值结果类型无法识别 {type(raw).__name__}",
    )


def _eval_case_asserts(
    case: CaseSpec, ctx: RunContext, result: CaseRunResult
) -> list[AssertionResult]:
    """用例级聚合断言。没有求值器 → inconclusive（绝不假 PASS）。"""
    produced: list[AssertionResult] = []
    for index, decl in enumerate(case.asserts):
        assertion_id = f"{case.id}.assert[{index}]"
        if ctx.assert_evaluator is None:
            produced.append(
                _make_assertion(
                    ctx.run_id,
                    assertion_id,
                    BLOCK_UNKNOWN,
                    "未提供用例级断言求值器（ctx.assert_evaluator），"
                    "该断言未求值：unknown 绝不判 pass",
                    expected=decl,
                )
            )
            continue
        try:
            raw = ctx.assert_evaluator(decl, result)
        except Exception as exc:
            produced.append(
                _make_assertion(
                    ctx.run_id,
                    assertion_id,
                    BLOCK_UNKNOWN,
                    f"用例级断言求值异常：{type(exc).__name__}: {exc}",
                    expected=decl,
                )
            )
            continue
        produced.append(
            _as_assertion(
                raw,
                run_id=ctx.run_id,
                assertion_id=assertion_id,
                fallback_message=f"用例级断言 {assertion_id}",
            )
        )
    return produced


def _apply_case_asserts(verdict: str, case_assertions: list[AssertionResult]) -> str:
    if not case_assertions:
        return verdict
    outcomes = [a.outcome for a in case_assertions]
    if OUTCOME_FAIL in outcomes:
        return OUTCOME_FAIL
    if any(outcome != OUTCOME_PASS for outcome in outcomes):
        # 块全 pass 但用例级断言未求值/未通过 → 降级，绝不假 PASS
        return OUTCOME_INCONCLUSIVE if verdict == OUTCOME_PASS else verdict
    return verdict


def execute_case(
    case: CaseSpec,
    ctx: RunContext | None = None,
    *,
    stop_on_fail: bool | None = None,
) -> CaseRunResult:
    """执行一个用例（按**步骤**顺序执行，REQS-0042 D3-1）。

    - ``ctx``：:class:`RunContext`（``None`` 时新建一个空上下文）
    - ``stop_on_fail``：``None`` = 取 ``ctx.stop_on_fail``；``True`` 时首个非 pass 步骤后
      下游全 ``skipped``。它只是**默认值**：某一步显式写了 ``on_fail`` 时以该步为准
      （``on_fail="continue"`` 时即使 ``stop_on_fail=True`` 后续步骤也照跑）。
    """
    if ctx is None:
        ctx = RunContext()
    if not isinstance(ctx, RunContext):
        raise TypeError(
            f"ctx 必须是 RunContext 或 None，收到 {type(ctx).__name__}"
            "（请用 case_runner.core.executor.RunContext 承载 facts_ctx / 能力句柄）"
        )
    do_stop = ctx.stop_on_fail if stop_on_fail is None else bool(stop_on_fail)
    facts_ctx = ctx.facts_ctx

    planned = plan(case, facts_ctx, block_specs=ctx.block_specs)

    records: list[BlockRecord] = []
    assertions: list[AssertionResult] = []
    unmet: dict[str, dict[str, Any]] = {}
    failed_block = ""
    blocked_block = ""
    failed_step = ""
    blocked_step = ""
    stopped_at = ""
    stopped_why = ""

    # 用例级事实先落一份结论，保证「断点原因点名事实」在结果里始终可见
    if case.needs:
        for fact_id, value in facts_ctx.resolve(case.needs).items():
            if not value.satisfied:
                unmet.setdefault(fact_id, value.to_dict())

    for item in planned:
        spec = ctx.block_specs.get(item.block_id)
        step_id = item.step_id or item.block_id
        label = step_label(step_id, item.block_id)
        # 本步实际生效的失败策略：显式 on_fail 优先，否则继承 run/方案级 stop_on_fail
        effective_on_fail = item.resolved_on_fail(do_stop)
        step_kwargs = {
            "assertion_id": _assertion_id(case.id, step_id),
            "step_id": step_id,
            "on_fail": effective_on_fail,
            "params": effective_params(spec, item, ctx),
        }
        if stopped_at:
            record = _record(
                item.block_id,
                BLOCK_SKIPPED,
                f"前序块 {stopped_at} 非 pass（{stopped_why}），下游跳过",
                ctx.run_id,
                item.facts,
                runnable=False,
                **step_kwargs,
            )
        elif not item.runnable:
            record = _record(
                item.block_id,
                BLOCK_SKIPPED,
                item.skip_reason,
                ctx.run_id,
                item.facts,
                runnable=False,
                **step_kwargs,
            )
        else:
            live = facts_ctx.resolve(item.needs) if item.needs else {}
            unmet_live = {fid: fv for fid, fv in live.items() if not fv.satisfied}
            if unmet_live:
                record = _record(
                    item.block_id,
                    BLOCK_SKIPPED,
                    "块前置事实未满足（运行时复核）：" + describe_facts(unmet_live),
                    ctx.run_id,
                    {**item.facts, **live},
                    runnable=False,
                    **step_kwargs,
                )
            else:
                record = _run_block(
                    case,
                    ctx,
                    item,
                    spec,
                    {**item.facts, **live},
                    on_fail=effective_on_fail,
                )

        for fact_id, value in record.facts.items():
            if not value.satisfied:
                unmet.setdefault(fact_id, value.to_dict())
        if record.state == BLOCK_FAIL and not failed_block:
            failed_block = item.block_id
            failed_step = step_id
        if record.state == BLOCK_SKIPPED and not blocked_block:
            blocked_block = item.block_id
            blocked_step = step_id
        # 断链判定按**本步生效的策略**：显式 on_fail 优先，否则跟 run 级 stop_on_fail。
        # 文案对老用例逐字不变（do_stop 且本步没声明 on_fail → "stop_on_fail=True"）。
        if (
            record.state != BLOCK_PASS
            and effective_on_fail == "stop"
            and not stopped_at
        ):
            stopped_at = label
            stopped_why = (
                "stop_on_fail=True"
                if item.on_fail is None
                else f"步骤 {step_id} 声明 on_fail=stop"
            )

        records.append(record)
        assertions.append(record.assertion)

    result = CaseRunResult(
        case_id=case.id,
        run_id=ctx.run_id,
        blocks=records,
        assertions=assertions,
        verdict=_verdict(records),
        failed_block=failed_block,
        blocked_block=blocked_block,
        failed_step=failed_step,
        blocked_step=blocked_step,
        unmet_facts=unmet,
        stop_on_fail=do_stop,
    )

    case_assertions = _eval_case_asserts(case, ctx, result)
    if case_assertions:
        result.assertions.extend(case_assertions)
        result.verdict = _apply_case_asserts(result.verdict, case_assertions)
        if result.verdict == OUTCOME_FAIL and not result.failed_block:
            for assertion in case_assertions:
                if assertion.outcome == OUTCOME_FAIL:
                    result.failed_block = assertion.assertion_id
                    break

    return result


__all__ = [
    "BLOCK_TO_OUTCOME",
    "OUTCOME_TO_BLOCK",
    "BlockResult",
    "BlockRecord",
    "CaseRunResult",
    "RunContext",
    "coerce_block_return",
    "execute_case",
]
