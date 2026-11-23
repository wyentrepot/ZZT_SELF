"""档案类原子块 ``archive.add`` / ``archive.del`` / ``archive.query``（REQS-0041 §10.5 BL-2）。

判据（BL-2，缺一不可）
--------------------
``archive.add``   **双判据** —— 00H-F1 确认收到 **且** 10H-F2 能回查到该地址。
                  只收到确认、档案没落地 → **FAIL**（「防假 PASS」的核心：
                  ``test_archive_nwk.py::test_archive_add_confirm_received_but_not_landed_is_fail``）。
``archive.del``   00H-F1 确认 **且** 10H-F2 查不到该地址。
``archive.query`` 10H-F2 返回的档案表含目标地址 / 总数符合期望 / 期望空表；
                  **一条判据都没给（无 meter_addr / expect_total / expect_empty）→ ``unknown``**
                  —— 什么都不断言却判 pass 就是假 PASS。

地址角色（REQS-0042 §3.1 / **D2-1** / **D2-2**）
----------------------------------------------
调用方**只认识电表 645 表地址** ``meter_addr``（多值 ``meter_addrs[]``）；档案侧地址由
:func:`case_runner.blocks.common.archive_addr_of` **换算**得出（12 位档案人读地址 =
6 字节整体反转），本文件**不写任何反转逻辑**。三个块一律「先解析电表地址 → 换算成档案
地址 → 用档案地址构 11H 帧与 10H-F2 回查判据」，**同一块内只有一只表**（action 与 assert
不许各指一只，否则会出现「删 A 验 B」的假通过）。

状态口径（REQS RU-3「绝不假 PASS」）
-----------------------------------
===================  ==========================================================
00H-F2 否认帧         ``fail``（设备明确拒绝，detail 带错误状态字）
帧已发出但超时无应答  ``fail``（激励确实发出、设备无响应）
闸门忙 409            ``skipped``（**不自动重试 POST**，交上层策略）
台架不可达/服务异常   ``skipped``
job 轮询超时          ``unknown``（不表示设备失败）
构帧参数错（422/构帧失败） ``unknown``（配置错误，不是设备结论）
应答无法解析/档案表被截断  ``unknown``
===================  ==========================================================

两个 1376.2 记法坑（沿用 ``sim_concentrator.matcher`` 口径，``blocks/common.py`` 已归一）
--------------------------------------------------------------------------------------
- **AFN 字符串按 hex、整数按字节值**：``"11"``→0x11，``11``→0x0B，``0x11``→0x11。
- **FN 一律十进制编号**：``"F2"``/``"2"``/``2`` → 2。

依赖方向（REQS §10.5 解耦要求）
-----------------------------
本文件**不 import 任何兄弟块模块**：通用 1376.2 单步/取帧/判定小工具与 10H-F2 档案表
判定（``archive_snapshot`` / ``judge_contains``）都在 ``blocks/common.py`` 里，**只有一份**
（事实 ``archive.contains`` 探测体 ``facts_nwk.py`` 也从 ``common`` 取同一对象，FT-2）。

``archive.add`` **不声明** ``provides: ["archive.contains"]``：该事实的参数是 ``meter_addr``，
块回写只按 ``fact_id`` 缓存 → 给 A 建档成功会让 B 的探测命中 yes = **跨地址假 PASS**。
块内已做「确认 + 回查」双判据，provides 本属冗余；``loader`` 另有一道结构校验挡住复发。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from case_runner.blocks.common import (  # noqa: F401 - 下面一并对旧调用方再导出
    ADDR_LEN,
    AFN_CONFIRM,
    AFN_ROUTE_QUERY,
    COUNT_MAX,
    DEFAULT_MAX_PAGES,
    DEFAULT_QUERY_COUNT,
    DEFAULT_TIMEOUT,
    FN_ARCHIVE_QUERY,
    FN_CONFIRM,
    ITEM_ANSWERED,
    ITEM_ERROR,
    ITEM_TOTAL,
    SimconCapability,
    afn_int,
    afn_fn_of,
    archive_addr_of,
    archive_snapshot,
    block_action_params,
    call_verify,
    capability_gate,
    field_raw,
    fn_int,
    int_of,
    item_value,
    items_of,
    journal_parsed,
    judge_confirm_step,
    judge_contains,
    judge_step,
    normalize_addr,
    page_table,
    require_target,
    resolve_simcon,
    send_step,
    snapshot_kind,
    steps_of,
    verify_failure_state,
)
from case_runner.core.defaults import default_meter_addr
from case_runner.core.executor import PARAM_ASSERT, BlockResult
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
# 常量（块 id / 事实 id / 档案帧口径）
# --------------------------------------------------------------------------- #

BLOCK_ADD = "archive.add"
BLOCK_DEL = "archive.del"
BLOCK_QUERY = "archive.query"
FACT_CONTAINS = "archive.contains"

AFN_ARCHIVE = 0x11          # 11H：路由设置（档案增删）
FN_ARCHIVE_ADD = 1          # 11H-F1 添加从节点档案
FN_ARCHIVE_DEL = 2          # 11H-F2 删除从节点档案


# --------------------------------------------------------------------------- #
# 块执行体
# --------------------------------------------------------------------------- #


def _query_options(action: Mapping[str, Any]) -> Dict[str, Any]:
    """从 action 取查询口径（start/count/max_pages/expect_timeout）。"""
    return {
        "start": action.get("start", 0),
        "count": action.get("query_count", action.get("count", DEFAULT_QUERY_COUNT)),
        "max_pages": action.get("max_pages", DEFAULT_MAX_PAGES),
        "expect_timeout": action.get("expect_timeout", DEFAULT_TIMEOUT),
    }


# --------------------------------------------------------------------------- #
# 地址角色：电表 645 表地址 → 档案侧地址（REQS-0042 §3.1 / D2-1 / D2-2）
# --------------------------------------------------------------------------- #


def _push_addr(out: List[str], value: Any) -> None:
    """收一个合法 12 位 BCD 表地址（去重；``None``/非法值静默跳过，不猜）。"""
    addr = normalize_addr(value)
    if addr and addr not in out:
        out.append(addr)


def _given_addr_values(action: Mapping[str, Any]) -> List[Any]:
    """入参里**真正给了**的原始表地址值（未校验；``None``/空串不算给）。"""
    out: List[Any] = []
    multi = action.get("meter_addrs")
    if isinstance(multi, (list, tuple)):
        out.extend(item for item in multi if item not in (None, ""))
    elif multi not in (None, ""):
        out.append(multi)
    single = action.get("meter_addr")
    if single not in (None, ""):
        out.append(single)
    return out


def _assert_meter_addrs(params: Any) -> List[str]:
    """块声明 ``assert`` 里写明的电表表地址（判据侧口径，**只在入参没给时兜底**）。

    为什么要读 assert：``action`` 与 ``assert`` 是声明 JSON 里两份手写字段，任一侧单独写
    了另一只表就会变成「档案删 A、回验 B」——那是最典型的假 PASS。取 assert 侧的地址，
    保证同一块内 action 与判据作用于**同一只表**。
    """
    raw = params if isinstance(params, Mapping) else {}
    entries = raw.get(PARAM_ASSERT)
    out: List[str] = []
    if isinstance(entries, (list, tuple)):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            multi = entry.get("meter_addrs")
            if isinstance(multi, (list, tuple)):
                for item in multi:
                    _push_addr(out, item)
            _push_addr(out, entry.get("meter_addr"))
    return out


def meter_addrs_of(
    params: Any,
    *,
    allow_config_default: bool = True,
    allow_assert_fallback: bool = True,
) -> tuple[List[str], List[str]]:
    """解析本块要作用的**电表 645 表地址**：返回 ``(合法地址列表, 非法入参说明列表)``。

    解析顺序（REQS-0042 D2-1/D2-2）：**入参** > **声明默认**（``loader`` 已把 JSON 里的
    ``null`` 解析成配置默认值 ``case_defaults.meter_addr``，到块里就是真值）>
    :func:`case_runner.core.defaults.default_meter_addr`（``allow_config_default`` 时）。

    三个刻意的取舍：

    - **给了但不合法 ≠ 没给**：显式传了 ``"12345"`` 这种垃圾地址，绝不静默换成默认表地址
      ——那会把「传错地址」变成「动到默认那只表」（删档尤其危险）。此时第二个返回值非空，
      由块自己报配置错误；
    - 入参完全没给时，先看声明 ``assert`` 写明的表地址（同一块内动作与判据必须同一只表），
      再退配置默认；
    - **破坏性块两个开关都关**（``archive.del``）：不猜、不兜底，只要调用方没显式给地址就
      不执行。注意 ``loader`` 会把声明里 ``assert[].meter_addr: null`` 解析成配置默认值，
      所以「只看 action」不够——assert 兜底也得关，否则默认表会从后门被删掉。
    """
    action = block_action_params(params)
    given = _given_addr_values(action)
    if given:
        good: List[str] = []
        bad: List[str] = []
        for value in given:
            addr = normalize_addr(value)
            if addr:
                if addr not in good:
                    good.append(addr)
            else:
                bad.append(repr(value))
        return good, bad

    if allow_assert_fallback:
        asserted = _assert_meter_addrs(params)
        if asserted:
            return asserted, []
    if allow_config_default:
        fallback = normalize_addr(default_meter_addr())
        if fallback:
            return [fallback], []
    return [], []


def archive_addrs_of(meter_addrs: Sequence[Any]) -> List[str]:
    """电表侧地址 → **档案侧人读地址**（唯一换算 = ``common.archive_addr_of``）。"""
    out: List[str] = []
    for meter_addr in meter_addrs:
        addr = archive_addr_of(meter_addr)
        if addr and addr not in out:
            out.append(addr)
    return out


def _fail(detail: str) -> BlockResult:
    return BlockResult(state=BLOCK_FAIL, detail=detail)


def block_add(params: Any, ctx: Any = None) -> BlockResult:
    """``archive.add``：11H-F1 建档，**双判据**（00H-F1 确认 + 10H-F2 回查落地）。

    入参 ``meter_addr``（电表 645 表地址）→ ``archive_addr_of()`` 换算成档案地址后构帧与
    回查；返回值里的 ``facts={"archive.contains": ...}`` **只作证据**（进块记录），不作为
    ``provides`` 回写事实缓存 —— 该事实带 ``meter_addr`` 参数，回写会造成跨地址假 PASS。
    """
    target, error = require_target(params, AFN_ARCHIVE, FN_ARCHIVE_ADD, BLOCK_ADD)
    if target is None:
        return _fail(error)
    action = block_action_params(params)
    meter_addrs, bad = meter_addrs_of(params)
    if bad:
        return _fail(
            "archive.add 参数 meter_addr 非法：%s（需 12 位 BCD 数字，如 123456789012）"
            % "、".join(bad)
        )
    archive_addrs = archive_addrs_of(meter_addrs)
    if not archive_addrs:
        return _fail(
            "archive.add 缺少必填参数 meter_addr（12 位 BCD 电表 645 表地址，如 123456789012；"
            "缺省取 case_defaults.meter_addr）"
        )
    if len(archive_addrs) > 1:
        return _fail(
            "archive.add 一次只能给一只表（meter_addr 单值 / meter_addrs 单个），收到 %d 个：%s"
            % (len(archive_addrs), ",".join(meter_addrs))
        )
    addr = archive_addrs[0]           # 档案侧人读地址（构帧 + 回查判据都用它）
    meter_addr = meter_addrs[0]       # 电表侧人读地址（只用于 detail 留痕）
    protocol_raw = action.get("protocol", 2)
    protocol = int_of(protocol_raw)
    if protocol is None or protocol not in (2, 3):
        return _fail(
            "archive.add 参数 protocol 只能是 2（645）或 3（698），收到 %r" % (protocol_raw,)
        )

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate

    confirm_timeout = action.get("confirm_timeout", DEFAULT_TIMEOUT)
    confirm = call_verify(
        cap,
        [
            send_step(
                "添加从节点档案 %s（电表表地址 %s）" % (addr, meter_addr),
                AFN_ARCHIVE,
                FN_ARCHIVE_ADD,
                {"action": "add", "addr": addr, "protocol": protocol},
                expect_afn=AFN_CONFIRM,
                expect_fn=FN_CONFIRM,
                timeout=confirm_timeout,
            )
        ],
    )
    job_ids = [str(confirm.get("job_id") or "")] if confirm.get("job_id") else []
    confirm_steps = steps_of(confirm)
    if not confirm_steps:
        state, why = verify_failure_state(confirm)
        return BlockResult(
            state=state,
            detail="%s：建档帧未得到可判定结论，双判据未成立（%s）" % (BLOCK_ADD, why),
            evidence_ids=job_ids,
        )
    verdict = judge_confirm_step(
        confirm_steps[-1], label="11H-F1 建档确认", cap=cap, job=confirm
    )
    if verdict["kind"] != "answered":
        return BlockResult(
            state=verdict["state"],
            detail="%s 判据 1 未成立：%s" % (BLOCK_ADD, verdict["detail"]),
            evidence_ids=job_ids,
            expected="00H-F1 确认帧",
            actual=verdict["kind"],
        )

    snapshot = archive_snapshot(cap=cap, **_query_options(action))
    job_ids.extend(snapshot.get("job_ids") or [])
    contains_state, contains_detail = judge_contains(snapshot, addr)
    if contains_state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="双判据成立：00H-F1 确认已收到，且 %s（10H-F2）" % contains_detail,
            facts={
                FACT_CONTAINS: {
                    "state": FACT_YES,
                    "detail": "archive.add 双判据通过：%s" % contains_detail,
                    "evidence": list(job_ids),
                }
            },
            evidence_ids=list(job_ids),
            expected="确认 + 档案表含 %s" % addr,
            actual="确认 + 档案表含 %s" % addr,
        )
    if contains_state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail=(
                "确认已收到但档案未落地：00H-F1 确认帧已收到，但 %s。"
                "（判据 2 未成立 —— 这是防假 PASS 的 FAIL，不是 unknown）" % contains_detail
            ),
            evidence_ids=list(job_ids),
            expected="档案表含 %s" % addr,
            actual="档案表不含 %s（%s）" % (addr, snapshot.get("detail") or ""),
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=(
            "判据 1 成立（00H-F1 确认已收到），但判据 2 无法判定：%s（%s）"
            % (contains_detail, snapshot.get("detail") or "")
        ),
        evidence_ids=list(job_ids),
        expected="档案表含 %s" % addr,
        actual="未知",
    )


def block_del(params: Any, ctx: Any = None) -> BlockResult:
    """``archive.del``：11H-F2 删档，判据 = 00H-F1 确认 **且** 10H-F2 查不到。

    入参 ``meter_addrs[]``（多值删档）/ ``meter_addr``（单值）；逐个 ``archive_addr_of()``
    换算成档案地址后构帧，回查也逐个按档案地址判 —— 多值时**不允许只验第一个**
    （删了 3 只却只回查 1 只 = 另外 2 只没验就判 PASS）。

    **破坏性动作不吃默认地址**（``allow_config_default=False`` 且不取 assert 兜底）：
    调用方没显式给地址 → 下发前一帧不发就 ``fail``。理由：``case_defaults.meter_addr``
    是「现场那只被测表」，拿它当删档默认等于「不传参就把被测表的档案删了」；而 ``loader``
    会把声明 ``assert`` 里的 ``null`` 解析成同一个默认值，所以 assert 兜底也必须关掉。
    """
    target, error = require_target(params, AFN_ARCHIVE, FN_ARCHIVE_DEL, BLOCK_DEL)
    if target is None:
        return _fail(error)
    action = block_action_params(params)
    meter_addrs, bad = meter_addrs_of(
        params, allow_config_default=False, allow_assert_fallback=False
    )
    if bad:
        return _fail(
            "archive.del 参数 meter_addrs/meter_addr 非法：%s（需 12 位 BCD 数字，"
            "如 123456789012）" % "、".join(bad)
        )
    archive_addrs = archive_addrs_of(meter_addrs)
    if not archive_addrs:
        return _fail(
            "archive.del 缺少必填参数 meter_addrs（12 位电表 645 表地址数组）"
            "或 meter_addr（单只表地址）。删除是破坏性动作，不接受配置默认地址，"
            "请显式给要删的表地址（--params meter_addr=... / meter_addrs=[...]）"
        )
    meters = archive_addrs           # 档案侧地址：构帧 + 回查判据同一份

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate

    confirm = call_verify(
        cap,
        [
            send_step(
                "删除从节点档案 %s（电表表地址 %s）"
                % (",".join(meters), ",".join(meter_addrs)),
                AFN_ARCHIVE,
                FN_ARCHIVE_DEL,
                {"meters": meters},
                expect_afn=AFN_CONFIRM,
                expect_fn=FN_CONFIRM,
                timeout=action.get("confirm_timeout", DEFAULT_TIMEOUT),
            )
        ],
    )
    job_ids = [str(confirm.get("job_id") or "")] if confirm.get("job_id") else []
    confirm_steps = steps_of(confirm)
    if not confirm_steps:
        state, why = verify_failure_state(confirm)
        return BlockResult(
            state=state,
            detail="%s：删档帧未得到可判定结论（%s）" % (BLOCK_DEL, why),
            evidence_ids=job_ids,
        )
    verdict = judge_confirm_step(
        confirm_steps[-1], label="11H-F2 删档确认", cap=cap, job=confirm
    )
    if verdict["kind"] != "answered":
        return BlockResult(
            state=verdict["state"],
            detail="%s 判据 1 未成立：%s" % (BLOCK_DEL, verdict["detail"]),
            evidence_ids=job_ids,
            expected="00H-F1 确认帧",
            actual=verdict["kind"],
        )

    snapshot = archive_snapshot(cap=cap, **_query_options(action))
    job_ids.extend(snapshot.get("job_ids") or [])
    still: List[str] = []
    unknown: List[str] = []
    for addr in meters:
        state, detail = judge_contains(snapshot, addr)
        if state == FACT_YES:
            still.append(addr)
        elif state == FACT_UNKNOWN:
            unknown.append("%s（%s）" % (addr, detail))

    if still:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="确认已收到但档案仍在：00H-F1 确认帧已收到，10H-F2 仍查得到 %s"
            % ",".join(still),
            evidence_ids=list(job_ids),
            expected="删除后查不到 %s" % ",".join(meters),
            actual="仍可查到 %s" % ",".join(still),
        )
    if unknown:
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="判据 1 成立（00H-F1 确认已收到），但判据 2 无法判定：%s"
            % "；".join(unknown),
            evidence_ids=list(job_ids),
        )
    return BlockResult(
        state=BLOCK_PASS,
        detail="双判据成立：00H-F1 确认已收到，且 10H-F2 已查不到 %s（%s）"
        % (",".join(meters), snapshot.get("detail") or ""),
        evidence_ids=list(job_ids),
        expected="删除后查不到 %s" % ",".join(meters),
        actual="查不到 %s" % ",".join(meters),
    )


def block_query(params: Any, ctx: Any = None) -> BlockResult:
    """``archive.query``：10H-F2 查档案表，按 meter_addr / expect_total / 空表 判。

    判定优先级：

    1. 给了 ``meter_addr``（电表表地址，内部换算成档案地址后比对）→ 档案表必须含该地址；
    2. 否则给了 ``expect_total`` → 总数必须相等；
    3. 否则 ``expect_empty=true`` 或 ``count=0`` → 期望空表（total==0 且无地址）；
    4. 都没有 → **``unknown``**：一条判据都没有就什么都不该断言。
       查询应答本身可解析只说明「帧通了」，不说明「档案内容符合期望」；
       把这种情形判 pass 就是假 PASS（detail 写明原因与已看到的档案表摘要）。

    **不取配置默认地址**（``allow_config_default=False``）：本块的地址就是判据，凭空塞一只
    默认表进判据会把「没给判据」变成「给了一条可能不成立也没人要的判据」。
    """
    target, error = require_target(params, AFN_ROUTE_QUERY, FN_ARCHIVE_QUERY, BLOCK_QUERY)
    if target is None:
        return _fail(error)
    action = block_action_params(params)
    meter_addrs, bad = meter_addrs_of(params, allow_config_default=False)
    if bad:
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="%s：参数 meter_addr 非法：%s（需 12 位 BCD 数字，如 123456789012）"
            "——配置问题，不是档案表结论" % (BLOCK_QUERY, "、".join(bad)),
        )
    archive_addrs = archive_addrs_of(meter_addrs)
    addr = archive_addrs[0] if archive_addrs else ""
    meter_addr = meter_addrs[0] if meter_addrs else ""
    expect_total = int_of(action.get("expect_total"))
    raw_count = action.get("count")
    expect_empty = bool(action.get("expect_empty")) or int_of(raw_count) == 0

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate

    options = _query_options(action)
    options["count"] = DEFAULT_QUERY_COUNT if expect_empty else options["count"]
    snapshot = archive_snapshot(cap=cap, **options)
    job_ids = list(snapshot.get("job_ids") or [])
    if snapshot.get("state") != "answered":
        return BlockResult(
            state=str(snapshot.get("block_state") or BLOCK_UNKNOWN),
            detail="%s：%s" % (BLOCK_QUERY, snapshot.get("detail") or "查询未得到可判定应答"),
            evidence_ids=job_ids,
        )
    summary = "档案表 total=%s，已查 %d 条 %s" % (
        snapshot.get("total") if snapshot.get("total") is not None else "未知",
        len(snapshot.get("addresses") or []),
        "（完整）" if snapshot.get("complete") else "（未覆盖全部，已翻页上限）",
    )

    if addr:
        state, detail = judge_contains(snapshot, addr)
        if state == FACT_YES:
            return BlockResult(
                state=BLOCK_PASS,
                detail="%s：%s（电表表地址 %s）" % (BLOCK_QUERY, detail, meter_addr),
                evidence_ids=job_ids,
                expected="档案表含 %s" % addr,
                actual="含 %s" % addr,
            )
        if state == FACT_NO:
            return BlockResult(
                state=BLOCK_FAIL,
                detail="%s：%s（%s；电表表地址 %s）"
                % (BLOCK_QUERY, detail, summary, meter_addr),
                evidence_ids=job_ids,
                expected="档案表含 %s" % addr,
                actual="不含 %s" % addr,
            )
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="%s：%s（电表表地址 %s）" % (BLOCK_QUERY, detail, meter_addr),
            evidence_ids=job_ids,
        )

    if expect_total is not None:
        total = snapshot.get("total")
        if total is None:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="%s：应答里没有「%s」字段，无法比对期望总数 %d（%s）"
                % (BLOCK_QUERY, ITEM_TOTAL, expect_total, summary),
                evidence_ids=job_ids,
            )
        if total == expect_total:
            return BlockResult(
                state=BLOCK_PASS,
                detail="%s：总数符合期望 %d（%s）" % (BLOCK_QUERY, expect_total, summary),
                evidence_ids=job_ids,
                expected=expect_total,
                actual=total,
            )
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：总数不符（期望 %d，实得 %d；%s）"
            % (BLOCK_QUERY, expect_total, total, summary),
            evidence_ids=job_ids,
            expected=expect_total,
            actual=total,
        )

    if expect_empty:
        total = snapshot.get("total")
        addresses = list(snapshot.get("addresses") or [])
        if total is not None and total == 0 and not addresses:
            return BlockResult(
                state=BLOCK_PASS,
                detail="%s：档案表为空（total=0）" % BLOCK_QUERY,
                evidence_ids=job_ids,
            )
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：期望空表，实得 total=%s / %d 条地址（%s）"
            % (BLOCK_QUERY, total if total is not None else "未知", len(addresses), summary),
            evidence_ids=job_ids,
            expected="空档案表",
            actual="total=%s / %d 条" % (total, len(addresses)),
        )

    # 一条判据都没给 → 什么都没断言 = 假 PASS，只能 unknown
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：未指定 meter_addr/expect_total/expect_empty，无法断言档案内容"
        "（查询应答可解析：%s；帧通了不等于档案内容符合期望）" % (BLOCK_QUERY, summary),
        evidence_ids=job_ids,
    )


# --------------------------------------------------------------------------- #
# 注册（import 即注册；重复注册同 id 按注册表契约报 Duplicate）
# --------------------------------------------------------------------------- #

_BLOCKS = {
    BLOCK_ADD: block_add,
    BLOCK_DEL: block_del,
    BLOCK_QUERY: block_query,
}


def register_blocks(*, replace: bool = False) -> None:
    """注册本模块的块执行体（幂等：已注册同一执行体则跳过）。

    测试隔离流程：``clear_registries()`` → ``register_blocks()``。
    同一 id 被**别的**执行体占用时抛 :class:`Duplicate`（不静默覆盖）。
    """
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
    # 块/事实 id
    "BLOCK_ADD",
    "BLOCK_DEL",
    "BLOCK_QUERY",
    "FACT_CONTAINS",
    # 执行体
    "block_add",
    "block_del",
    "block_query",
    "register_blocks",
    "register",
    # 地址角色（REQS-0042 §3.1）：电表表地址 → 档案侧地址
    "meter_addrs_of",
    "archive_addrs_of",
    # 档案帧口径
    "AFN_ARCHIVE",
    "FN_ARCHIVE_ADD",
    "FN_ARCHIVE_DEL",
    # 再导出（旧调用方从 archive 取这些名字仍然可用；实现只有 common 一份）
    "archive_snapshot",
    "judge_contains",
    "judge_step",
    "judge_confirm_step",
    "send_step",
    "call_verify",
    "verify_failure_state",
    "steps_of",
    "journal_parsed",
    "snapshot_kind",
    "block_action_params",
    "require_target",
    "resolve_simcon",
    "capability_gate",
    "normalize_addr",
    "int_of",
    "afn_int",
    "fn_int",
    "afn_fn_of",
    "field_raw",
    "items_of",
    "item_value",
    "page_table",
    "SimconCapability",
    "AFN_CONFIRM",
    "AFN_ROUTE_QUERY",
    "FN_ARCHIVE_QUERY",
    "FN_CONFIRM",
    "ADDR_LEN",
    "COUNT_MAX",
    "DEFAULT_QUERY_COUNT",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_TIMEOUT",
    "ITEM_TOTAL",
    "ITEM_ANSWERED",
    "ITEM_ERROR",
]
