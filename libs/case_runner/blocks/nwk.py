"""组网类原子块：``nwk.route_restart`` / ``nwk.active_registration`` /
``nwk.archive_count``（兼容别名 ``nwk.onnet_count``）/ ``nwk.nodes_online`` /
``nwk.route_status``（REQS-0041 §10.5 BL-3）。

判据
----
==========================  ==================================================
``nwk.route_restart``       12H-F1（无参）→ 00H-F1 确认
``nwk.active_registration`` 11H-F5 → 00H-F1 确认；**必填四参**
                            ``time6`` + ``duration`` + ``retry`` + ``slices``，
                            缺参在**下发前**就 fail 并点名缺哪个（REQS §10.7 事实 I：
                            ``encode_app_data(0x11, 5, {})`` 会抛「缺少参数 duration」）
``nwk.archive_count``       10H-F1（无参）→ 上行「从节点总数量」= **路由档案数** ≥ ``min``
``nwk.nodes_online``        **真实可达（在网）节点数**：10H-F4「已抄从节点数量」与
                            10H-F2 逐节点「信号品质>0」两路交叉，> 0 才可能 pass
``nwk.route_status``        10H-F4 → 「工作状态/当前状态」+「运行状态字」路由完成标志分层
==========================  ==================================================

历史缺陷（真实台架已复现）
--------------------------
``nwk.onnet_count`` 曾把 10H-F1 的「从节点总数量」当成可达节点数：台架实测
``从节点总数量=12 / 已抄从节点数量=0 / 12 个节点信号品质全 0``，旧块判
「12 ≥ 期望 1 → 组网通 → PASS」= **假 PASS**。元数据
（``adapter_10376/metadata/afn_fn.json`` 10H-F1 ``resp.fields[0].d``）明确写的是
「当前路由档案中的从节点总数」——**档案数**，不是在线数。因此：

- 旧块正名为 ``nwk.archive_count``（判据 = 档案数 ≥ ``min``，detail 里写明
  「这是路由档案中的从节点总数，不等于真实可达的节点数」）；
- 新块 ``nwk.nodes_online`` 用 10H-F4「已抄数」+ 10H-F2「信号品质」两路证据判真实可达；
- ``nwk.onnet_count`` 只作兼容别名保留（内部委托 ``nwk.archive_count``）。

组网与档案的事实关系（不在代码里写死顺序）
-----------------------------------------
**组网前应有档案**：CCO 只给"在档案里"的从节点分配网内地址，档案数是入网数的上限。
块层因此**不**强制 ``needs: ["archive.contains"]``——顺序由用例编排
（``CaseSpec.blocks``）决定；本文件只在文档与 detail 里提示这一事实关系。

依赖方向（REQS §10.5 解耦要求）
-----------------------------
本文件**不 import 任何兄弟块模块**：通用 1376.2 单步/取帧/判定小工具、10H-F1/F4 组网
快照与判定都在 ``blocks/common.py``（**只有一份**，事实探测体 ``facts_nwk.py`` 也从
``common`` 取同一对象，REQS FT-2）。历史问题：这些件曾经长在 ``archive.py`` 里，导致
组网块曾必须从 ``blocks/archive.py`` 里取这些工具，档案与组网互相牵连。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from case_runner.blocks.common import (  # noqa: F401 - 下面一并对旧调用方再导出
    AFN_CONFIRM,
    AFN_ROUTE_QUERY,
    ARCHIVE_COUNT_NOTE,
    DEFAULT_MIN_ONLINE,
    DEFAULT_NODES_ONLINE_COUNT,
    DEFAULT_TIMEOUT,
    EVIDENCE_ARCHIVED,
    EVIDENCE_QUALITY,
    FN_CONFIRM,
    FN_ONNET_COUNT,
    FN_ROUTE_STATUS,
    ITEM_ANSWERED_TOTAL,
    ITEM_CURRENT_STATE,
    ITEM_RELAY_ANSWERED_TOTAL,
    ITEM_RUN_WORD,
    ITEM_SIGNAL_QUALITY,
    ITEM_SWITCH,
    ITEM_TOTAL,
    ITEM_WORK_STATE,
    ROUTE_LEARNING_INCOMPLETE,
    ROUTE_LEARNING_INCONCLUSIVE,
    ROUTE_RUNNING,
    ROUTE_UNKNOWN,
    WORK_LEARNING,
    WORK_RUNNING,
    archive_count_snapshot,
    block_action_params,
    call_verify,
    capability_gate,
    classify_route_state,
    flag_of,
    int_of,
    item_value,
    items_of,
    judge_archive_count_min,
    judge_confirm_step,
    judge_nodes_online,
    judge_onnet_min,
    judge_route_running,
    judge_route_status,
    judge_step,
    nodes_online_snapshot,
    onnet_snapshot,
    require_target,
    resolve_simcon,
    route_snapshot,
    route_status_fields,
    send_step,
    snapshot_kind,
    steps_of,
    verify_failure_state,
)
from case_runner.core.executor import BlockResult
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_UNKNOWN,
    FACT_NO,
    FACT_UNKNOWN,
    FACT_YES,
)
from case_runner.core.registry import (
    BLOCK_REGISTRY,
    Duplicate,
    register_block,
    unregister_block,
)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

BLOCK_ROUTE_RESTART = "nwk.route_restart"
BLOCK_ACTIVE_REGISTRATION = "nwk.active_registration"
BLOCK_ARCHIVE_COUNT = "nwk.archive_count"
#: **兼容别名**：旧 id 内部委托 ``nwk.archive_count``（判的一直是档案数，历史命名错误）
BLOCK_ONNET_COUNT = "nwk.onnet_count"
BLOCK_NODES_ONLINE = "nwk.nodes_online"
BLOCK_ROUTE_STATUS = "nwk.route_status"

FACT_ONNET_COUNT_MIN = "nwk.onnet_count_min"     # 兼容别名事实（判档案数）
FACT_NODES_ONLINE_MIN = "nwk.nodes_online_min"   # 真在网事实（参数 min）
FACT_ROUTE_RUNNING = "nwk.route_running"

AFN_ROUTE_CONTROL = 0x12        # 12H：路由控制
FN_ROUTE_RESTART = 1            # 12H-F1 启动路由（重新组网）
AFN_NWK_SET = 0x11              # 11H：路由设置
FN_ACTIVE_REGISTRATION = 5      # 11H-F5 激活从节点主动注册

#: 协议里「工作状态 / 当前状态」的合法取值（文档用；判定实现见 common）
WORK_STATES = (WORK_RUNNING, WORK_LEARNING)
CURRENT_STATES = ("抄表", "搜表", "升级", "其他")
#: 默认「期望档案数下限」（``nwk.archive_count`` 的 ``min`` 语义）
DEFAULT_MIN_ARCHIVE = 1
#: 兼容别名：旧名字指的也是档案数
DEFAULT_MIN_ONNET = DEFAULT_MIN_ARCHIVE
#: ``nwk.route_status`` 的分类标签（= ``common.classify_route_state`` 的取值集合）
ROUTE_LABELS = (
    ROUTE_RUNNING,
    ROUTE_LEARNING_INCONCLUSIVE,
    ROUTE_LEARNING_INCOMPLETE,
    ROUTE_UNKNOWN,
)

#: 11H-F5 构帧四参（REQS §10.7 事实 I）
REGISTRATION_TIME_KEYS = ("time6", "time", "sec")
REGISTRATION_VALUE_KEYS = ("duration", "retry", "slices")
REGISTRATION_VALUE_RANGE = {"duration": (0, 0xFFFF), "retry": (0, 0xFF), "slices": (0, 0xFF)}
_TIME_PARTS = ("sec", "min", "hour", "day", "mon", "year")


# --------------------------------------------------------------------------- #
# 11H-F5 四参处理
# --------------------------------------------------------------------------- #


def time6_to_digits(value: Any) -> Tuple[Optional[str], str]:
    """6 字节 BCD 时间 hex（``"000000000126"``）→ 构帧器的 ``"SSMMHHDDMMYY"`` 数字串。

    BCD 每字节高/低半字节都必须 ≤ 9，否则不是合法 BCD（A~F 会变成非法十进制位）。
    """
    text = "".join(str(value).split())
    if text[:2].lower() == "0x":
        text = text[2:]
    if len(text) != 12:
        return None, "time6 必须是 6 字节 BCD 时间的 12 位 hex（如 000000000126），收到 %r" % (
            value,
        )
    digits: List[str] = []
    for index in range(0, 12, 2):
        pair = text[index:index + 2]
        if pair[0].upper() in "ABCDEF" or pair[1].upper() in "ABCDEF":
            return None, "time6 含非 BCD 半字节（%r），BCD 每位必须 0~9：%r" % (pair, value)
        digits.append(pair)
    return "".join(digits), ""


def registration_time_params(action: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """把三种时间写法归一成构帧器要的 ``{"time": ...}`` 或分项字典。"""
    time6 = action.get("time6")
    if time6 not in (None, ""):
        digits, error = time6_to_digits(time6)
        if error:
            return None, error
        return {"time": digits}, ""
    plain = action.get("time")
    if plain not in (None, ""):
        text = "".join(str(plain).split()).replace("-", "").replace(":", "")
        if len(text) != 12 or not text.isdigit():
            return None, (
                "参数 time 须为 12 位数字（秒分时日月年，如 \"000000000126\"）：%r" % (plain,)
            )
        return {"time": text}, ""
    if any(key in action for key in _TIME_PARTS):
        payload: Dict[str, Any] = {}
        for key in _TIME_PARTS:
            value = int_of(action.get(key, 0))
            if value is None:
                return None, "参数 %s 不是整数：%r" % (key, action.get(key))
            payload[key] = value
        return payload, ""
    return None, (
        "缺少必填参数 time6（6 字节 BCD 时间 hex，如 000000000126；"
        "也接受 time=\"SSMMHHDDMMYY\" 或 sec/min/hour/day/mon/year 分项）"
    )


def registration_payload(action: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """11H-F5 全参构造：四参齐给才放行，缺哪个点名哪个（下发前失败）。"""
    problems: List[str] = []
    time_payload, time_error = registration_time_params(action)
    if time_error:
        problems.append(time_error)

    missing = [key for key in REGISTRATION_VALUE_KEYS if action.get(key) in (None, "")]
    if missing:
        problems.append("缺少必填参数 %s" % "/".join(missing))

    values: Dict[str, int] = {}
    if not missing:
        for key in REGISTRATION_VALUE_KEYS:
            number = int_of(action.get(key))
            low, high = REGISTRATION_VALUE_RANGE[key]
            if number is None:
                problems.append("参数 %s 不是整数：%r" % (key, action.get(key)))
            elif not low <= number <= high:
                problems.append("参数 %s 越界 [%d,%d]：%r" % (key, low, high, action.get(key)))
            else:
                values[key] = number

    if problems:
        return None, (
            "%s 参数不全/非法：%s（11H-F5 构帧必须四参齐给："
            "time6 + duration + retry + slices）" % (BLOCK_ACTIVE_REGISTRATION, "；".join(problems))
        )
    payload = dict(time_payload or {})
    payload.update(values)
    return payload, ""


# --------------------------------------------------------------------------- #
# 块执行体
# --------------------------------------------------------------------------- #


def block_route_restart(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.route_restart``：12H-F1 启动路由（重新组网），判据 = 00H-F1 确认。"""
    target, error = require_target(params, AFN_ROUTE_CONTROL, FN_ROUTE_RESTART, BLOCK_ROUTE_RESTART)
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    result = call_verify(
        cap,
        [
            send_step(
                "启动路由（重新组网）",
                AFN_ROUTE_CONTROL,
                FN_ROUTE_RESTART,
                {},
                expect_afn=AFN_CONFIRM,
                expect_fn=FN_CONFIRM,
                timeout=action.get("confirm_timeout", DEFAULT_TIMEOUT),
            )
        ],
    )
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return BlockResult(
            state=state,
            detail="%s：路由启动帧未得到可判定结论（%s）" % (BLOCK_ROUTE_RESTART, why),
            evidence_ids=job_ids,
        )
    verdict = judge_confirm_step(
        got_steps[-1], label="12H-F1 启动路由确认", cap=cap, job=result
    )
    if verdict["kind"] != "answered":
        return BlockResult(
            state=verdict["state"],
            detail="%s：%s" % (BLOCK_ROUTE_RESTART, verdict["detail"]),
            evidence_ids=job_ids,
            expected="00H-F1 确认帧",
            actual=verdict["kind"],
        )
    return BlockResult(
        state=BLOCK_PASS,
        detail="%s：%s（组网是否真起来由 nwk.nodes_online（真在网）/ nwk.route_status 再判；"
        "档案数由 nwk.archive_count 判，不代表可达状态）"
        % (BLOCK_ROUTE_RESTART, verdict["detail"]),
        evidence_ids=job_ids,
    )


def block_active_registration(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.active_registration``：11H-F5 激活从节点主动注册，判据 = 00H-F1 确认。

    四参（time6 + duration + retry + slices）在**下发前**校验，缺哪个在 detail 点名。
    """
    target, error = require_target(
        params, AFN_NWK_SET, FN_ACTIVE_REGISTRATION, BLOCK_ACTIVE_REGISTRATION
    )
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    payload, error = registration_payload(action)
    if payload is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    result = call_verify(
        cap,
        [
            send_step(
                "激活从节点主动注册（time6=%s duration=%s retry=%s slices=%s）"
                % (
                    action.get("time6") or action.get("time"),
                    payload.get("duration"),
                    payload.get("retry"),
                    payload.get("slices"),
                ),
                AFN_NWK_SET,
                FN_ACTIVE_REGISTRATION,
                payload,
                expect_afn=AFN_CONFIRM,
                expect_fn=FN_CONFIRM,
                timeout=action.get("confirm_timeout", DEFAULT_TIMEOUT),
            )
        ],
    )
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return BlockResult(
            state=state,
            detail="%s：主动注册帧未得到可判定结论（%s）"
            % (BLOCK_ACTIVE_REGISTRATION, why),
            evidence_ids=job_ids,
        )
    verdict = judge_confirm_step(
        got_steps[-1], label="11H-F5 主动注册确认", cap=cap, job=result
    )
    if verdict["kind"] != "answered":
        return BlockResult(
            state=verdict["state"],
            detail="%s：%s" % (BLOCK_ACTIVE_REGISTRATION, verdict["detail"]),
            evidence_ids=job_ids,
            expected="00H-F1 确认帧",
            actual=verdict["kind"],
        )
    return BlockResult(
        state=BLOCK_PASS,
        detail="%s：%s（注册窗口 duration=%s retry=%s slices=%s，"
        "入网结果由 nwk.nodes_online（真在网）/ nwk.route_status 再判）"
        % (
            BLOCK_ACTIVE_REGISTRATION,
            verdict["detail"],
            payload.get("duration"),
            payload.get("retry"),
            payload.get("slices"),
        ),
        evidence_ids=job_ids,
    )


def block_archive_count(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.archive_count``：10H-F1 查**路由档案中的从节点数量**，判据 = 档案数 ≥ ``min``。

    **这不是可达节点数**：10H-F1 只回「从节点总数量」（元数据原文「当前路由档案中的
    从节点总数」）+「路由支持最大从节点数量」，没有任何"抄通/可达"信息。判真实可达
    请用 :func:`block_nodes_online`。``min`` 语义 = **期望档案数下限**。
    """
    target, error = require_target(
        params, AFN_ROUTE_QUERY, FN_ONNET_COUNT, BLOCK_ARCHIVE_COUNT
    )
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    if action.get("min") in (None, ""):
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s 缺少必填参数 min（期望的**档案数**下限，如 1；%s）"
            % (BLOCK_ARCHIVE_COUNT, ARCHIVE_COUNT_NOTE),
        )
    minimum = int_of(action.get("min"))
    if minimum is None or minimum < 0:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s 参数 min 必须 ≥0 的整数，收到 %r" % (BLOCK_ARCHIVE_COUNT, action.get("min")),
        )

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    snapshot = archive_count_snapshot(
        cap=cap, expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT)
    )
    job_ids = list(snapshot.get("job_ids") or [])
    if snapshot.get("state") != "answered":
        return BlockResult(
            state=str(snapshot.get("block_state") or BLOCK_UNKNOWN),
            detail="%s：%s"
            % (BLOCK_ARCHIVE_COUNT, snapshot.get("detail") or "查询未得到可判定应答"),
            evidence_ids=job_ids,
        )
    state, detail = judge_archive_count_min(snapshot, minimum)
    if state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="%s：%s" % (BLOCK_ARCHIVE_COUNT, detail),
            evidence_ids=job_ids,
            expected="档案数 ≥ %d" % minimum,
            actual=snapshot.get("total"),
        )
    if state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：%s（组网前应先建档；档案数达标不代表节点真实可达，"
            "可达性请看 %s）" % (BLOCK_ARCHIVE_COUNT, detail, BLOCK_NODES_ONLINE),
            evidence_ids=job_ids,
            expected="档案数 ≥ %d" % minimum,
            actual=snapshot.get("total"),
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：%s" % (BLOCK_ARCHIVE_COUNT, detail),
        evidence_ids=job_ids,
    )


def block_onnet_count(params: Any, ctx: Any = None) -> BlockResult:
    """**兼容别名** ``nwk.onnet_count`` → 内部委托 :func:`block_archive_count`。

    旧 id 从未判过可达节点数（10H-F1 也给不出可达性），保留只为不打断已有用例/方案；
    detail 会标明这是兼容别名，判据仍是**档案数**。
    """
    result = block_archive_count(params, ctx)
    detail = result.detail.replace(BLOCK_ARCHIVE_COUNT, BLOCK_ONNET_COUNT, 1)
    return BlockResult(
        state=result.state,
        detail="%s（兼容别名 %s → %s：判据是档案数，真实可达节点数见 %s）"
        % (detail, BLOCK_ONNET_COUNT, BLOCK_ARCHIVE_COUNT, BLOCK_NODES_ONLINE),
        facts=dict(result.facts),
        evidence_ids=list(result.evidence_ids),
        expected=result.expected,
        actual=result.actual,
        assertion=result.assertion,
    )


def block_nodes_online(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.nodes_online``：判**真实在网/可达**节点数（两路只读证据交叉）。

    证据 A = ``10H-F4``「已抄从节点数量」（> 0 才说明确有节点被成功抄到）；
    证据 B = ``10H-F2`` 逐节点「信号品质>0」的节点数。

    - 两路任一 > 0 且达到 ``min`` → pass；都明确为 0 → **fail**（档案有节点但一个都不通）；
    - 任一路取不到 → 只用可得那路；两路都取不到 → unknown；
    - **绝不**用「从节点总数量」（档案数）推断在网。

    参数：``min``（期望在网节点数下限，默认 1）、``count``（10H-F2 查询条数，默认 32）。

    块声明的 ``afn/fn`` 取 **10H-F4**（主证据 A 就在这条应答里），本块另发**一次**
    10H-F2（带 ``start=0 / count=``）取逐节点信号品质；两条都是只读查询。
    """
    target, error = require_target(
        params, AFN_ROUTE_QUERY, FN_ROUTE_STATUS, BLOCK_NODES_ONLINE
    )
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    raw_min = action.get("min")
    minimum = int_of(DEFAULT_MIN_ONLINE if raw_min in (None, "") else raw_min)
    if minimum is None or minimum < 0:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s 参数 min 必须 ≥0 的整数（期望在网节点数下限），收到 %r"
            % (BLOCK_NODES_ONLINE, raw_min),
        )

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    snapshot = nodes_online_snapshot(
        cap=cap,
        count=action.get("count", DEFAULT_NODES_ONLINE_COUNT),
        max_pages=action.get("max_pages"),
        expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT),
    )
    job_ids = list(snapshot.get("job_ids") or [])
    channel_a = snapshot.get("channel_a") or {}
    channel_b = snapshot.get("channel_b") or {}
    readings = {
        EVIDENCE_ARCHIVED: channel_a.get("value"),
        EVIDENCE_QUALITY: channel_b.get("value"),
        "档案数（不参与判定）": snapshot.get("archive_total"),
    }
    state, detail = judge_nodes_online(snapshot, minimum)
    # 判定结论后面追加两路证据明细（扫描条数/各自读数），人工复核时不用再翻 job
    evidence_text = snapshot.get("detail") or ""
    if evidence_text:
        detail = "%s（两路证据明细：%s）" % (detail, evidence_text)
    if state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="%s：%s" % (BLOCK_NODES_ONLINE, detail),
            facts={
                FACT_NODES_ONLINE_MIN: {
                    "state": FACT_YES,
                    "detail": detail,
                    "evidence": list(job_ids),
                }
            },
            evidence_ids=job_ids,
            expected="在网节点数 ≥ %d" % minimum,
            actual=readings,
        )
    if state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：%s（这是防假 PASS 的 FAIL：两路独立证据都显示没有节点被抄通）"
            % (BLOCK_NODES_ONLINE, detail),
            evidence_ids=job_ids,
            expected="在网节点数 ≥ %d" % minimum,
            actual=readings,
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：%s" % (BLOCK_NODES_ONLINE, detail),
        evidence_ids=job_ids,
        expected="在网节点数 ≥ %d" % minimum,
        actual=readings,
    )


def block_route_status(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.route_status``：10H-F4 查路由运行状态（学习态按协议语义分层，不直接判 FAIL）。

    分层（``common.classify_route_state``，含协议位出处）：

    - ``ROUTE_RUNNING``（工作状态=抄表）→ **pass**；
    - ``ROUTE_LEARNING_INCONCLUSIVE``（运行状态字 D1 路由完成标志=1 **且** 档案数>0，
      但工作状态=学习）→ **inconclusive**（块态 unknown）：路由基础已建立，
      尚未进入抄表工作 —— 刚下发 12H-F1/11H-F5 后进入学习态是协议正常行为；
    - ``ROUTE_LEARNING_INCOMPLETE``（路由完成标志=0 + 学习）→ 块态 unknown（单帧不足以
      证明"持续未完成"），显式 ``params.learning_fail=true`` 才判 fail；
    - ``ROUTE_UNKNOWN``（字段缺失/取值非法）→ unknown。

    显式 ``params.expect``（字符串/数组）仍按原口径匹配「工作状态/当前状态」。
    """
    target, error = require_target(params, AFN_ROUTE_QUERY, FN_ROUTE_STATUS, BLOCK_ROUTE_STATUS)
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    expect = action.get("expect", action.get("expect_state"))
    learning_fail = flag_of(action.get("learning_fail"))
    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    snapshot = route_snapshot(
        cap=cap, expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT)
    )
    job_ids = list(snapshot.get("job_ids") or [])
    if snapshot.get("state") != "answered":
        return BlockResult(
            state=str(snapshot.get("block_state") or BLOCK_UNKNOWN),
            detail="%s：%s" % (BLOCK_ROUTE_STATUS, snapshot.get("detail") or "查询未得到可判定应答"),
            evidence_ids=job_ids,
        )
    label, reason = classify_route_state(snapshot)
    state, detail = judge_route_status(snapshot, expect, learning_fail=learning_fail)
    actual = {
        "分类": label,
        "工作状态": snapshot.get("work_state"),
        "当前状态": snapshot.get("current_state"),
        "路由完成标志": snapshot.get("route_done"),
        "档案数": snapshot.get("archive_total"),
        "已抄": snapshot.get("answered_total"),
    }
    if state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="%s：%s" % (BLOCK_ROUTE_STATUS, detail),
            evidence_ids=job_ids,
            actual=actual,
        )
    if state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：%s（分类=%s）" % (BLOCK_ROUTE_STATUS, detail, label),
            evidence_ids=job_ids,
            expected=expect,
            actual=actual,
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：%s（分类=%s）" % (BLOCK_ROUTE_STATUS, detail, label),
        evidence_ids=job_ids,
        actual=actual,
    )


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

_BLOCKS = {
    BLOCK_ROUTE_RESTART: block_route_restart,
    BLOCK_ACTIVE_REGISTRATION: block_active_registration,
    BLOCK_ARCHIVE_COUNT: block_archive_count,
    BLOCK_ONNET_COUNT: block_onnet_count,        # 兼容别名（内部委托 archive_count）
    BLOCK_NODES_ONLINE: block_nodes_online,
    BLOCK_ROUTE_STATUS: block_route_status,
}


def register_blocks(*, replace: bool = False) -> None:
    """注册本模块的块执行体（幂等；同 id 被别的执行体占用则抛 ``Duplicate``）。"""
    for block_id, fn in _BLOCKS.items():
        existing = BLOCK_REGISTRY.get(block_id)
        if existing is fn:
            continue
        if existing is not None:
            if not replace:
                raise Duplicate(
                    "块 %r 已被其它执行体注册（如需接管请 replace=True）" % block_id
                )
            unregister_block(block_id)
        register_block(block_id, fn)


register_blocks()


def register() -> None:
    """兼容 ``case_runner.blocks.loader.register_domain_modules`` 的约定入口。"""
    register_blocks()


__all__ = [
    "BLOCK_ROUTE_RESTART",
    "BLOCK_ACTIVE_REGISTRATION",
    "BLOCK_ARCHIVE_COUNT",
    "BLOCK_ONNET_COUNT",      # 兼容别名
    "BLOCK_NODES_ONLINE",
    "BLOCK_ROUTE_STATUS",
    "FACT_ONNET_COUNT_MIN",   # 兼容别名事实（判档案数）
    "FACT_NODES_ONLINE_MIN",
    "FACT_ROUTE_RUNNING",
    "AFN_ROUTE_CONTROL",
    "FN_ROUTE_RESTART",
    "AFN_NWK_SET",
    "FN_ACTIVE_REGISTRATION",
    "WORK_STATES",
    "CURRENT_STATES",
    "ROUTE_LABELS",
    "DEFAULT_MIN_ARCHIVE",
    "DEFAULT_MIN_ONNET",
    "time6_to_digits",
    "registration_time_params",
    "registration_payload",
    "block_route_restart",
    "block_active_registration",
    "block_archive_count",
    "block_onnet_count",
    "block_nodes_online",
    "block_route_status",
    "register_blocks",
    "register",
    # 再导出（事实模块与旧调用方从 nwk 取这些名字仍然可用；实现只有 common 一份）
    "archive_count_snapshot",
    "judge_archive_count_min",
    "onnet_snapshot",          # 兼容别名
    "judge_onnet_min",         # 兼容别名
    "nodes_online_snapshot",
    "judge_nodes_online",
    "route_snapshot",
    "route_status_fields",
    "classify_route_state",
    "judge_route_status",
    "judge_route_running",
    "judge_step",
    "judge_confirm_step",
    "send_step",
    "call_verify",
    "verify_failure_state",
    "steps_of",
    "snapshot_kind",
    "block_action_params",
    "require_target",
    "resolve_simcon",
    "capability_gate",
    "int_of",
    "item_value",
    "items_of",
    "AFN_CONFIRM",
    "FN_CONFIRM",
    "AFN_ROUTE_QUERY",
    "FN_ONNET_COUNT",
    "FN_ROUTE_STATUS",
    "ITEM_TOTAL",
    "ITEM_WORK_STATE",
    "ITEM_CURRENT_STATE",
    "ITEM_SWITCH",
    "ITEM_RUN_WORD",
    "DEFAULT_TIMEOUT",
]
