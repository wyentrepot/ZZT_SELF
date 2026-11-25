"""并发抄表**入队前闸门**负向观测块（REQS-0043 F0-4）：``gate.multi_frame_110`` /
``gate.concurrent_109``。

两道闸门都在 CCO **收帧入队前**、与表是否应答无关（``CCO实现逻辑/02-并发抄表.md:133-156``），
所以单表台架即可触发（CASE-BACKLOG §F0-1 对照发现 / §F0-4 判定：负向判据的最佳落点）。

判据出处（逐条随代码保留）：

- **110 / 6E**＝645 类单帧报文条数 > 13（``READMETER_MAX=13``，
  ``02-并发抄表.md:84``；CheckMeter645Item 逐条验帧计数 ≥13 → 否认 110/6E）。
  多报文打包口径：一帧 = 规约类型(1B)+保留(1B)+报文长度(2B LE)+**N 条 645 帧拼接**
  （``03_QGDW10376.2_全帧类型.md`` §4.15 示例帧：报文长度 0x1E=30=2 条 15B 645 帧）。
  条数档缺省 **14**（13 是上限，14 触发）；报文 DI 缺省 ``03300501``——长报文口径
  （「每帧包含 7 条 07 报文（DI=03300501）…超过最大字节数 1024 的数据不会返回」，
  ``用例库/STA/台账/123_国网用例_台账.md:74`` 行 36 步骤 25；长报文「不返回」是
  业务行为、不产否认帧，**否认 6E 只看条数闸门**，两者口径都在此登记）。
- **109 / 6D**＝并发帧数超 CCO 队列上限（队列 20、台体送检 10，
  ``02-并发抄表.md:69,86,143-156``；``expect_rules.json:18`` 109=6D）。
  连发 **21** 帧背靠背压队列（每帧 1 条 645 报文），第 21 帧（或更早，视 CCO
  实际配置）应回否认 6D。帧数档可显式覆盖（``frames``）。
- 整体帧长 ≤ 2000B（``02-并发抄表.md:17/:80``）：打包超限属配置问题 → 原因码
  ``invalid_param``（未下发，绝不折成设备观测）。

块 = 传感器（0044 §4.2）：观测 = **是否收到 00H-F2 否认帧 + 否认码**
（``否认码`` / ``否认码文本`` / ``否认帧`` hex / 下发口径值）。「否认码应为 6E/6D」
是**用例层 expect**（``{"op":"eq","field":"否认码","value":"6E"}``）；等满预算没有
否认帧 → 原因码 ``timeout``（闸门没触发可能是产品行为，也可能是台架态，**说不清**，
绝不折算成任何结论——绝无假 PASS 通道）。

与 simcon 的关系（薄编排）：构帧走 simcon verify 的**语义化** send
（``afn=F1/fn=1/params={protocol,payload}``，``adapter_10376.encode_app_data``
即 §4.15 单头+N 条拼接格式）；否认码人话 simcon matcher 已解（``deny_info``，
REQS-0027 G2），步骤结果里带 ``deny`` / ``matched``，本块只搬运。109 的
「背靠背连发」用 verify 的**仅下发步**（无 expect = 不等待，``runner.run_step``
分支 3「仅下发」），最后一挂 ``recv_only`` 监听否认帧——中间步的 drain 不冲掉
末帧否认（drain 在 send **前**发生）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from case_runner.blocks.meter import (
    _capability,
    block_params,
    build_645_read_frame,
    meter_addr_of,
    norm_hex,
)
from case_runner.capabilities.frames import FramesCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.executor import PARAM_ASSERT, BlockResult
from case_runner.core.expect import (
    OBS_BENCH_UNREACHABLE,
    OBS_INVALID_PARAM,
    OBS_SERIAL_BUSY,
    OBS_TIMEOUT,
    Observation,
)
from case_runner.core.models import BLOCK_UNKNOWN
from case_runner.core.registry import has_block, register_block

#: 645 类单帧报文条数上限（``READMETER_MAX=13``，02-并发抄表.md:84）；缺省发 14 条触发
READMETER_MAX = 13
DEFAULT_COUNT_110 = READMETER_MAX + 1

#: CCO 并发队列上限（02-并发抄表.md:86、143-156；deep_app.MAX_CONCURRENT_CAP=20）
CONCURRENT_QUEUE_MAX = 20
DEFAULT_FRAMES_109 = CONCURRENT_QUEUE_MAX + 1

#: 长报文口径 DI（123_国网用例_台账.md:74 行 36 步骤 25）
DEFAULT_GATE_DI = "03300501"

#: 整体帧长上限（02-并发抄表.md:17/:80）；1376.2 帧头尾固定 15B（68+L2+C+R6+AFN+DT2+CS+16）
FRAME_LENGTH_LIMIT = 2000
_13762_OVERHEAD = 15

#: 否认等待缺省秒数：expect_rules default 档（查询/确认/设置类 5s；
#: 闸门在入队前、否认即时回，5s 足够；expect_rules.json:24-26）
DEFAULT_EXPECT_TIMEOUT_S = 5.0

#: F1H-F1 语义化 send 的协议类型（02H = DL/T 645-2007，全帧类型.md:1633）
_PROTOCOL_645_2007 = 2

#: 否认码 → expect_rules 人话的**块层兜底表**（只兜 simcon 未带 deny 说明时的底；
#: 权威口径在 ``sim_concentrator/expect_rules.json:18-20``，本表逐字对齐）
DENY_TEXTS: Dict[str, str] = {
    "6D": "超过最大并发帧数（109）",
    "6E": "超过单个1376.2帧最大允许的电表协议报文条数（110）",
    "6F": "正在抄读该表（111）",
}

_ACTION_META_KEYS = frozenset(
    {"afn", "fn", "params", "recipe", "semantic", "cleanup", "assert", "tags"}
)


# --------------------------------------------------------------------------- #
# 打包构帧器（F0-4 缺块①：多报文打包；纯函数，无 IO）
# --------------------------------------------------------------------------- #


def pack_645_payload(addr: Any, di: Any, count: int) -> Tuple[str, str]:
    """构「N 条 645 读帧拼接」的报文内容 hex（§4.15 报文内容段，不含 4B 头）。

    头（规约类型+保留+长度 2B LE）由 simcon 语义构帧补（``encode_app_data``
    (0xF1,1) 分支），这里只产拼接报文。每条 645 帧必须 CS 合法——CCO 逐条验帧
    计数（CheckMeter645Item），坏帧不计条数就触发不了 110。
    返回 ``(payload_hex, 失败原因)``。
    """
    frames: List[str] = []
    for _ in range(max(int(count), 1)):
        frame, reason = build_645_read_frame(addr, di)
        if not frame:
            return "", "第 %d 条 645 帧构帧不成功：%s" % (len(frames) + 1, reason)
        frames.append(frame)
    payload_hex = "".join(frames)
    total = len(payload_hex) // 2 + 4 + _13762_OVERHEAD
    if total > FRAME_LENGTH_LIMIT:
        return "", "打包后整体帧长 %dB 超上限 %dB（%d 条 × %dB + 头 4B + 帧头尾 15B）" % (
            total, FRAME_LENGTH_LIMIT, count, len(frames[0]) // 2)
    return payload_hex, ""


# --------------------------------------------------------------------------- #
# 观测组装
# --------------------------------------------------------------------------- #


def _invalid(detail: str, *, evidence: Any = ()) -> BlockResult:
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=detail,
        observation=Observation(
            values={}, complete=False, reason_code=OBS_INVALID_PARAM, detail=detail
        ),
        evidence_ids=[str(item) for item in (evidence or ()) if item],
    )


def _env_error(result: Any, what: str) -> BlockResult:
    """simcon verify 错误码 → 原因码观测（映射与 blocks/meter.py 同口径）。"""
    error = str((result or {}).get("error") or "")
    detail = "%s：%s" % (what, (result or {}).get("detail") or error or "未知错误")
    if error == "http_409":
        code, text = OBS_SERIAL_BUSY, "%s（闸门/会话忙 409）" % detail
    elif error in ("no_base_url", "unreachable", "http_404", "transport_error") \
            or error.startswith("http_5"):
        code, text = OBS_BENCH_UNREACHABLE, detail
    elif error in ("poll_timeout",) or error.startswith("job_"):
        code, text = OBS_TIMEOUT, detail
    else:
        code, text = OBS_INVALID_PARAM, "%s（请求被拒 %s）" % (detail, error)
    observation = Observation(values={}, complete=False, reason_code=code, detail=text)
    return BlockResult(state=BLOCK_UNKNOWN, detail=text, observation=observation)


def _frame_entries(frame: Any) -> List[Mapping[str, Any]]:
    """facade 解码结构的 fields/items 摊平（与 blocks/meter.py 的取项口径一致）。"""
    entries: List[Mapping[str, Any]] = []
    if not isinstance(frame, Mapping):
        return entries
    for key in ("fields", "items"):
        value = frame.get(key)
        if isinstance(value, list):
            entries.extend(entry for entry in value if isinstance(entry, Mapping))
    return entries


def _deny_from_frame(frames: Any, frame_hex: str) -> Tuple[str, str]:
    """否认帧 hex → ``(否认码 hex, 否认码文本)``；解不出 → ``("", "")``。

    优先用 facade 解出的「错误状态字」数据项（``matcher.deny_info`` 同源；
    ``deny_info`` 的适配器 items 里 hex='6E'），文本兜底走块层 :data:`DENY_TEXTS`。
    """
    if not frame_hex:
        return "", ""
    decoded = frames.decode(frame_hex, protocol="1376.2")
    frame = decoded.get("frame") if isinstance(decoded, Mapping) else None
    for entry in _frame_entries(frame):
        if str(entry.get("name") or "") != "错误状态字":
            continue
        code = norm_hex(entry.get("hex"))
        text = str(entry.get("value") or "") or DENY_TEXTS.get(code, "")
        return code, text
    return "", ""


def _deny_from_steps(steps: Any, frames: Any) -> Tuple[str, str, str]:
    """verify 步骤结果 → ``(否认码, 否认码文本, 否认帧 hex)``；没收到 → 全空。

    步骤结果里 deny 说明（simcon matcher 产物）优先，其次回解 matched 帧。
    """
    for step in steps or []:
        if not isinstance(step, Mapping):
            continue
        matched = norm_hex(step.get("matched"))
        deny = step.get("deny")
        if isinstance(deny, Mapping) and deny.get("code_hex"):
            code = norm_hex(deny.get("code_hex"))
            text = str(deny.get("text") or "") or DENY_TEXTS.get(code, "")
            return code, text, matched
        if not matched:
            continue
        # 否认帧可能在「未匹配」步骤的 matched 里也留过痕——都解一遍再筛
        code, text = _deny_from_frame(frames, matched)
        if code:
            return code, text, matched
    return "", "", ""


def _gate_observation(
    *,
    values: Dict[str, Any],
    deny: Tuple[str, str, str],
    what: str,
) -> BlockResult:
    """闸门块统一出口：收到否认帧 → 完整观测；没有 → ``timeout``（说不清）。"""
    code, text, frame_hex = deny
    if code:
        values["否认码"] = code
        values["否认码文本"] = text or DENY_TEXTS.get(code, "")
        values["否认帧"] = frame_hex
        detail = "%s：收到否认帧，否认码 %s（%s）" % (what, code, values["否认码文本"])
        observation = Observation(values=values, complete=True, detail=detail)
        return BlockResult(state=BLOCK_UNKNOWN, detail=detail, observation=observation)
    detail = "%s：预算内未收到 00H-F2 否认帧（闸门未触发或台架态不符——说不清，不判结论）" % what
    observation = Observation(values=values, complete=False, reason_code=OBS_TIMEOUT,
                              detail=detail)
    return BlockResult(state=BLOCK_UNKNOWN, detail=detail, observation=observation)


def _gate_options(params: Mapping[str, Any]) -> Dict[str, Any]:
    action = dict(params or {})
    return {
        key: value for key, value in block_params(action).items()
        if key not in (PARAM_ASSERT, "_cleanup")
    }


def _gate_addr(options: Mapping[str, Any]) -> Tuple[str, Optional[BlockResult]]:
    addr, bad = meter_addr_of(dict(options))
    if bad:
        return "", _invalid("meter_addr 非法：%s（需 12 位 BCD 数字）——配置问题" % bad)
    if not addr:
        return "", _invalid(
            "未解析到表地址 meter_addr（闸门与表无关，但 645 验帧计数需要合法帧——"
            "地址取 case_defaults.meter_addr 或显式给出）"
        )
    return addr, None


def _gate_int(options: Mapping[str, Any], key: str, default: int,
              low: int, high: int, *, why: str) -> Tuple[int, Optional[BlockResult]]:
    raw = options.get(key)
    if raw in (None, ""):
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0, _invalid("参数 %s 非法：%r（应 %d~%d 的整数）" % (key, raw, low, high))
    if not low <= value <= high:
        return 0, _invalid("参数 %s 超界：%s（允许 %d~%d：%s）" % (key, value, low, high, why))
    return value, None


def _gate_timeout(options: Mapping[str, Any]) -> Tuple[float, Optional[BlockResult]]:
    raw = options.get("expect_timeout")
    if raw in (None, ""):
        return DEFAULT_EXPECT_TIMEOUT_S, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0, _invalid("expect_timeout 非法：%r（应 >0 秒数）" % (raw,))
    if value <= 0:
        return 0.0, _invalid("expect_timeout 非法：%r（应 >0 秒数）" % (raw,))
    return value, None


def _gate_di(options: Mapping[str, Any]) -> Tuple[str, Optional[BlockResult]]:
    di = norm_hex(options.get("di")) or DEFAULT_GATE_DI
    if len(di) != 8:
        return "", _invalid("di 非法：%r（需 8 位 hex，如 %s）" % (options.get("di"), DEFAULT_GATE_DI))
    return di, None


def _poll_budget(steps_total: int, expect_timeout: float) -> float:
    """verify 轮询总预算：仅下发步很快，给「步数 × 1s + 否认等待 + 30s」兜底。"""
    return float(steps_total) + expect_timeout + 30.0


# --------------------------------------------------------------------------- #
# 块：gate.multi_frame_110（超单帧条数否认 6E）
# --------------------------------------------------------------------------- #


def gate_multi_frame_110(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``gate.multi_frame_110``：一帧打包 14 条 645 报文 → 观测 CCO 否认 6E。

    观测值：``下发条数`` / ``报文DI`` / ``单帧报文长度B`` / ``数据单元长度B`` /
    ``否认等待秒`` / ``否认码`` / ``否认码文本`` / ``否认帧``。「否认码 = 6E」
    由步骤 expect 声明；没等到否认帧 → 原因码 ``timeout``（绝不折算结论）。
    判据出处见模块头（02-并发抄表.md:84 / 全帧类型.md §4.15 / 123 台账:74）。
    """
    options = _gate_options(params)
    addr, bad = _gate_addr(options)
    if bad:
        return bad
    di, bad = _gate_di(options)
    if bad:
        return bad
    count, bad = _gate_int(
        options, "count", DEFAULT_COUNT_110, 1, 100,
        why="645 类单帧上限 13 条（READMETER_MAX），>13 触发否认 110/6E",
    )
    if bad:
        return bad
    expect_timeout, bad = _gate_timeout(options)
    if bad:
        return bad

    payload_hex, reason = pack_645_payload(addr, di, count)
    if not payload_hex:
        return _invalid("多报文打包构帧未成：%s——配置问题（未下发）" % reason,
                        evidence=[di])

    frames_cap = _capability(ctx, "frames", FramesCapability, options)
    simcon = _capability(ctx, "simcon", SimconCapability, options)
    steps = [{
        "name": "多帧打包下发（%d 条 645，DI %s）" % (count, di),
        "send": {"afn": "F1", "fn": 1,
                 "params": {"protocol": _PROTOCOL_645_2007, "payload": payload_hex}},
        "expect": {"afn": "00", "fn": 2, "dir": "up"},
        "expect_timeout": expect_timeout,
    }]
    result = simcon.verify(steps, max_poll_seconds=_poll_budget(1, expect_timeout))
    if not isinstance(result, Mapping):
        return _env_error({"error": "bad_response",
                           "detail": "verify 返回 %s" % type(result).__name__}, "闸门下发")
    deny = _deny_from_steps(result.get("steps"), frames_cap)
    data_unit_len = len(payload_hex) // 2 + 4
    values: Dict[str, Any] = {
        "下发条数": count,
        "报文DI": di,
        "单帧报文长度B": len(payload_hex) // 2 // max(count, 1),
        "数据单元长度B": data_unit_len,
        "否认等待秒": expect_timeout,
    }
    if not deny[0] and str(result.get("error") or "") not in ("", "job_not_passed"):
        return _env_error(result, "多帧闸门下发")
    return _gate_observation(values=values, deny=deny,
                             what="多帧条数闸门（%d 条/帧）" % count)


# --------------------------------------------------------------------------- #
# 块：gate.concurrent_109（超并发帧数否认 6D）
# --------------------------------------------------------------------------- #


def gate_concurrent_109(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``gate.concurrent_109``：连发 21 帧并发抄表 → 观测 CCO 否认 6D。

    步骤编排：``frame_count`` 个「仅下发」步（无 expect，背靠背压队列，每帧 1 条
    645 报文）+ 1 个 ``recv_only`` 否认监听步。观测值：``下发帧数`` / ``每帧条数`` /
    ``报文DI`` / ``否认等待秒`` / ``否认码`` / ``否认码文本`` / ``否认帧``。
    判据出处见模块头（02-并发抄表.md:86,143-156 / expect_rules.json:18）。
    """
    options = _gate_options(params)
    addr, bad = _gate_addr(options)
    if bad:
        return bad
    di, bad = _gate_di(options)
    if bad:
        return bad
    # 参数名用 frame_count（不用 frames）：frames 是能力注入键
    # （_capability(ctx, "frames", …)），同名会撞——11H-F5「不设隐式默认」教训的反面：
    # 参数名不得与能力键冲突。
    frame_count, bad = _gate_int(
        options, "frame_count", DEFAULT_FRAMES_109, 1, 64,
        why="CCO 并发队列上限 20（台体送检 10），>20 触发否认 109/6D",
    )
    if bad:
        return bad
    expect_timeout, bad = _gate_timeout(options)
    if bad:
        return bad

    payload_hex, reason = pack_645_payload(addr, di, 1)
    if not payload_hex:
        return _invalid("并发帧构帧未成：%s——配置问题（未下发）" % reason, evidence=[di])

    frames_cap = _capability(ctx, "frames", FramesCapability, options)
    simcon = _capability(ctx, "simcon", SimconCapability, options)
    send = {"afn": "F1", "fn": 1,
            "params": {"protocol": _PROTOCOL_645_2007, "payload": payload_hex}}
    steps: List[Dict[str, Any]] = []
    for index in range(frame_count):
        # 无 expect = 仅下发不等待（runner.run_step 分支 3）：并发帧要背靠背进队列
        steps.append({
            "name": "并发帧 %d/%d（仅下发）" % (index + 1, frame_count),
            "send": dict(send),
        })
    # 末帧否认不会被中间步的 drain 冲掉（drain 在 send 前）；recv_only 不 send 不 drain
    steps.append({
        "name": "否认帧监听（00H-F2）",
        "recv_only": True,
        "expect": {"afn": "00", "fn": 2, "dir": "up"},
        "expect_timeout": expect_timeout,
    })
    result = simcon.verify(steps, max_poll_seconds=_poll_budget(len(steps), expect_timeout))
    if not isinstance(result, Mapping):
        return _env_error({"error": "bad_response",
                           "detail": "verify 返回 %s" % type(result).__name__}, "闸门下发")
    deny = _deny_from_steps(result.get("steps"), frames_cap)
    values: Dict[str, Any] = {
        "下发帧数": frame_count,
        "每帧条数": 1,
        "报文DI": di,
        "否认等待秒": expect_timeout,
    }
    if not deny[0] and str(result.get("error") or "") not in ("", "job_not_passed"):
        return _env_error(result, "并发帧闸门下发")
    return _gate_observation(values=values, deny=deny,
                             what="并发帧数闸门（连发 %d 帧）" % frame_count)


#: 块 id → 执行体（声明见 ``data/blocks/gate_*.json``）
BLOCKS: Dict[str, Any] = {
    "gate.multi_frame_110": gate_multi_frame_110,
    "gate.concurrent_109": gate_concurrent_109,
}


def register() -> None:
    """注册本文件块执行体（幂等，与 ``blocks/__init__.py`` 约定一致）。"""
    for block_id, fn in BLOCKS.items():
        if not has_block(block_id):
            register_block(block_id, fn)


register()

__all__ = [
    "BLOCKS",
    "CONCURRENT_QUEUE_MAX",
    "DEFAULT_COUNT_110",
    "DEFAULT_EXPECT_TIMEOUT_S",
    "DEFAULT_FRAMES_109",
    "DEFAULT_GATE_DI",
    "DENY_TEXTS",
    "FRAME_LENGTH_LIMIT",
    "READMETER_MAX",
    "gate_concurrent_109",
    "gate_multi_frame_110",
    "pack_645_payload",
    "register",
]
