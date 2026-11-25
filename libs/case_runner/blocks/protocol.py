"""协议一致性块（REQS-0043 工位 H；CASE-BACKLOG §G-6「协议一致性三方补步」）。

``bench.simeter_protocol``（kind=setup，side_effect=reversible）
------------------------------------------------------------------
设/读**模拟电表侧协议模式**（``sim_meter`` 即装即采 ``jzjc.protocol_type``：
00H 透明 / 02H 645-2007 / 03H 698.45，``libs/sim_meter/api.py:102`` 默认 0x02、
``:109`` 可传 3）。写动作 = 经 ``capabilities/simeter`` 既有 HTTP 面
（``GET /api/simeter/config`` 读、``POST /api/simeter/jzjc`` 写）切 645/698；
读动作 = 写后再 ``GET /config`` **回读确认**（写返回 ok 不足以判就绪，同
``fix.simeter_session`` 开口复核口径）。

出处：用户权威口径（2026-11-24）「集中器下发的协议类型与模拟电表的协议类型
要保持一致」+ §G-2 H2（台架三方不一致：CCO 档案=698 / simeter=645 态）。
观测 = 当前模式与切换确认；判据（模式==期望）归步骤期望（观察块零判定，
REQS-0044 §4.2）。side_effect=reversible：协议模式可再切回（无破坏性）。

``sta.meter_protocol``（kind=probe，side_effect=none）
------------------------------------------------------------------
只读判读 **STA 表口协议日志指纹**：STA 开机自动探测表口（645 读址 + 698 探帧），
结论落在会话日志 ``addrmap success,DLT645_CMD,check_procotol:N`` 行
（出处 §G-1.1；KB 无现成口径）。同时观测 ``try 698 pro`` 探帧尝试行。

**判读口径未经真机校准**（候选指纹，待 §G-4 E2 对照实验固化）：
- ``N=2`` → 645-2007：run 20261124-192633 STA 日志行 532/12686 实测
  （simeter 帧日志 seq 59/76 佐证：698 探帧 OAD 40010200 被不应答）；
- ``N=3`` → 698.45：**预期值，未实证**（E2 补齐 simeter 探帧应答后复位校准）。
因此本块**不设 provides**（未经校准的事实不回写事实缓存），观测值仅供
人工判读与诊断；引用它的步骤若要判定，须在期望里自行承担口径风险。

分层：只依赖 ``capabilities``（simeter/module）+ ``core``；与兄弟域模块零 import。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from case_runner.blocks.common import (
    block_action_params,
    coerce_int,
    int_of,
    module_capability,
    simeter_capability,
)
from case_runner.core.executor import BlockResult, RunContext
from case_runner.core.expect import (
    OBS_BENCH_UNREACHABLE,
    OBS_INCOMPLETE,
    OBS_INVALID_PARAM,
    Observation,
)
from case_runner.core.models import BLOCK_UNKNOWN
from case_runner.core.registry import has_block, register_block

#: 块 id（与 ``data/blocks/*.json`` 一一对应）
BLOCK_SIMETER_PROTOCOL = "bench.simeter_protocol"
BLOCK_STA_METER_PROTOCOL = "sta.meter_protocol"

#: simeter 侧协议模式值域（``sim_meter/meter.py`` JzjcConfig.protocol_type 口径）
SIMETER_MODE_NAMES: Dict[int, str] = {0: "透明", 2: "645-2007", 3: "698.45"}
#: 允许切换的目标模式（00H 透明不是协议切换目标，不开放写）
SWITCHABLE_MODES = (2, 3)
#: 缺省目标：698.45（本批交付口径：meter_698_smoke 三方一致）
DEFAULT_TARGET_PROTOCOL = 3

#: STA 表口协议指纹（§G-1.1；大小写按 STA 实测原文，``procotol`` 是固件拼写）
FINGERPRINT_CHECK_PROTOCOL = "check_procotol:"
FINGERPRINT_TRY_698 = "try 698 pro"
#: 判读值 → 协议名（候选口径：2=645-2007 实测；3=698.45 预期待校准）
STA_PROTOCOL_NAMES: Dict[int, str] = {1: "645-1997", 2: "645-2007", 3: "698.45"}

#: sta-main 会话日志默认读取行数（同 ``reset.sta`` 的 lines 缺省）
DEFAULT_LOG_LINES = 400

#: 「台架明确缺失」类错误码 → 原因码 bench_unreachable（skipped 语义）
_BENCH_ABSENT = frozenset({"http_404", "no_base_url", "unreachable", "transport_error"})


# --------------------------------------------------------------------------- #
# bench.simeter_protocol
# --------------------------------------------------------------------------- #


def _mode_name(value: Any) -> str:
    """协议模式字节值 → 人读名（取不到 → 空串，绝不猜）。"""
    number = int_of(value)
    if number is None:
        return ""
    return SIMETER_MODE_NAMES.get(number, "未知(0x%02X)" % number)


def _env_reason(detail: Any) -> str:
    """台架类异常 → 原因码：明确缺失/不可达 → bench_unreachable，其余 → incomplete。"""
    text = str(detail or "")
    if any(token in text for token in ("404", "未挂载", "不可达", "no_base_url",
                                       "unreachable", "transport_error", "传输层异常")):
        return OBS_BENCH_UNREACHABLE
    return OBS_INCOMPLETE


def bench_simeter_protocol(params: Optional[Dict[str, Any]] = None,
                           ctx: Optional[RunContext] = None) -> BlockResult:
    """``bench.simeter_protocol``：设/读模拟电表协议模式——只报观测（零判定）。

    观测字段：``目标协议`` / ``切换前模式`` / ``切换动作`` / ``协议模式``（回读）/
    ``协议名称`` / ``切换确认``（仅写路径给出：回读模式==目标）。
    判据（协议模式==期望）由步骤 ``expect`` 声明，如 ``eq 协议模式=3``。

    ``params.protocol``：目标模式（2=645-2007 / 3=698.45）；不给（``null``）＝只读。
    """
    raw = dict(params or {})
    action = block_action_params(raw)
    capability = simeter_capability(raw, ctx)
    target = int_of(action.get("protocol"))
    if target is not None and target not in SWITCHABLE_MODES:
        detail = (
            "%s：目标协议非法：%r（仅 2=645-2007 / 3=698.45；只读请给 null）"
            "——配置错误，不是台架结论" % (BLOCK_SIMETER_PROTOCOL, action.get("protocol"))
        )
        return _observation_result(Observation(
            values={"目标协议": target}, complete=False,
            reason_code=OBS_INVALID_PARAM, detail=detail), detail)

    # 1) 读当前模式（GET /config → jzjc.protocol_type）
    cfg = capability.config()
    if not cfg.get("ok"):
        detail = "%s：simeter 配置不可读（%s）：%s" % (
            BLOCK_SIMETER_PROTOCOL, cfg.get("error"), cfg.get("detail", ""))
        return _observation_result(Observation(
            values={"目标协议": target}, complete=False,
            reason_code=_env_reason(detail), detail=detail), detail)
    before = int_of(cfg.get("protocol_type"))
    if before is None:
        detail = ("%s：simeter 配置可读但取不到 jzjc.protocol_type（响应缺字段）"
                  "——说不清当前模式，不动手" % BLOCK_SIMETER_PROTOCOL)
        return _observation_result(Observation(
            values={"目标协议": target, "切换前模式": None}, complete=False,
            reason_code=OBS_INCOMPLETE, detail=detail), detail)

    values: Dict[str, Any] = {
        "目标协议": target,
        "切换前模式": before,
        "协议名称": _mode_name(before),
    }
    switch_action = "只读（未给目标协议）"

    # 2) 写动作：目标与当前不同才切（POST /jzjc 只带 protocol_type，其余字段保留）
    if target is not None and before != target:
        switched = capability.set_jzjc(protocol_type=target)
        if not switched.get("ok"):
            detail = "%s：切换 %s 未生效（%s）：%s" % (
                BLOCK_SIMETER_PROTOCOL, _mode_name(target),
                switched.get("error"), switched.get("detail", ""))
            values["切换动作"] = "已下发切换但未确认（%s）" % switched.get("error")
            return _observation_result(Observation(
                values=values, complete=False,
                reason_code=_env_reason(detail), detail=detail), detail)
        switch_action = "已切换 %s → %s" % (_mode_name(before), _mode_name(target))
    elif target is not None:
        switch_action = "无需切换（已是 %s）" % _mode_name(before)

    # 3) 读动作：回读确认（写返回 ok 不足以判就绪）
    after_cfg = capability.config()
    if not after_cfg.get("ok"):
        detail = "%s：%s；回读未拿到（%s）：%s" % (
            BLOCK_SIMETER_PROTOCOL, switch_action,
            after_cfg.get("error"), after_cfg.get("detail", ""))
        values["切换动作"] = switch_action
        return _observation_result(Observation(
            values=values, complete=False,
            reason_code=_env_reason(detail), detail=detail), detail)
    after = int_of(after_cfg.get("protocol_type"))
    values["切换动作"] = switch_action
    values["协议模式"] = after
    values["协议名称"] = _mode_name(after)
    if target is not None:
        values["切换确认"] = after == target
    detail = "%s：%s；回读模式=%s（%s）" % (
        BLOCK_SIMETER_PROTOCOL, switch_action,
        after if after is not None else "缺失", _mode_name(after))
    complete = after is not None and (target is None or after == target)
    return _observation_result(Observation(
        values=values, complete=complete, reason_code="",
        detail=detail), detail)


# --------------------------------------------------------------------------- #
# sta.meter_protocol
# --------------------------------------------------------------------------- #


def _protocol_number_after(text: str, marker: str) -> Optional[int]:
    """取指纹行里 ``marker`` 之后的十进制数（``check_procotol:2`` → 2；无 → None）。"""
    at = text.find(marker)
    if at < 0:
        return None
    rest = text[at + len(marker):].strip()
    digits = ""
    for ch in rest:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int_of(digits) if digits else None


def match_protocol_fingerprint(entries: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """扫会话日志行，取**最近一条**表口协议指纹（§G-1.1 候选口径，未校准）。

    返回 ``{"line", "number", "name", "ts", "seq", "try698", "try698_line"}``；
    没有指纹时 ``line`` 为空串、``number``/``name`` 为 ``None``/``""``。
    ``try698`` = 窗口内出现过 ``try 698 pro`` 探帧尝试行（True/False）。
    """
    line = ""
    number: Optional[int] = None
    ts = None
    seq = None
    try698 = False
    try698_line = ""
    for entry in entries or []:
        text = str((entry or {}).get("text") or "")
        if not text:
            continue
        if FINGERPRINT_CHECK_PROTOCOL in text:
            candidate = _protocol_number_after(text, FINGERPRINT_CHECK_PROTOCOL)
            if candidate is not None:
                line = text.strip()
                number = candidate
                ts = entry.get("ts")
                seq = entry.get("seq")
        if FINGERPRINT_TRY_698 in text:
            try698 = True
            try698_line = text.strip()
    return {
        "line": line, "number": number,
        "name": STA_PROTOCOL_NAMES.get(number, "") if number is not None else "",
        "ts": ts, "seq": seq,
        "try698": try698, "try698_line": try698_line,
    }


def sta_meter_protocol(params: Optional[Dict[str, Any]] = None,
                       ctx: Optional[RunContext] = None) -> BlockResult:
    """``sta.meter_protocol``：只读判读 STA 表口协议日志指纹——只报观测（零判定）。

    观测字段：``指纹原文`` / ``判读协议类型`` / ``判读协议名称`` / ``698探帧尝试`` /
    ``日志行数``。**不设 provides**：指纹口径未经真机校准（N=3 待 E2），
    未经校准的事实不回写事实缓存（REQS-0044 §4.2 观察块零判定 + 事实门槛）。

    不 ensure 会话、不发任何激励（side_effect=none）：只读 ``sta-main`` 会话日志，
    会话不存在 / 日志不可读 → 原因码 ``bench_unreachable``；
    日志可读但窗口内无指纹 → ``incomplete``（STA 尚未完成表口探测或已滚动）。
    """
    raw = dict(params or {})
    action = block_action_params(raw)
    cap = module_capability(raw, ctx)
    mapping_id = str(action.get("mapping_id") or raw.get("mapping_id")
                     or getattr(cap, "mapping_id", "") or "sta-main")
    log_lines = coerce_int(action.get("lines"), DEFAULT_LOG_LINES)

    logs = cap.logs(mapping_id, lines=log_lines)
    if not logs.get("ok"):
        detail = "%s：STA 会话日志不可读（%s）：%s" % (
            BLOCK_STA_METER_PROTOCOL, logs.get("error"), logs.get("detail", ""))
        return _observation_result(Observation(
            values={}, complete=False,
            reason_code=_env_reason(detail), detail=detail), detail)

    entries = [e for e in (logs.get("entries") or []) if isinstance(e, Mapping)]
    hit = match_protocol_fingerprint(entries)
    values: Dict[str, Any] = {
        "指纹原文": hit["line"],
        "判读协议类型": hit["number"],
        "判读协议名称": hit["name"],
        "698探帧尝试": bool(hit["try698"]),
        "日志行数": len(entries),
    }
    if not hit["line"]:
        detail = (
            "%s：窗口内（最近 %d 行）未命中表口协议指纹 %r——STA 尚未完成表口探测、"
            "日志已滚动或本次复位未触发探测；判读口径未经真机校准（§G-4 E2 待做）"
            % (BLOCK_STA_METER_PROTOCOL, len(entries), FINGERPRINT_CHECK_PROTOCOL)
        )
        return _observation_result(Observation(
            values=values, complete=False,
            reason_code=OBS_INCOMPLETE, detail=detail), detail)
    detail = (
        "%s：表口协议指纹判读=%s（%s）；指纹原文=%r；698 探帧尝试=%s——"
        "候选口径未经真机校准（N=2 对应 645 的证据：run 20261124-192633 STA 日志行 532；"
        "N=3 对应 698 待 E2 对照实验固化），不回写事实"
        % (BLOCK_STA_METER_PROTOCOL, hit["number"], hit["name"] or "未知取值",
           hit["line"], "是" if hit["try698"] else "否")
    )
    return _observation_result(Observation(
        values=values, complete=True, reason_code="", detail=detail), detail)


# --------------------------------------------------------------------------- #
# 公共出口（观察块：观测完备时步骤期望判 pass/fail，不完备落 skipped/unknown）
# --------------------------------------------------------------------------- #


def _observation_result(observation: Observation, detail: str) -> BlockResult:
    """观察块统一出口：观测交步骤判定，块态置 unknown（不参与判定语义）。

    原因码为环境类（bench_unreachable）时步骤态落 skipped；invalid_param /
    incomplete 依 core.expect 的原因码映射（invalid_param → unknown，
    incomplete → skipped/unknown 按 ENVIRONMENT_REASON_CODES 口径）。
    """
    return BlockResult(state=BLOCK_UNKNOWN, detail=detail, observation=observation)


# --------------------------------------------------------------------------- #
# 注册（幂等）
# --------------------------------------------------------------------------- #


def register() -> None:
    """注册本模块的块执行体（幂等：已注册则跳过）。"""
    if not has_block(BLOCK_SIMETER_PROTOCOL):
        register_block(BLOCK_SIMETER_PROTOCOL, bench_simeter_protocol)
    if not has_block(BLOCK_STA_METER_PROTOCOL):
        register_block(BLOCK_STA_METER_PROTOCOL, sta_meter_protocol)


register()


__all__ = [
    "BLOCK_SIMETER_PROTOCOL",
    "BLOCK_STA_METER_PROTOCOL",
    "SIMETER_MODE_NAMES",
    "SWITCHABLE_MODES",
    "DEFAULT_TARGET_PROTOCOL",
    "FINGERPRINT_CHECK_PROTOCOL",
    "FINGERPRINT_TRY_698",
    "STA_PROTOCOL_NAMES",
    "DEFAULT_LOG_LINES",
    "bench_simeter_protocol",
    "sta_meter_protocol",
    "match_protocol_fingerprint",
    "register",
]
