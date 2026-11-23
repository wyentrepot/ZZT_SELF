"""用例执行器（REQS v3 §10.5 KE-3 / PLAN P1-4；REQS-0042 D3-1/D3-2 起按「步骤」执行）。

流程：``plan()`` → 逐步 ``get_block(id)(params, ctx)`` → 聚合。

铁律（``test_executor.py`` 守门）：

1. **分两层，不许串层**（REQS-0042 §3.5 / D2-4）：
   - 断言层：结论**一律**落 ``test_automation.models.AssertionResult``
     （``outcome ∈ pass|fail|inconclusive``，``ASSERTION_OUTCOMES`` **一字不改**）；
     ``blocked`` **永远不出现**在断言层。
   - 结论层：用例 / 方案 ``verdict`` **四态** ``pass|fail|blocked|inconclusive``
     （``core.models.OUTCOMES``），由 :data:`BLOCK_TO_OUTCOME` 聚合而来：
     ``pass→pass`` / ``fail→fail`` / ``skipped→blocked``（台架不具备条件）/
     ``unknown→inconclusive``（说不清）。
2. ``unknown`` 与 ``skipped`` **永不**计入 PASS 计数（REQS RU-3「绝不假 PASS」）；
   一个用例只有在**所有步骤都 pass** 时才判 ``pass``。
3. ``stop_on_fail=True`` 时首个非 pass 步骤 → 下游全 ``skipped``；``stop_on_fail=False`` 时
   其余步骤照常执行并各自记录（REQS KE-3）。
4. 块返回值**只认显式结论**（``state`` / ``outcome`` / :class:`BlockResult` /
   :class:`AssertionResult`）；**不认** ``ok`` / ``passed`` —— 能力层的 ``{"ok": true}``
   只代表「服务处理成功」，不代表「设备测试通过」，折算成 PASS 就是假 PASS。
5. 同一用例内多块混合时取**最严重**者：``fail > blocked > inconclusive > pass``
   （:data:`OUTCOME_PRIORITY` / :func:`aggregate_verdict`）。方案级聚合用同一份口径
   —— **不另写一套**（``cli`` / API 一律调 :func:`aggregate_verdict`）。

步骤实例化（REQS-0042 D3-1）
===========================
规划单元从「块」变成「步骤」（``StepSpec``）：同一个块可以在一条用例里出现多次，
每次带自己的参数。每条 :class:`BlockRecord` 因此带 ``step_id``（老用例等于块 id）
与 ``params``（该步**实际下发**的参数快照，D4-1 参数冻结的数据基础）。

自愈留痕（REQS-0042 §3.4 / SH-2 / D4-4）
========================================
- 自愈动作发生在块内部（``blocks.common.with_self_heal``），本模块提供**收集器**
  （:func:`self_heal_sink` / :func:`record_self_heal`）：执行器每步开一个 sink，
  退出时把收到的留痕写进该步 :class:`BlockRecord.self_healed`；
  块也可直接填 ``BlockResult.self_healed``（两边都填按四项字段去重）。
- 留痕是**自动决策**，聚合到 ``CaseRunResult.to_dict()["self_healed"]``（扁平）。
  它**不能**混进 ``interventions[]``（人/AI 介入，D4-4 要求恒为空数组）。
- 留痕只记录「动过什么」；块态语义不变：自愈成功照常 pass/fail，仍被占用则照旧
  ``skipped``（聚合处映射成 ``blocked``，见 §3.5）。
- 每项留痕同时转成一条 ``decisions[]`` 里的 ``kind="self_heal"`` 自动决策（D4-4）。

自动决策留痕（REQS-0042 D4-4）
=============================
``CaseRunResult`` 里有三份互不混装的数组：

- ``decisions[]``：本次运行中所有**自动**决策（自愈 / 重试 / 等待轮询 / 超时收尾），
  每条至少带 ``{kind, step_id, block_id, attempts, reason}``。来源两条路：
  1. 块态自动派生：``self_healed[]`` → ``kind="self_heal"``；等待型块（``nwk.topology``
     / ``nwk.route_status`` / ``nwk.nodes_online`` 这类会打「等了 N 秒，只读探测 K 次」
     的块）→ ``kind="wait"``，等满预算仍不满足 → 另加 ``kind="timeout"``；
  2. 块显式导出：``BlockResult.decisions`` / 返回映射里的 ``decisions``（可选通道）。
- ``interventions[]``：人 / AI 介入记录，**必须恒为空数组**；非空即本次运行不成立，
  需重跑（:class:`CaseRunResult.__post_init__` 因此直接拒绝非空值）。
- ``self_healed[]``：抢占了谁（§3.4 SH-2），作为 ``decisions[]`` 的原始素材保留。

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

import contextvars
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping

from test_automation.models import AssertionResult

from .models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_STATES,
    BLOCK_UNKNOWN,
    OUTCOME_BLOCKED,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    OUTCOME_PRIORITY,
    OUTCOMES,
    OUTCOMES_ASSERTION,
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

#: 块执行态 → 用例 / 方案 **verdict**（四态，§3.5 失败三分类 / D2-4）
#:
#: - ``skipped`` = 台架不具备条件（服务不可达 / 端口占用 / 闸门忙 / 前置事实不满足）
#:   → ``blocked``：产品没被证伪，只是这轮没验成；
#: - ``unknown`` = 说不清（超时无应答 / 解析不出 / 等待超时仍学习）→ ``inconclusive``。
BLOCK_TO_OUTCOME: dict[str, str] = {
    BLOCK_PASS: OUTCOME_PASS,
    BLOCK_FAIL: OUTCOME_FAIL,
    BLOCK_SKIPPED: OUTCOME_BLOCKED,
    BLOCK_UNKNOWN: OUTCOME_INCONCLUSIVE,
}

#: 块执行态 → **断言层**三态（``AssertionResult.outcome``）。
#:
#: **红线（D2-4）**：断言层只认 ``pass|fail|inconclusive``，``blocked`` 只活在
#: 用例 / 方案 verdict 层。所以 ``skipped`` 在这里落 ``inconclusive``（没验成 = 未定论），
#: 与上面那张表**故意不同**——两张表不许合并成一张。
BLOCK_TO_ASSERTION_OUTCOME: dict[str, str] = {
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

#: 块返回值里写 ``outcome`` 时的反查表（``blocked`` 只可能是块态 ``skipped``）
OUTCOME_TO_BLOCK: dict[str, str] = {
    OUTCOME_PASS: BLOCK_PASS,
    OUTCOME_FAIL: BLOCK_FAIL,
    OUTCOME_BLOCKED: BLOCK_SKIPPED,
    OUTCOME_INCONCLUSIVE: BLOCK_UNKNOWN,
}


def aggregate_verdict(verdicts: Iterable[str]) -> str:
    """按优先级 ``fail > blocked > inconclusive > pass`` 聚合多个结论。

    用例级与**方案级**共用这一份口径（D2-4 / §3.5）：调用方不要自己写 if-else 链，
    否则两处优先级迟早漂移。

    - 空列表 / 全是无法识别的值 → ``inconclusive``（未验证，绝不 ``pass``）；
    - 未知取值不参与比较，只影响「一条合法结论都没有」时的兜底。
    """

    seen = {str(item) for item in verdicts}
    for verdict in OUTCOME_PRIORITY:
        if verdict in seen:
            return verdict
    return OUTCOME_INCONCLUSIVE


# --------------------------------------------------------------------------- #
# 自愈留痕（REQS-0042 §3.4 / SH-1..3 / D4-4）
# --------------------------------------------------------------------------- #
#
# 为什么留痕机制在``core``而不是``blocks``
# ----------------------------------------
# 自愈发生在**块内部**（``blocks/common.py`` 的能力层包装），而留痕要挂到
# :class:`BlockRecord` / :class:`CaseRunResult` —— 两者都在本模块，分层铁律
# （``test_layering.py`` 铁律 1）禁止 ``core`` import ``case_runner.blocks``。
# 所以**收集器**放在这里：执行器在某一步外围开一个 sink，块层（``blocks.common``）
# 往 sink 里追加，执行器取回后写进该步的 :class:`BlockRecord`。这样领域块
# **一行都不用改**就能留痕。
#
# 自愈属**自动决策**，不是人/AI 介入（D4-4：``interventions[]`` 必须恒为空数组）；
# ``CaseRunResult.to_dict()`` 里两者分列，不得混装。

#: 自愈留痕的统一字段（每项一条自动决策，见 §3.4 / D4-4）
SELF_HEAL_FIELDS: tuple[str, ...] = ("action", "target", "result", "detail")

#: ``BlockResult`` / ``BlockRecord`` / ``CaseRunResult.to_dict()`` 的键名
SELF_HEALED_KEY = "self_healed"

#: 当前步骤的自愈留痕收集器（``None`` = 不在步骤执行中，留痕静默丢弃）
_SELF_HEAL_SINK: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("case_runner_self_heal_sink", default=None)
)


def self_heal_entry(action: Any, target: Any, result: Any, detail: Any) -> dict[str, Any]:
    """构造一条自愈留痕（字段固定为 :data:`SELF_HEAL_FIELDS` 四项，值一律转字符串）。"""
    return {
        "action": str(action or ""),
        "target": str(target or ""),
        "result": str(result or ""),
        "detail": str(detail or ""),
    }


def normalize_self_heal(entry: Any) -> dict[str, Any]:
    """把任意来源的一条留痕归一（非法项 → ``None`` 由调用方丢弃）。"""
    if not isinstance(entry, Mapping):
        return {}
    return self_heal_entry(
        entry.get("action"), entry.get("target"), entry.get("result"), entry.get("detail")
    )


@contextmanager
def self_heal_sink(
    sink: list[dict[str, Any]] | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """开启一个自愈留痕收集器，``yield`` 出可读的列表。

    嵌套时内层覆盖外层（每个步骤各自一份）；退出时恢复原收集器，
    所以块里打的全是**本步**的留痕，不会串步骤。
    """
    target: list[dict[str, Any]] = sink if sink is not None else []
    token = _SELF_HEAL_SINK.set(target)
    try:
        yield target
    finally:
        _SELF_HEAL_SINK.reset(token)


def record_self_heal(*entries: Any) -> list[dict[str, Any]]:
    """把自愈留痕写进当前收集器，返回归一后的条目列表。

    没有收集器（例如直接单测某个块函数）时**不报错**，只是没有人接这堆留痕 ——
    返回值仍给调用方，便于它自己挂到 ``BlockResult.self_healed``。
    """
    written: list[dict[str, Any]] = []
    for entry in entries:
        item = normalize_self_heal(entry)
        if not item:
            continue
        written.append(item)
    current = _SELF_HEAL_SINK.get()
    if current is not None:
        current.extend(written)
    return written


# --------------------------------------------------------------------------- #
# 自动决策 / 人-AI 介入留痕（REQS-0042 D4-4）
# --------------------------------------------------------------------------- #
#
# 为什么由执行器派生，而不是让每个块自己上报
# ------------------------------------------
# ``decisions[]`` 要的是「本次运行中所有**自动**决策」，凡是标准化的动作（自愈、重试、
# 等待轮询、超时收尾）执行器都能从现有素材里看到，不该逼 13 个块各写一遍 ——
# 漏写一处就少一条证据，而且改动会扩散到 ``blocks/**``（本需求不许动块判定）。
# 所以：
#
# 1. ``self_healed[]``（块层 ``with_self_heal`` 写、执行器收集）→ 每条一条
#    ``kind="self_heal"``；
# 2. 等待型块打的 detail 尾巴「（等了 N 秒…，只读探测 K 次）」或 ``actual`` 里的
#    ``等待秒`` / ``探测次数`` → ``kind="wait"``；等满预算仍不满足 → 另加
#    ``kind="timeout"``（D4-4 的「超时收尾」）；
# 3. 块显式导出（``BlockResult.decisions`` 或返回映射里的 ``decisions``）原样收下，
#    留给将来需要更细语义的块。
#
# **不解析块的判定**：这里只读「等了多久 / 探了几次」这类留痕，块态该是什么还是什么。

DECISION_SELF_HEAL = "self_heal"  # 自愈（抢占端口/重建会话/幂等重试）
DECISION_RETRY = "retry"  # 显式重试（块自己上报）
DECISION_WAIT = "wait"  # 等待轮询（等组网/等路由转抄表）
DECISION_TIMEOUT = "timeout"  # 等待预算耗尽后的收尾判定
DECISION_PREFLIGHT = "preflight"  # 启动前预检（run 级，由调用方上报）

#: ``decisions[].kind`` 的允许取值（建议集合：新增请一并更新这里与文档）
DECISION_KINDS: frozenset[str] = frozenset(
    {
        DECISION_SELF_HEAL,
        DECISION_RETRY,
        DECISION_WAIT,
        DECISION_TIMEOUT,
        DECISION_PREFLIGHT,
    }
)

#: 每条 decision 的必备字段（D4-4：各带理由与次数）
DECISION_FIELDS: tuple[str, ...] = ("kind", "step_id", "block_id", "attempts", "reason")

#: ``CaseRunResult.to_dict()`` 里两个数组的键名
DECISIONS_KEY = "decisions"
INTERVENTIONS_KEY = "interventions"

#: 「等了 N 秒」/「只读探测 K 次」：等待型块（``nwk.topology`` / ``nwk.route_status``
#: / ``nwk.nodes_online`` 等）的 detail 由 ``blocks.nwk.waited_text`` 统一写入。
_WAITED_RE = re.compile(r"等了\s*(\d+(?:\.\d+)?)\s*秒")
_ATTEMPTS_RE = re.compile(r"只读探测\s*(\d+)\s*次")

#: 等待型块 ``actual`` 里的结构化读数（``blocks/nwk.py`` 一直在写这两个键）
ACTUAL_WAITED_KEY = "等待秒"
ACTUAL_ATTEMPTS_KEY = "探测次数"


def decision_entry(
    kind: str,
    *,
    step_id: str = "",
    block_id: str = "",
    attempts: int = 1,
    reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """构造一条自动决策（``kind/step_id/block_id/attempts/reason`` 必备，其余附加）。"""

    entry: dict[str, Any] = {
        "kind": str(kind or ""),
        "step_id": str(step_id or ""),
        "block_id": str(block_id or ""),
        "attempts": int(attempts or 0),
        "reason": str(reason or ""),
    }
    for key, value in extra.items():
        if key in entry:
            raise ValueError(f"decision 附加字段与必备字段同名：{key!r}")
        entry[key] = _jsonable(value)
    return entry


def normalize_decision(entry: Any) -> dict[str, Any]:
    """把任意来源的一条 decision 归一（缺 ``kind`` 的丢弃，返回 ``{}``）。

    必备字段一律补齐（缺 ``attempts`` 记 1），附加字段原样保留 —— 结果要能直接落盘。
    """

    if not isinstance(entry, Mapping):
        return {}
    kind = str(entry.get("kind") or "").strip()
    if not kind:
        return {}
    attempts = entry.get("attempts")
    try:
        attempts_int = int(attempts) if attempts is not None else 1
    except (TypeError, ValueError):
        attempts_int = 1
    normalized: dict[str, Any] = {
        "kind": kind,
        "step_id": str(entry.get("step_id") or ""),
        "block_id": str(entry.get("block_id") or ""),
        "attempts": max(attempts_int, 0),
        "reason": str(entry.get("reason") or entry.get("detail") or ""),
    }
    for key, value in entry.items():
        if key in normalized:
            continue
        normalized[str(key)] = _jsonable(value)
    return normalized


def poll_reading(record: Any) -> tuple[float, int] | None:
    """取一步的轮询读数 ``(等了 N 秒, 探测 K 次)``；不是等待型步骤 → ``None``。

    优先读 ``AssertionResult.actual`` 里的结构化读数，退化再从 detail 解析
    「（等了 N 秒，只读探测 K 次）」。两处都没有 = 本步没轮询过。
    """

    actual = getattr(getattr(record, "assertion", None), "actual", None)
    if isinstance(actual, Mapping):
        waited = _as_float(actual.get(ACTUAL_WAITED_KEY))
        attempts = _as_int(actual.get(ACTUAL_ATTEMPTS_KEY))
        if waited is not None or attempts is not None:
            return (waited or 0.0, attempts or 0)

    detail = str(getattr(record, "detail", "") or "")
    waited_match = _WAITED_RE.search(detail)
    attempts_match = _ATTEMPTS_RE.search(detail)
    if waited_match is None and attempts_match is None:
        return None
    waited = float(waited_match.group(1)) if waited_match else 0.0
    attempts = int(attempts_match.group(1)) if attempts_match else 0
    return (waited, attempts)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _step_ids_of(record: Any) -> tuple[str, str]:
    block_id = str(getattr(record, "block_id", "") or "")
    step_id = str(getattr(record, "step_id", "") or "") or block_id
    return step_id, block_id


def step_decisions(record: Any) -> list[dict[str, Any]]:
    """一步的自动决策：块自己导出的 + 自愈派生 + 等待/超时派生（顺序稳定）。"""

    step_id, block_id = _step_ids_of(record)
    produced: list[dict[str, Any]] = []

    # 1) 块显式导出的（可选通道）
    for item in getattr(record, "decisions", ()) or ():
        normalized = normalize_decision(item)
        if not normalized:
            continue
        normalized["step_id"] = normalized["step_id"] or step_id
        normalized["block_id"] = normalized["block_id"] or block_id
        produced.append(normalized)

    # 2) 自愈留痕 → 一条一条 self_heal（保留 action/target/result/detail 四项）
    for item in getattr(record, "self_healed", ()) or ():
        heal = normalize_self_heal(item)
        if not heal:
            continue
        reason = heal["detail"] or "%s %s → %s" % (
            heal["action"] or "自愈",
            heal["target"],
            heal["result"] or "已完成",
        )
        produced.append(
            decision_entry(
                DECISION_SELF_HEAL,
                step_id=step_id,
                block_id=block_id,
                attempts=1,
                reason=reason,
                **heal,
            )
        )

    # 3) 等待轮询 / 超时收尾（等待型块：detail 或 actual 里有「等了 N 秒 / 探测 K 次」）
    reading = poll_reading(record)
    if reading is not None:
        waited, attempts = reading
        if attempts > 1 or waited > 0:
            state = str(getattr(record, "state", "") or "")
            produced.append(
                decision_entry(
                    DECISION_WAIT,
                    step_id=step_id,
                    block_id=block_id,
                    attempts=attempts or 1,
                    reason="等待型块轮询：等了 %s 秒、只读探测 %d 次"
                    % (_format_seconds(waited), attempts),
                    waited_s=round(waited, 3),
                    state=state,
                )
            )
            if state != BLOCK_PASS and waited > 0:
                produced.append(
                    decision_entry(
                        DECISION_TIMEOUT,
                        step_id=step_id,
                        block_id=block_id,
                        attempts=attempts or 1,
                        reason="等待预算耗尽（等了 %s 秒、探测 %d 次）仍不满足判据，"
                        "按块态 %s 收尾" % (_format_seconds(waited), attempts, state),
                        waited_s=round(waited, 3),
                        state=state,
                    )
                )
    return produced


def _format_seconds(value: float) -> str:
    """秒数人读化（整数不带小数点，与块层 ``waited_text`` 口径一致）。"""

    number = float(value)
    return str(int(number)) if number == int(number) else repr(number)


def auto_decisions(records: Iterable[Any]) -> list[dict[str, Any]]:
    """全 run 的自动决策（按步骤顺序拍平；同一条重复出现时去重）。"""

    merged: list[dict[str, Any]] = []
    for record in records:
        for item in step_decisions(record):
            if item not in merged:
                merged.append(item)
    return merged


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
    #: 自愈留痕（§3.4 / SH-2）：每项 ``{action, target, result, detail}``。
    #: 块可以直接填，也可以把留痕写进 :func:`self_heal_sink` 让执行器自动收集；
    #: 两边都填时执行器合并（按 ``(action, target, result, detail)`` 去重）。
    self_healed: list[dict[str, Any]] = field(default_factory=list)
    #: 块**显式导出**的自动决策（D4-4 可选通道）：每项 ``{kind, attempts, reason, ...}``，
    #: 执行器会补 ``step_id`` / ``block_id`` 后并进 ``CaseRunResult.decisions[]``。
    #: 不填也行——自愈与等待轮询由执行器从 ``self_healed`` / detail 读数自动派生。
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in BLOCK_STATES:
            raise ValueError(
                f"未知块执行态 {self.state!r}，允许：{sorted(BLOCK_STATES)}"
            )
        self.self_healed = [item for item in map(normalize_self_heal, self.self_healed) if item]
        self.decisions = [
            item for item in map(normalize_decision, self.decisions) if item
        ]


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
    #: 本步的自愈留痕（SH-2）：执行器把块写的 + 收集器收到的合并成一份
    self_healed: list[dict[str, Any]] = field(default_factory=list)
    #: 本步的自动决策（D4-4）：块显式导出的 + 执行器从 ``self_healed`` / 轮询读数派生的
    decisions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        """本步的结论层取值（四态：``skipped`` → ``blocked``）。"""

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
            # SH-2：自愈是自动决策，挂在本步（聚合口径见 CaseRunResult.to_dict）
            SELF_HEALED_KEY: [dict(item) for item in self.self_healed],
            # D4-4：本步的自动决策（全 run 聚合见 CaseRunResult.decisions）
            DECISIONS_KEY: [dict(item) for item in step_decisions(self)],
            "assertion_id": self.assertion.assertion_id if self.assertion else "",
            "message": self.assertion.message if self.assertion else self.detail,
        }


@dataclass
class CaseRunResult:
    """一次用例执行结果（``to_dict()`` 为落盘/API 口径）。

    ``verdict`` 是**四态**（``pass|fail|blocked|inconclusive``，D2-4）；``assertions[]``
    仍是断言层三态，两者不许互相污染。
    """

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
    #: 自动决策（D4-4）：调用方可以显式塞（如 run 级 preflight），留空则由
    #: :func:`auto_decisions` 从 ``blocks[]`` 派生（自愈 / 等待 / 超时）。
    decisions: list[dict[str, Any]] = field(default_factory=list)
    #: 人 / AI 介入记录（D4-4）：**必须恒为空**。任何非空 = 本次运行不成立、需重跑，
    #: 所以这里直接拒绝非空值，``to_dict()`` 也永远输出 ``[]``。
    interventions: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in OUTCOMES:
            raise ValueError(
                f"未知 verdict {self.verdict!r}，允许：{sorted(OUTCOMES)}"
            )
        if self.interventions:
            raise ValueError(
                "interventions[] 必须恒为空数组（REQS-0042 D4-4）："
                "它记的是人/AI 介入，出现任何一条即本次运行不成立、需重跑。"
                f"收到 {len(self.interventions)} 条：{self.interventions!r}"
            )
        self.decisions = [
            item for item in map(normalize_decision, self.decisions) if item
        ]

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

    def self_healed_entries(self) -> list[dict[str, Any]]:
        """全 run 的自愈留痕**扁平**列表（SH-2 / D4-4 的证据来源）。

        每项在 ``{action, target, result, detail}`` 之上补 ``step_id`` / ``block_id``，
        便于事后回指「哪一步抢占了谁」。自愈是**自动决策**：它可以进 ``decisions[]``，
        **不得**混进 ``interventions[]``（人/AI 介入，D4-4 要求恒为空数组）。
        """
        merged: list[dict[str, Any]] = []
        for record in self.blocks:
            for item in record.self_healed:
                entry = dict(item)
                entry["step_id"] = record.step_id or record.block_id
                entry["block_id"] = record.block_id
                merged.append(entry)
        return merged

    def resolved_decisions(self) -> list[dict[str, Any]]:
        """本次运行的全部**自动**决策（D4-4 的 ``decisions[]`` 口径）。

        = 调用方显式给的（``decisions=``，如 run 级 preflight）
        + 从 ``blocks[]`` 派生的（自愈 / 等待轮询 / 超时收尾，见 :func:`step_decisions`）。

        两条来源都归一过，每条至少带 ``{kind, step_id, block_id, attempts, reason}``，
        可直接 JSON 落盘。``interventions[]`` **不在这里**：它是人/AI 介入，恒为空。
        """

        merged: list[dict[str, Any]] = [
            item for item in map(normalize_decision, self.decisions) if item
        ]
        for item in auto_decisions(self.blocks):
            if item not in merged:
                merged.append(item)
        return merged

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
            # SH-2：全 run 自愈留痕（扁平，含 step_id/block_id）；
            # 自愈属自动决策，不写进 interventions[]（D4-4：那里必须恒为空）
            SELF_HEALED_KEY: self.self_healed_entries(),
            # D4-4：自动决策（自愈 / 重试 / 等待 / 超时 / 预检），每条带理由与次数
            DECISIONS_KEY: self.resolved_decisions(),
            # D4-4：人/AI 介入记录**恒为空数组**——这里写死 ``[]``，连传进来的值都不认，
            # 因为任何非空即「本 run 不成立，需重跑」（构造期已拒绝非空，见 __post_init__）
            INTERVENTIONS_KEY: [],
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
    """块态 → **断言层**三态 ``AssertionResult``（``skipped``/``unknown`` → inconclusive）。"""

    return AssertionResult(
        run_id=run_id,
        assertion_id=assertion_id,
        outcome=BLOCK_TO_ASSERTION_OUTCOME.get(state, OUTCOME_INCONCLUSIVE),
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


def _merge_self_heal(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """合并多组自愈留痕并按四项字段去重（块自己写的在前，收集器收到的不重复）。"""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for item in group or ():
            entry = normalize_self_heal(item)
            if not entry:
                continue
            key = tuple(entry[field_] for field_ in SELF_HEAL_FIELDS)  # type: ignore[misc]
            if key in seen:
                continue
            seen.add(key)  # type: ignore[arg-type]
            merged.append(entry)
    return merged


def _merge_decisions(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """合并多组块显式导出的决策并去重（缺 ``kind`` 的丢弃）。"""
    merged: list[dict[str, Any]] = []
    for group in groups:
        for item in group or ():
            entry = normalize_decision(item)
            if not entry:
                continue
            if entry not in merged:
                merged.append(entry)
    return merged


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
    self_healed: Iterable[Mapping[str, Any]] | None = None,
    decisions: Iterable[Mapping[str, Any]] | None = None,
) -> BlockRecord:
    assertion = assertion or _make_assertion(
        run_id, assertion_id or block_id, state, detail
    )
    record = BlockRecord(
        block_id=block_id,
        state=state,
        detail=detail,
        facts=dict(facts or {}),
        assertion=assertion,
        runnable=runnable,
        step_id=step_id or block_id,
        on_fail=on_fail,
        params={key: _jsonable(value) for key, value in (params or {}).items()},
        self_healed=_merge_self_heal(self_healed or ()),
        decisions=_merge_decisions(decisions or ()),
    )
    return record


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

    # 本步的自愈留痕收集器（§3.4 / SH-2）：块层（``blocks.common.with_self_heal``）
    # 往这里追加，块本身不用改一行就能把「抢占了谁」带进结果。
    heal_sink: list[dict[str, Any]] = []
    try:
        with self_heal_sink(heal_sink):
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
            # 异常前已经发生的抢占也要留痕（事后追「我们动过谁」靠它）
            self_healed=heal_sink,
        )

    coerced = coerce_block_return(raw)
    # 块也可以直接在返回的映射里带 ``self_healed``（例如把 ``call_verify`` 的整个结果返回）
    declared_heal: list[Any] = list(coerced.self_healed)
    if isinstance(raw, Mapping) and isinstance(raw.get(SELF_HEALED_KEY), (list, tuple)):
        declared_heal.extend(raw[SELF_HEALED_KEY])
    # 块显式导出的决策（D4-4 可选通道）：BlockResult.decisions 或返回映射里的 decisions
    declared_decisions: list[Any] = list(coerced.decisions)
    if isinstance(raw, Mapping) and isinstance(raw.get(DECISIONS_KEY), (list, tuple)):
        declared_decisions.extend(raw[DECISIONS_KEY])
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
        # 块自己写的（BlockResult.self_healed / 返回映射里的 self_healed）
        # + 收集器收到的（with_self_heal 走的全局通道）
        self_healed=_merge_self_heal(declared_heal, heal_sink),
        decisions=_merge_decisions(declared_decisions),
    )


def _verdict(records: list[BlockRecord]) -> str:
    """用例结论（四态）：按 ``fail > blocked > inconclusive > pass`` 取最严重者。

    - 只有**所有步骤都 pass** 才是 ``pass``；
    - 有 ``fail`` → ``fail``（真失败优先于台架问题）；
    - 有 ``skipped`` → ``blocked``（台架不具备条件，产品没被证伪）；
    - 有 ``unknown`` → ``inconclusive``（说不清）。
    """

    if not records:
        return OUTCOME_INCONCLUSIVE
    return aggregate_verdict(record.outcome for record in records)


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
        if outcome in OUTCOMES_ASSERTION:
            # 断言层三态：直接采用（这里**不许**出现 blocked，见 D2-4 红线）
            return AssertionResult(
                run_id=str(raw.get("run_id") or run_id),
                assertion_id=str(raw.get("assertion_id") or assertion_id),
                outcome=outcome,
                expected=raw.get("expected"),
                actual=raw.get("actual"),
                evidence_ids=list(raw.get("evidence_ids") or []),
                message=str(raw.get("message") or fallback_message),
            )
        if outcome in OUTCOMES:
            # 求值器回了结论层四态里的 ``blocked``：断言层不认，折算回 inconclusive
            # （断言层永远三值；blocked 只活在用例/方案 verdict 层）
            return _make_assertion(
                run_id,
                assertion_id,
                OUTCOME_TO_BLOCK[outcome],
                f"{fallback_message}：断言层只认 pass|fail|inconclusive，"
                f"``{outcome}`` 折算为未验证（D2-4）",
                actual=raw.get("actual"),
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
    """用例级断言并入 verdict（按同一份优先级 ``fail > blocked > inconclusive > pass``）。

    - 断言 ``fail`` → ``fail``（有明确失败即最严重）；
    - 断言全 ``pass`` → 沿用块级 verdict（块态说了算）；
    - 断言未求值 / inconclusive → 与块级 verdict 一起按优先级合并：
      ``pass`` 必须降级成 ``inconclusive``（绝不假 PASS），``blocked`` 仍压过
      ``inconclusive``（台架问题不该被「断言没求值」盖成说不清）。
    """

    if not case_assertions:
        return verdict
    outcomes = [a.outcome for a in case_assertions]
    if OUTCOME_FAIL in outcomes:
        return OUTCOME_FAIL
    if all(outcome == OUTCOME_PASS for outcome in outcomes):
        return verdict
    return aggregate_verdict([verdict, OUTCOME_INCONCLUSIVE])


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
    "BLOCK_TO_ASSERTION_OUTCOME",
    "BLOCK_TO_OUTCOME",
    "DECISIONS_KEY",
    "DECISION_FIELDS",
    "DECISION_KINDS",
    "DECISION_PREFLIGHT",
    "DECISION_RETRY",
    "DECISION_SELF_HEAL",
    "DECISION_TIMEOUT",
    "DECISION_WAIT",
    "INTERVENTIONS_KEY",
    "OUTCOME_TO_BLOCK",
    "SELF_HEALED_KEY",
    "SELF_HEAL_FIELDS",
    "BlockResult",
    "BlockRecord",
    "CaseRunResult",
    "RunContext",
    "aggregate_verdict",
    "auto_decisions",
    "coerce_block_return",
    "decision_entry",
    "execute_case",
    "normalize_decision",
    "normalize_self_heal",
    "poll_reading",
    "record_self_heal",
    "self_heal_entry",
    "self_heal_sink",
    "step_decisions",
]
