"""698 抄表块（REQS-0043 F0-5 路径 1/2）：``meter.read_698``。

02H-F1 数据转发、**通信协议类型 = 03H（DL/T 698.45）**（``03_QGDW10376.2_全帧类型.md:1633``；
CCO 按协议类型提表地址，``CCO实现逻辑/01-事务框架-1376.2协议栈-抄表透传.md:222``），
payload = **完整 698 链路层帧**（68|L|C|SA|CA|HCS|APDU|FCS|16，可带 FE×4 前导）。
表侧应答由模拟电表 698 从站产出（``sim_meter/protocol_698.py:242-274`` GET 应答、
``meter.py:_handle_698`` service=0x05 才应答）——REQS-0037 已交付，本块不重造。

判据出处（CASE-BACKLOG §F0-5 路径 1，冒烟档）：
``用例库/STA/台账/123_国网用例_台账.md:73``（行 35 步骤 11-14）「13H-F1 抄表，内容为
698 读取请求类 05 …」判据「CCO 端正确返回 698 数据」；蒸馏卡
``用例库/CCO/能力域/156_国网AFN功能回归清单_用例蒸馏.md:88`` 同口径。
KB 判据只到「正确返回 698 数据」，块按 0044 §8.1-4 口径补齐到「有值且可解」：
**帧格式合法（HCS/FCS 校验）+ 应答是 GET-Response + OAD 回显 + 数值可解**——
即「证明这次真的读到了表里一项合法 698 数据」，不证明数值真值（真值比对仅当
Procedure 显式给期望）。

请求帧口径（与 STA 真实下发模板一致，``sim_meter/protocol_698.py`` 模块头）：
SECURITY-Request(明文) 包 GET-RequestNormal：``10 00 <len> | 05 01 PIID OAD 00 |
01 10 | RN(16×00)``；``security=False`` 时发裸 APDU（``05 01 PIID OAD 00``）。
缺省 OAD ``20000201``（A 相计量电压——KB ``用例库/STA/能力域/141_分钟级采集_用例蒸馏.md:33-40``
登记的 645 ``02010100`` ↔ 698 ``20000201`` 对应关系；模拟表数据集样例同款）。

观测（零 pass/fail）：``请求OAD`` / ``应答APDU`` / ``应答控制域C`` /
``帧格式合法`` / ``FCS说明`` / ``OAD回显`` / ``数值`` / ``单位`` / ``数据项`` /
``内层698帧``。内层解不开 / 无转发报文 → ``incomplete``；
等满预算无应答 → ``timeout``；请求 OAD 非法 → ``invalid_param``。

复用边界：下发/等待/外层解帧全部复用 ``blocks/meter.py`` 的既有函数
（``probe_forward_reply`` / ``wait_attempts`` / ``decode_outer``），本文件只补
698 请求构帧与 698 应答判读两段纯函数——698 编解码不在块层重写第二份
（构帧走 facade ``build("698.45", …)``，判读走 facade ``decode(…, protocol="698.45")``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from case_runner.blocks.meter import (
    _capability,
    Attempt,
    block_params,
    decode_outer,
    _finalize_observation,
    _forwarded_payload,
    _got_observation,
    meter_addr_of,
    norm_hex,
    poll_hooks,
    probe_forward_reply,
    resolve_expect_timeout,
    wait_attempts,
)
from case_runner.capabilities.frames import FramesCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.executor import PARAM_ASSERT, BlockResult
from case_runner.core.expect import (
    OBS_INCOMPLETE,
    OBS_INVALID_PARAM,
    Observation,
)
from case_runner.core.models import BLOCK_UNKNOWN
from case_runner.core.registry import has_block, register_block

#: 1376.2「通信协议类型」= 03H（DL/T 698.45，全帧类型.md:1633）
PROTOCOL_698_45 = 3

#: 698 APDU 服务标签（DL/T 698.45 §6.3.4，与 sim_meter/protocol_698.py 同词表）
APDU_GET_REQUEST = 0x05
APDU_GET_RESPONSE = 0x85
APDU_SECURITY_REQUEST = 0x10

#: 缺省 OAD：20000201（A 相计量电压；KB 141 蒸馏卡 645↔698 对照 + 模拟表数据集样例）
DEFAULT_OAD = "20000201"

#: FE 前导（表口唤醒，与 sim_meter/protocol_698.FE_PREAMBLE 同款 4×FE）
FE_PREAMBLE_HEX = "FEFEFEFE"


# --------------------------------------------------------------------------- #
# 698 请求构帧（纯函数；链路层走 facade，APDU 在这里拼——facade apdu 透传入参）
# --------------------------------------------------------------------------- #


def build_698_get_apdu(oad: str, *, piid: int = 1, security: bool = True) -> str:
    """GET-RequestNormal APDU hex：裸 ``05 01 PIID OAD 00`` 或 SECURITY 明文包一层。

    SECURITY 明文包（STA 模板，sim_meter/protocol_698.py 模块头 / 简装即采同款）：
    ``10 00 <len> | <内层> | 01 10 | RN 16×00``。
    """
    oad_hex = norm_hex(oad)
    if len(oad_hex) != 8:
        return ""
    inner = bytes([APDU_GET_REQUEST, 0x01, piid & 0xFF]) + bytes.fromhex(oad_hex) + b"\x00"
    if not security:
        return inner.hex().upper()
    wrapped = (bytes([APDU_SECURITY_REQUEST, 0x00, len(inner)]) + inner
               + b"\x01\x10" + b"\x00" * 16)
    return wrapped.hex().upper()


def build_698_get_request(
    addr: Any,
    oad: Any = DEFAULT_OAD,
    *,
    piid: int = 1,
    security: bool = True,
    fe_preamble: bool = True,
    sa_head: str = "05",
    ca: str = "00",
    frames: Any = None,
) -> Tuple[str, str]:
    """构完整 698 读请求链路帧：返回 ``(frame_hex, 失败原因)``。

    - SA = ``<sa_head> + meter_addr``（6 字节表通信地址；simeter `_handle_698`
      按 ``addr[1:]`` 比对）；CA 缺省 ``00``（与 ``build_get_request_43000200`` 同款）；
    - 链路层（L/C/HCS/FCS）由 facade ``build("698.45", …)`` 计算，**不在块层重写**；
    - ``fe_preamble=True``（缺省）加 4×FE 唤醒前导（表口惯例，simeter 会剥离）。
    """
    frames = frames if frames is not None else FramesCapability()
    addr_hex = norm_hex(addr)
    if len(addr_hex) != 12:
        return "", "698 表地址非法：%r（SA 需 12 位 hex 表通信地址，如 000000000001）" % (addr,)
    head = norm_hex(sa_head) or "05"
    apdu_hex = build_698_get_apdu(norm_hex(oad), piid=piid, security=security)
    if not apdu_hex:
        return "", "698 OAD 非法：%r（需 8 位 hex，如 %s）" % (oad, DEFAULT_OAD)
    try:
        built = frames.build("698.45", {
            "addr": head + addr_hex,
            "ca": ca,
            "control": "43",
            "apdu": apdu_hex,
        })
    except Exception as exc:  # noqa: BLE001 - 构帧异常收敛成配置错误
        return "", "698 构帧异常：%r" % (exc,)
    if not (isinstance(built, Mapping) and built.get("ok")):
        return "", "698 构帧不成功：%s" % (
            (built.get("detail") if isinstance(built, Mapping) else built),)
    frame_hex = norm_hex(built.get("frame_hex"))
    if not frame_hex:
        return "", "698 构帧返回为空"
    return ((FE_PREAMBLE_HEX + frame_hex) if fe_preamble else frame_hex), ""


# --------------------------------------------------------------------------- #
# 698 应答判读（帧格式合法 + GET-Response + OAD 回显 + 数值可解 → 全量观测）
# --------------------------------------------------------------------------- #


def _entries(frame: Any) -> List[Mapping[str, Any]]:
    entries: List[Mapping[str, Any]] = []
    if not isinstance(frame, Mapping):
        return entries
    for key in ("fields", "items"):
        value = frame.get(key)
        if isinstance(value, list):
            entries.extend(entry for entry in value if isinstance(entry, Mapping))
    return entries


def _entry(frame: Any, name: str) -> Optional[Mapping[str, Any]]:
    for entry in _entries(frame):
        if str(entry.get("name") or "") == name:
            return entry
    return None


def judge_read_698(
    reply_frame: Any,
    *,
    expect_oad: Any = DEFAULT_OAD,
    frames: Any = None,
) -> Observation:
    """上行 02H-F1 内嵌 698 应答 → 观测（不判结论；判据归步骤 expect）。

    ``complete=True`` 当且仅当内层 698 帧解得开（帧格式合法性由 facade 校验，
    HCS/FCS 不过直接解不开）；``OAD回显`` / ``数值`` 如实报——OAD 不回显或数值
    为空都是**观测**（步骤用 ``eq``/``has_value`` 判 fail），不是块态。
    """
    frames = frames if frames is not None else FramesCapability()
    oad = norm_hex(expect_oad)
    values: Dict[str, Any] = {"请求OAD": oad}
    frame_hex = norm_hex(reply_frame)
    if not frame_hex:
        return Observation(values=values, complete=False,
                           reason_code=OBS_INCOMPLETE, detail="没有上行帧 hex")

    decoded, protocol_used = decode_outer(frames, frame_hex)
    if decoded is None:
        return Observation(
            values=values, complete=False, reason_code=OBS_INCOMPLETE,
            detail="上行帧无法解析（既非 1376.2 也未通过自动嗅探）",
        )
    payload = _forwarded_payload(decoded)
    if not payload:
        return Observation(
            values=values, complete=False, reason_code=OBS_INCOMPLETE,
            detail="上行 02H-F1 里没有「转发报文」字段（不是数据转发应答）",
        )

    inner = frames.decode(payload, protocol="698.45")
    if not (isinstance(inner, Mapping) and inner.get("ok")):
        detail = inner.get("detail") if isinstance(inner, Mapping) else ""
        return Observation(
            values=values, complete=False, reason_code=OBS_INCOMPLETE,
            detail="内层转发报文无法按 698.45 解析：%s（%s…）"
            % (inner.get("error") if isinstance(inner, Mapping) else inner, payload[:32]),
        )
    inner_frame = inner.get("frame") or {}

    # 帧格式合法（facade 已验 HCS/FCS，解得开即合法；desc 如实报）
    fcs = _entry(inner_frame, "帧校验FCS") or {}
    hcs = _entry(inner_frame, "帧头校验HCS") or {}
    fcs_desc = str(fcs.get("desc") or "")
    values["帧格式合法"] = ("校验通过" in fcs_desc) or bool(fcs.get("raw"))
    values["FCS说明"] = fcs_desc or str(hcs.get("desc") or "")

    ctrl = _entry(inner_frame, "控制域C") or {}
    values["应答控制域C"] = norm_hex(ctrl.get("hex")) or ""
    apdu_type = _entry(inner_frame, "APDU类型") or {}
    apdu_hex = norm_hex(apdu_type.get("hex"))
    values["应答APDU"] = "%s(%s)" % (apdu_hex, apdu_type.get("value") or "")
    is_get_response = apdu_hex == "%02X" % APDU_GET_RESPONSE

    # OAD 回显 + 数值可解（facade 已把 OAD 译名/解值放进 items：hex=OAD、value=解出的数）
    oad_echo = False
    item: Optional[Mapping[str, Any]] = None
    for entry in _entries(inner_frame):
        if norm_hex(entry.get("hex")) == oad:
            oad_echo = True
            if entry.get("value") not in (None, ""):
                item = entry if item is None else item
    values["OAD回显"] = oad_echo
    if item is not None:
        values["数值"] = item.get("value")
        values["单位"] = str(item.get("unit") or "")
        values["数据项"] = str(item.get("name") or "")
    values["内层698帧"] = payload
    if not is_get_response:
        values.setdefault("数值", None)
        detail = "内层 698 应答 APDU=0x%s，不是 GET-Response(%02X)——观测如实报" % (
            apdu_hex or "??", APDU_GET_RESPONSE)
        return Observation(values=values, complete=True, detail=detail)
    return Observation(values=values, complete=True, detail="内层 698 应答已解析（观测完整）")


# --------------------------------------------------------------------------- #
# 块：meter.read_698
# --------------------------------------------------------------------------- #


def _invalid_param_result(detail: str, *, evidence: Any = ()) -> BlockResult:
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=detail,
        observation=Observation(
            values={}, complete=False, reason_code=OBS_INVALID_PARAM, detail=detail
        ),
        evidence_ids=[str(item) for item in (evidence or ()) if item],
    )


def meter_read_698(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``meter.read_698``：02H-F1 透传 698 GET-Request，只报观测不判定。

    判据出处与观测清单见模块头；「OAD 回显」「数值有值且可解」「否认/无应答怎么判」
    全部由步骤 expect 声明。等待预算 ``wait_s`` 由 Procedure 显式给（0044 §8.1-3，
    块缺省 0 = 只探一次兜底）；单次透传等待口径与 ``meter.read_645`` 一致
    （显式 ``expect_timeout`` > 03H-F7 取 t+5s > 回落 60s）。
    """
    action = dict(params or {})
    options = {
        key: value for key, value in block_params(action).items()
        if key not in (PARAM_ASSERT, "_cleanup")
    }
    frames = _capability(ctx, "frames", FramesCapability, options)

    addr, bad = meter_addr_of(action)
    if bad:
        return _invalid_param_result(
            "meter_addr 非法：%s（698 表通信地址需 12 位 hex，如 000000000001）——配置问题" % bad,
        )
    if not addr:
        return _invalid_param_result(
            "未解析到表地址 meter_addr（缺省取 case_defaults.meter_addr）——配置问题"
        )
    oad = norm_hex(options.get("oad")) or DEFAULT_OAD
    if len(oad) != 8:
        return _invalid_param_result(
            "oad 非法：%r（需 8 位 hex，如 %s）——配置问题" % (options.get("oad"), DEFAULT_OAD)
        )
    security = options.get("security", True)
    security = False if str(security).lower() in ("false", "0", "no") else bool(security)
    fe_preamble = options.get("fe_preamble", True)
    fe_preamble = False if str(fe_preamble).lower() in ("false", "0", "no") else bool(fe_preamble)
    try:
        piid = int(options.get("piid") or 1)
    except (TypeError, ValueError):
        return _invalid_param_result("piid 非法：%r" % (options.get("piid"),))

    from case_runner.blocks.meter import wait_params

    wait_s, interval_s, wait_error = wait_params(options)
    if wait_error:
        return _invalid_param_result("等待参数非法：%s" % wait_error)

    payload = norm_hex(options.get("payload"))
    if not payload:
        payload, reason = build_698_get_request(
            addr, oad, piid=piid, security=security, fe_preamble=fe_preamble, frames=frames,
        )
        if not payload:
            return _invalid_param_result(
                "698 读请求帧构帧不成功：%s" % reason, evidence=[oad]
            )

    simcon = _capability(ctx, "simcon", SimconCapability, options)
    timeout, timeout_note = resolve_expect_timeout(options, ctx, simcon=simcon, frames=frames)

    def attempt(attempt_timeout: float) -> Attempt:
        outcome = probe_forward_reply(
            simcon,
            payload,
            protocol_type=PROTOCOL_698_45,
            expect_timeout=attempt_timeout,
            max_poll_seconds=options.get("max_poll_seconds"),
            name="抄读表数据（02H-F1 透传 698 GET OAD %s）" % oad,
        )
        if outcome.state != "reply":
            from case_runner.blocks.meter import _outcome_to_observation

            return Attempt(
                _outcome_to_observation(outcome, extra="等待口径：%s" % timeout_note),
                frame=outcome.reply_frame,
            )
        observation = judge_read_698(outcome.reply_frame, expect_oad=oad, frames=frames)
        return Attempt(
            BlockResult(
                state=BLOCK_UNKNOWN,
                detail=observation.detail,
                observation=observation,
                evidence_ids=[payload, outcome.reply_frame],
            ),
            frame=outcome.reply_frame,
        )

    clock, sleeper = poll_hooks(options, simcon)
    loop = wait_attempts(
        attempt,
        wait_s=wait_s,
        interval_s=interval_s,
        expect_timeout=timeout,
        clock=clock,
        sleep=sleeper,
        stop_when=_got_observation,
    )
    return _finalize_observation(loop, wait_s=wait_s,
                                 what="上行 02H-F1（698 抄读 OAD %s）" % oad)


#: 块 id → 执行体（声明见 ``data/blocks/meter_read_698.json``）
BLOCKS: Dict[str, Any] = {
    "meter.read_698": meter_read_698,
}


def register() -> None:
    """注册本文件块执行体（幂等，与 ``blocks/__init__.py`` 约定一致）。"""
    for block_id, fn in BLOCKS.items():
        if not has_block(block_id):
            register_block(block_id, fn)


register()

__all__ = [
    "APDU_GET_REQUEST",
    "APDU_GET_RESPONSE",
    "APDU_SECURITY_REQUEST",
    "BLOCKS",
    "DEFAULT_OAD",
    "FE_PREAMBLE_HEX",
    "PROTOCOL_698_45",
    "build_698_get_apdu",
    "build_698_get_request",
    "judge_read_698",
    "meter_read_698",
    "register",
]
