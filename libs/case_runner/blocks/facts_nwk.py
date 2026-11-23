"""组网/档案相关事实探测体（REQS-0041 §10.5 FT-1/FT-2/FT-3）。

四个事实

=========================  ==================================================
``archive.contains``       探测 10H-F2 档案表是否含 ``addr``
``nwk.onnet_count_min``    **档案数**是否 ≥ ``min``（10H-F1；id 为兼容旧名保留，
                           语义一直是档案数：10H-F1 只回档案条数）
``nwk.nodes_online_min``   **真实在网/可达**节点数是否 ≥ ``min``（10H-F4 已抄数 +
                           10H-F2 逐节点信号品质两路交叉）
``nwk.route_running``      探测 10H-F4 路由是否处于抄表运行态（学习态按路由完成标志分层）
=========================  ==================================================

FT-2（判定逻辑只有一份）
------------------------
四个探测体**不自己判定**，一律调用 ``blocks/common.py`` 里那份块层判定函数：

- ``archive.contains``     → ``archive_snapshot`` + ``judge_contains``
- ``nwk.onnet_count_min``  → ``archive_count_snapshot`` + ``judge_archive_count_min``
- ``nwk.nodes_online_min`` → ``nodes_online_snapshot`` + ``judge_nodes_online``
- ``nwk.route_running``    → ``route_snapshot`` + ``judge_route_running``

**本文件不 import 任何兄弟块模块**（``archive`` / ``nwk`` 也不 import 它）：公共件只有
``common.py`` 一份，事实与块拿到的是**同一个函数对象**（不是「看起来一样」的两份实现）。

FT-3（三态，unknown 永不折算）
------------------------------
探测不到（台架不在线、job 超时、字段缺失、窗口未覆盖）**一律** ``unknown``；
只有拿到可判定应答且比对成功才 ``yes``，比对失败才 ``no``。

事实探测体签名按内核契约 ``fn(params) -> FactValue | "yes"|"no"|"unknown" | dict``，
**没有** ``ctx`` 入参，因此能力句柄由 :func:`simcon_handle` 统一提供
（读 ``WORKBENCH_BASE`` 自建并 probe；单测 monkeypatch 本函数注入替身）。
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from case_runner.blocks.common import (
    DEFAULT_MAX_PAGES,
    DEFAULT_MIN_ONLINE,
    DEFAULT_NODES_ONLINE_COUNT,
    DEFAULT_QUERY_COUNT,
    DEFAULT_TIMEOUT,
    archive_count_snapshot,
    archive_snapshot,
    capability_gate,
    int_of,
    judge_archive_count_min,
    judge_contains,
    judge_nodes_online,
    judge_onnet_min,
    judge_route_running,
    nodes_online_snapshot,
    normalize_addr,
    onnet_snapshot,
    resolve_simcon,
    route_snapshot,
)
from case_runner.core.models import FACT_NO, FACT_UNKNOWN, FACT_YES
from case_runner.core.planner import FactValue
from case_runner.core.registry import (
    FACT_REGISTRY,
    Duplicate,
    register_fact,
    unregister_fact,
)

#: 事实 id（与 ``data/facts/*.json`` 一一对应）
FACT_CONTAINS = "archive.contains"
#: 兼容别名事实：判的是**档案数**（10H-F1 只给档案条数，给不出在网数）
FACT_ONNET_COUNT_MIN = "nwk.onnet_count_min"
#: 真在网事实（两路证据交叉，参数 ``min``）
FACT_NODES_ONLINE_MIN = "nwk.nodes_online_min"
FACT_ROUTE_RUNNING = "nwk.route_running"


def simcon_handle() -> Tuple[Any, bool]:
    """事实探测用的能力句柄：``(capability, injected)``。

    默认按 ``WORKBENCH_BASE`` 自建（``injected=False`` → 探测前先 probe）；
    单测 monkeypatch 本函数返回 ``(fake, True)`` 即可脱离台架。
    """
    return resolve_simcon(None)


def _params_of(params: Any) -> Mapping[str, Any]:
    return params if isinstance(params, Mapping) else {}


def _unknown(detail: str, evidence: Any = ()) -> FactValue:
    return FactValue(state=FACT_UNKNOWN, detail=detail, evidence=list(evidence or ()))


# --------------------------------------------------------------------------- #
# archive.contains
# --------------------------------------------------------------------------- #


def probe_archive_contains(params: Any) -> FactValue:
    """10H-F2 档案表是否含 ``params["addr"]``。"""
    action = _params_of(params)
    addr = normalize_addr(action.get("addr"))
    if not addr:
        return _unknown("archive.contains 缺少/非法参数 addr（12 位 BCD 表地址）")

    cap, injected = simcon_handle()
    gate = capability_gate(cap, injected)
    if gate is not None:
        return _unknown("archive.contains 无法探测：%s" % gate.detail)

    snapshot = archive_snapshot(
        cap=cap,
        start=action.get("start", 0),
        count=action.get("count", DEFAULT_QUERY_COUNT),
        max_pages=action.get("max_pages", DEFAULT_MAX_PAGES),
        expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT),
    )
    state, detail = judge_contains(snapshot, addr)
    if state not in (FACT_YES, FACT_NO):
        state = FACT_UNKNOWN
    return FactValue(state=state, detail=detail, evidence=list(snapshot.get("job_ids") or []))


# --------------------------------------------------------------------------- #
# nwk.onnet_count_min（= 档案数，兼容别名）/ nwk.nodes_online_min（真在网）
# --------------------------------------------------------------------------- #


def probe_archive_count_min(params: Any) -> FactValue:
    """10H-F1「从节点总数量」（= 路由**档案**条数）是否 ≥ ``params["min"]``。

    注意：这不是在网数。真实在网见 :func:`probe_nodes_online_min`。
    """
    action = _params_of(params)
    minimum = int_of(action.get("min"))
    if minimum is None or minimum < 0:
        return _unknown("nwk.onnet_count_min 缺少/非法参数 min（需 ≥0 的整数）")

    cap, injected = simcon_handle()
    gate = capability_gate(cap, injected)
    if gate is not None:
        return _unknown("nwk.onnet_count_min 无法探测：%s" % gate.detail)

    snapshot = archive_count_snapshot(
        cap=cap, expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT)
    )
    state, detail = judge_archive_count_min(snapshot, minimum)
    if state not in (FACT_YES, FACT_NO):
        state = FACT_UNKNOWN
    return FactValue(state=state, detail=detail, evidence=list(snapshot.get("job_ids") or []))


#: 兼容别名：旧探测体名字（判的还是档案数）
probe_onnet_count_min = probe_archive_count_min


def probe_nodes_online_min(params: Any) -> FactValue:
    """真实在网/可达节点数是否 ≥ ``params["min"]``（默认 1）。

    两路证据：10H-F4「已抄从节点数量」+ 10H-F2 逐节点「信号品质>0」的节点数；
    两路都为 0 → ``no``；两路都取不到 → ``unknown``；**绝不**用档案数推断。
    """
    action = _params_of(params)
    raw_min = action.get("min")
    minimum = int_of(DEFAULT_MIN_ONLINE if raw_min in (None, "") else raw_min)
    if minimum is None or minimum < 0:
        return _unknown("nwk.nodes_online_min 缺少/非法参数 min（需 ≥0 的整数）")

    cap, injected = simcon_handle()
    gate = capability_gate(cap, injected)
    if gate is not None:
        return _unknown("nwk.nodes_online_min 无法探测：%s" % gate.detail)

    snapshot = nodes_online_snapshot(
        cap=cap,
        count=action.get("count", DEFAULT_NODES_ONLINE_COUNT),
        max_pages=action.get("max_pages", DEFAULT_MAX_PAGES),
        expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT),
    )
    state, detail = judge_nodes_online(snapshot, minimum)
    if state not in (FACT_YES, FACT_NO):
        state = FACT_UNKNOWN
    return FactValue(state=state, detail=detail, evidence=list(snapshot.get("job_ids") or []))


# --------------------------------------------------------------------------- #
# nwk.route_running
# --------------------------------------------------------------------------- #


def probe_route_running(params: Any) -> FactValue:
    """10H-F4「工作状态」是否为抄表态（= 路由在运行）。"""
    action = _params_of(params)
    cap, injected = simcon_handle()
    gate = capability_gate(cap, injected)
    if gate is not None:
        return _unknown("nwk.route_running 无法探测：%s" % gate.detail)

    snapshot = route_snapshot(cap=cap, expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT))
    state, detail = judge_route_running(snapshot)
    if state not in (FACT_YES, FACT_NO):
        state = FACT_UNKNOWN
    return FactValue(state=state, detail=detail, evidence=list(snapshot.get("job_ids") or []))


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

_FACTS = {
    FACT_CONTAINS: probe_archive_contains,
    FACT_ONNET_COUNT_MIN: probe_archive_count_min,   # 兼容别名事实（档案数）
    FACT_NODES_ONLINE_MIN: probe_nodes_online_min,   # 真在网
    FACT_ROUTE_RUNNING: probe_route_running,
}


def register_facts(*, replace: bool = False) -> None:
    """注册本模块的事实探测体（幂等；同 id 被别的探测体占用则抛 ``Duplicate``）。"""
    for fact_id, fn in _FACTS.items():
        existing = FACT_REGISTRY.get(fact_id)
        if existing is fn:
            continue
        if existing is not None:
            if not replace:
                raise Duplicate(
                    "事实 %r 已被其它探测体注册（如需接管请 replace=True）" % fact_id
                )
            unregister_fact(fact_id)
        register_fact(fact_id, fn)


register_facts()


def register() -> None:
    """兼容 ``case_runner.blocks.loader.register_domain_modules`` 的约定入口。"""
    register_facts()


__all__ = [
    "FACT_CONTAINS",
    "FACT_ONNET_COUNT_MIN",
    "FACT_NODES_ONLINE_MIN",
    "FACT_ROUTE_RUNNING",
    "simcon_handle",
    "probe_archive_contains",
    "probe_archive_count_min",
    "probe_onnet_count_min",     # 兼容别名
    "probe_nodes_online_min",
    "probe_route_running",
    "register_facts",
    "register",
    # 再导出：事实与块共用同一对象（FT-2），便于测试做 identity 断言
    "archive_snapshot",
    "judge_contains",
    "archive_count_snapshot",
    "judge_archive_count_min",
    "onnet_snapshot",            # 兼容别名
    "judge_onnet_min",           # 兼容别名
    "nodes_online_snapshot",
    "judge_nodes_online",
    "route_snapshot",
    "judge_route_running",
]
