"""事实求值与块规划（REQS v3 §10.2 事实层 / PLAN P1-3）。

铁律（``test_planner.py`` 守门）：

1. 事实三态 ``yes`` / ``no`` / ``unknown``；**``no`` 与 ``unknown`` 一律使块不可执行**，
   ``unknown`` 绝不折算成 ``yes``/``pass``。
2. 同一 run 内同一事实**只探测一次**（``FactsContext`` 缓存），显式 :meth:`FactsContext.refresh`
   才重探（REQS §10.9 风险应对）；缓存键是**参数感知**的 ``(fact_id, 参数指纹)`` ——
   ``archive.contains(addr)`` 这类带参事实，A 地址的结论绝不能被 B 地址命中。
3. 前序块 ``provides`` 的事实**不提前探测**——否则会把运行前的 ``unknown`` 写进缓存，
   导致下游块被假跳过；这类事实记为 ``deferred``，由执行器在运行时用实时缓存复核。

探测体调用约定：``probe(fact_id, params) -> FactValue | "yes"|"no"|"unknown" | bool | dict | None``。
默认探测体查 ``FACT_REGISTRY`` 并调用 ``fn(params)``；缺失/异常一律降级为 ``unknown``。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .models import (
    FACT_NO,
    FACT_STATES,
    FACT_UNKNOWN,
    FACT_YES,
    BlockSpec,
    CaseSpec,
)
from .registry import get_fact, has_fact

#: 探测体类型：``(fact_id, params) -> 原始取值``
Probe = Callable[[str, dict[str, Any]], Any]


@dataclass
class FactValue:
    """一次事实求值结果（三态 + 说明 + 证据）。"""

    state: str = FACT_UNKNOWN
    detail: str = ""
    evidence: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in FACT_STATES:
            raise ValueError(
                f"未知事实状态 {self.state!r}，允许：{sorted(FACT_STATES)}"
            )

    @property
    def satisfied(self) -> bool:
        """只有 ``yes`` 才算满足；``unknown`` 与 ``no`` 均不满足。"""
        return self.state == FACT_YES

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }

    @classmethod
    def unknown(cls, detail: str = "") -> "FactValue":
        return cls(state=FACT_UNKNOWN, detail=detail)


def coerce_fact_value(raw: Any, *, fact_id: str = "") -> FactValue:
    """把探测体/块的任意返回值**保守地**归一为三态。

    宁可为 ``unknown``，绝不抬高为 ``yes``：
    只有显式 ``yes`` 字面量、``True``、``FactValue(yes)``、``{"state": "yes"}`` 才判 ``yes``。
    """
    if isinstance(raw, FactValue):
        return raw
    if raw is True:
        return FactValue(state=FACT_YES)
    if raw is False:
        return FactValue(state=FACT_NO)
    if raw is None:
        return FactValue(state=FACT_UNKNOWN, detail="探测无结论（None）")
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in FACT_STATES:
            return FactValue(state=token)
        return FactValue(
            state=FACT_UNKNOWN, detail=f"无法识别的事实取值：{raw!r}"
        )
    if isinstance(raw, Mapping):
        state = raw.get("state", raw.get("value"))
        detail = str(raw.get("detail", "") or "")
        evidence = raw.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        if not isinstance(state, str) or state.strip().lower() not in FACT_STATES:
            return FactValue(
                state=FACT_UNKNOWN,
                detail=detail or f"事实取值缺少合法 state：{dict(raw)!r}",
                evidence=list(evidence),
            )
        return FactValue(
            state=state.strip().lower(), detail=detail, evidence=list(evidence)
        )
    return FactValue(
        state=FACT_UNKNOWN,
        detail=f"无法识别的事实取值类型 {type(raw).__name__}（{fact_id or '未知事实'}）",
    )


def _registry_probe(fact_id: str, params: dict[str, Any]) -> Any:
    """默认探测体：查 ``FACT_REGISTRY`` 并调用 ``fn(params)``。"""
    if not has_fact(fact_id):
        return FactValue(state=FACT_UNKNOWN, detail=f"事实未注册：{fact_id!r}")
    return get_fact(fact_id)(dict(params or {}))


def param_fingerprint(params: Any) -> str:
    """参数指纹：``json.dumps(params, sort_keys=True)`` 的 sha1 前 16 位。

    - 键序无关（``sort_keys=True``），``{"a":1,"b":2}`` 与 ``{"b":2,"a":1}`` 同指纹；
    - 不可 JSON 序列化的值按 ``default=str`` 兜底，绝不抛异常（缓存键不能因参数怪而炸）；
    - ``None`` / 空 dict 都算「无参数」→ 同一指纹。
    """
    payload = params if params else {}
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - default=str 已兜底，双保险
        text = repr(payload)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class FactsContext:
    """一次 run 内的事实上下文（带缓存，负责同 run 只探测一次）。

    - ``params``：``{fact_id: {...}}``，事实探测入参（如 ``archive.contains`` 的地址）
    - ``probe``：默认查注册表；测试/能力层可注入
    - ``set()``：块 ``provides`` 回写（按参数指纹覆盖缓存，供下游 ``needs`` 使用）
    - ``refresh()``：清缓存重探（REQS §10.9 的显式重探入口）

    缓存键是 ``(fact_id, 参数指纹)``：参数指纹取 ``params[fact_id]`` 的 JSON 规范形 sha1
    前 16 位。带参事实（``archive.contains(addr)``）因此**不会跨参数复用结论** ——
    A 地址建档成功绝不能让 B 地址的探测命中 A 的缓存（那是跨参数假 PASS）。
    """

    probe: Probe | None = None
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    _cache: dict[tuple[str, str], FactValue] = field(default_factory=dict, repr=False)
    _calls: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.probe is None:
            self.probe = _registry_probe

    # -- 参数指纹 ----------------------------------------------------------- #

    def params_of(self, fact_id: str) -> dict[str, Any]:
        """该事实本次 run 的探测入参（缺省空 dict）。"""
        params = self.params.get(fact_id) or {}
        return dict(params) if isinstance(params, Mapping) else {}

    def fingerprint_of(self, fact_id: str) -> str:
        return param_fingerprint(self.params_of(fact_id))

    def _key(self, fact_id: str, params: Mapping[str, Any] | None = None) -> tuple[str, str]:
        if params is None:
            return (fact_id, self.fingerprint_of(fact_id))
        return (fact_id, param_fingerprint(params))

    # -- 探测 --------------------------------------------------------------- #

    def get(self, fact_id: str) -> FactValue:
        """取事实值（按 ``(fact_id, 参数指纹)`` 命中缓存，同 run 同参只探测一次）。"""
        key = self._key(fact_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = self._probe_once(fact_id)
        self._cache[key] = value
        return value

    def _probe_once(self, fact_id: str) -> FactValue:
        probe = self.probe or _registry_probe
        params = self.params_of(fact_id)
        self._calls += 1
        try:
            raw = probe(fact_id, params)
        except Exception as exc:  # 探测失败 = 不知道，不是 fail
            return FactValue(
                state=FACT_UNKNOWN,
                detail=f"事实探测异常 {fact_id}：{type(exc).__name__}: {exc}",
            )
        return coerce_fact_value(raw, fact_id=fact_id)

    def resolve(self, needs: Iterable[str]) -> dict[str, FactValue]:
        return {fact_id: self.get(fact_id) for fact_id in dict.fromkeys(needs)}

    # -- 写入 / 失效 -------------------------------------------------------- #

    def set(
        self,
        fact_id: str,
        value: Any,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> FactValue:
        """写入事实值（块 ``provides`` 回写），按参数指纹落缓存。

        ``params`` 为 ``None`` 时按 ``self.params[fact_id]``（本 run 声明的那组参数）写；
        带参事实若是块为**另一组参数**拿到的结论，调用方**必须**用 ``params=`` 显式
        给出那组参数，否则宁可不写（写错指纹 = 污染别的参数）。
        """
        coerced = coerce_fact_value(value, fact_id=fact_id)
        self._cache[self._key(fact_id, params)] = coerced
        return coerced

    def peek(self, fact_id: str) -> FactValue | None:
        """只看缓存（按当前声明参数），不触发探测。"""
        return self._cache.get(self._key(fact_id))

    def has_cached(self, fact_id: str) -> bool:
        return self._key(fact_id) in self._cache

    def refresh(self, fact_id: str | None = None) -> None:
        """清缓存（``None`` = 全部；给 fact_id = 清该事实的**所有**参数变体）。"""
        if fact_id is None:
            self._cache.clear()
            return
        for key in [k for k in self._cache if k[0] == fact_id]:
            self._cache.pop(key, None)

    @property
    def probe_calls(self) -> int:
        """实际探测次数（测试/诊断用）。"""
        return self._calls

    def to_dict(self) -> dict[str, Any]:
        """缓存快照。同名事实有多种参数变体时键写作 ``fact_id#指纹``。"""
        variants: dict[str, list[tuple[str, FactValue]]] = {}
        for (fact_id, fingerprint), value in self._cache.items():
            variants.setdefault(fact_id, []).append((fingerprint, value))
        out: dict[str, Any] = {}
        for fact_id, items in variants.items():
            if len(items) == 1:
                out[fact_id] = items[0][1].to_dict()
            else:
                for fingerprint, value in items:
                    out[f"{fact_id}#{fingerprint}"] = value.to_dict()
        return out


def resolve(
    needs: Iterable[str], facts_ctx: "FactsContext | None" = None
) -> dict[str, FactValue]:
    """求值一组事实 id（模块级便捷入口）。

    ``facts_ctx`` 为 ``None`` 时用一次性上下文（不跨调用缓存），调用方应始终传入
    同 run 复用的 :class:`FactsContext` 以获得缓存语义。
    """
    ctx = facts_ctx if isinstance(facts_ctx, FactsContext) else FactsContext()
    return ctx.resolve(needs)


@dataclass
class PlannedBlock:
    """规划结果：某块在本次 run 里能否执行，以及不能执行的原因。"""

    block_id: str
    runnable: bool
    skip_reason: str = ""
    needs: list[str] = field(default_factory=list)
    facts: dict[str, FactValue] = field(default_factory=dict)
    deferred: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "runnable": self.runnable,
            "skip_reason": self.skip_reason,
            "needs": list(self.needs),
            "facts": {fid: fv.to_dict() for fid, fv in self.facts.items()},
            "deferred": list(self.deferred),
        }


def describe_facts(state_map: Mapping[str, FactValue]) -> str:
    parts = []
    for fact_id, value in state_map.items():
        detail = f"（{value.detail}）" if value.detail else ""
        parts.append(f"{fact_id}={value.state}{detail}")
    return ", ".join(parts)


def plan(
    case: CaseSpec,
    facts_ctx: "FactsContext | None" = None,
    *,
    block_specs: Mapping[str, BlockSpec] | None = None,
) -> list[PlannedBlock]:
    """把一个用例规划成有序的块清单。

    - 用例级 ``needs`` 未满足 → 全部块不可执行（原因点名事实）
    - 块级 ``needs``（取自 ``block_specs``）未满足 → 该块及其下游不可执行
    - 前序块 ``provides`` 的事实记为 ``deferred``：不提前探测，交给执行器运行时复核
    """
    ctx = facts_ctx if isinstance(facts_ctx, FactsContext) else FactsContext()
    specs = dict(block_specs or {})

    case_facts = ctx.resolve(case.needs) if case.needs else {}
    unmet_case = {fid: fv for fid, fv in case_facts.items() if not fv.satisfied}

    provided_so_far: set[str] = set()
    blocked_at = ""
    planned: list[PlannedBlock] = []

    for block_id in case.blocks:
        spec = specs.get(block_id)
        needs = list(dict.fromkeys(spec.needs)) if spec is not None else []
        deferred = [fid for fid in needs if fid in provided_so_far and not ctx.has_cached(fid)]
        to_probe = [fid for fid in needs if fid not in deferred]
        block_facts = ctx.resolve(to_probe) if to_probe else {}
        # 用例级事实一并带上：块被拦下时，结果里要能同时看到「哪条用例前置事实不满足」
        facts = {**case_facts, **block_facts}
        unmet_block = {fid: fv for fid, fv in block_facts.items() if not fv.satisfied}

        root_reason = ""
        if unmet_case:
            root_reason = f"用例前置事实未满足：{describe_facts(unmet_case)}"
        elif unmet_block:
            root_reason = f"块前置事实未满足：{describe_facts(unmet_block)}"

        runnable = True
        skip_reason = ""
        if blocked_at:
            # 链式断点：原因指向**首个断点块**，本块自身条件若也不满足再附带说明
            runnable = False
            skip_reason = f"前序块 {blocked_at} 不可执行（链条已断，本块随之跳过）"
            if root_reason:
                skip_reason += f"；本块另有：{root_reason}"
        elif root_reason:
            runnable = False
            skip_reason = root_reason

        if not runnable and not blocked_at:
            blocked_at = block_id

        planned.append(
            PlannedBlock(
                block_id=block_id,
                runnable=runnable,
                skip_reason=skip_reason,
                needs=needs,
                facts=facts,
                deferred=deferred,
            )
        )

        if spec is not None and spec.provides:
            provided_so_far.update(spec.provides)

    return planned


__all__ = [
    "FACT_YES",
    "FACT_NO",
    "FACT_UNKNOWN",
    "FACT_STATES",
    "FactValue",
    "describe_facts",
    "coerce_fact_value",
    "param_fingerprint",
    "FactsContext",
    "resolve",
    "PlannedBlock",
    "plan",
]
