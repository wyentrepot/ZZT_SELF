"""验证任务执行器：下发 → 接收 → 匹配 → 解析 → 判定闭环。

一个验证任务（VerifyTask）由若干步骤组成，每步：
- 构造并下发一帧（send）；支持 CCO 本地协议（send.format="local"）。
- 可选：期望收到一帧并匹配（expect）。
- 可选：recv_only 步骤（不 send，只等待并匹配一帧——用于验证 CCO 主动上报）。
- 可选：expect_history 步骤（在超时内轮询历史帧，确认是否出现过某类上报）。
- 可选：该步骤期望无响应（expect_no_reply）。

应答引擎在任务执行期间挂载（内置 + 任务覆盖规则）：收到的每一帧若命中
应答规则，立即自动应答（统一回 00H-F1 确认），从而验证"模块上行 → 模拟
集中器应答"闭环；同一帧仍可被 expect / expect_history 匹配验证。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import nullcontext
from datetime import datetime
from typing import Dict, List, Optional

from sim_concentrator.frame_codec import (
    build_13762_frame,
    build_local_13762_frame,
    decode_frame,
    frame_to_hex,
    hex_to_bytes,
)
from sim_concentrator.matcher import deny_info, match_frame
from sim_concentrator.responder import Responder
from sim_concentrator.serial_io import SerialIO, resolve_serial_config
from sim_concentrator.scenario_codec import (
    ScenarioCodecError,
    build_send,
    load_profile,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 帧构造参数解析
# ---------------------------------------------------------------------------
def _to_int(v, base: int = 16):
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, base)
    return v


def build_send_frame(send: Optional[dict] = None,
                     profile: Optional[dict] = None,
                     seq: int = 1) -> bytes:
    """按 send 参数构造一帧（语义化主路径，ADR-5）。

    send = {
        # 语义化（默认）：afn/fn + 最小业务参数，经 scenario_codec + 13762 库构帧
        "afn": "11" | 0x11,
        "fn": "F231" | 231,
        "params": {...},        # 该 AFN/Fn 的数据单元字段
        "direction": "down" | "up",
        "comm_mode": 3,          # 可选，覆盖 profile
    }

    profile：task.profile 加载的全局信息（cco_addr / sta_archives / comm_mode）。
    seq：报文序列号（seq_auto=true 时注入 info.seq）。

    send.raw 整帧 hex 直发已彻底移除（契约约定，ADR-5），传入即报错。
    """
    send = send or {}

    # raw 整帧直发：契约已移除
    if send.get("raw"):
        raise ScenarioCodecError(
            "send.raw 已移除（ADR-5 用例语义化），请改用 afn/fn + params 描述命令")

    if send.get("format") == "local":
        # 兼容旧结构（迁移期保留，迁移完成删除）：format:"local" + afn/fn/buff
        afn = _to_int(send.get("afn", 0x00))
        fn = _to_int(send.get("fn", 1), 10)
        bf = send.get("buff", b"")
        if isinstance(bf, str):
            buff = hex_to_bytes(bf)
        elif isinstance(bf, list):
            buff = bytes(bf)
        else:
            buff = bytes(bf or b"")
        ctrl = _to_int(send.get("ctrl", 0x43))
        seq = _to_int(send.get("seq", 1), 10)
        info = bytes(send.get("info", [0] * 6))
        return build_local_13762_frame(afn=afn, fn=fn, buff=buff,
                                       ctrl=ctrl, info=info, seq=seq)

    # 语义化主路径：profile + params → 完整帧
    return build_send(send, profile or {}, seq=seq)


def _rtsa_to_show(rtsa: bytes) -> str:
    return rtsa[::-1].hex().upper()


# ---------------------------------------------------------------------------
# 单步执行
# ---------------------------------------------------------------------------
def run_step(io: SerialIO, responder: Optional[Responder],
             step: dict, idx: int, profile: Optional[dict] = None,
             seq: Optional[int] = None) -> dict:
    """执行一步，返回判定结果 dict。

    profile：task.profile 加载的全局信息（传给 build_send_frame 构帧）。
    seq：构帧时 info.seq 覆盖值（缺省用 idx+1；单步下发由调用方传会话内递增序号）。
    """
    name = step.get("name", f"步骤{idx + 1}")
    result = {
        "index": idx,
        "name": name,
        "sent_hex": "",
        "matched": None,
        "parsed": None,
        "result": "fail",
        "reason": "",
    }

    recv_only = step.get("recv_only", False)
    expect_history = step.get("expect_history", False)
    timeout = step.get("expect_timeout", 5.0)
    expect = step.get("expect")
    is_query = (expect is not None) and not expect_history

    # 1) 构造并下发（recv_only 跳过；expect_history 且无 send 时不构帧，纯历史扫描）
    if not recv_only and not (expect_history and not step.get("send")):
        try:
            raw = build_send_frame(step.get("send", {}), profile=profile,
                                   seq=seq if seq is not None else idx + 1)
        except Exception as e:
            result["reason"] = f"构帧失败: {e!r}"
            return result
        result["sent_hex"] = frame_to_hex(raw)
        try:
            io.send_frame(raw)
        except Exception as e:
            result["reason"] = f"发送失败: {e!r}"
            return result

    # 2) 期望无响应
    expect_no_reply = step.get("expect_no_reply", False)
    if expect_no_reply:
        got = io.recv_frame(timeout=timeout)
        if got is None:
            result["result"] = "pass"
            result["reason"] = "期望无响应，符合"
        else:
            result["matched"] = frame_to_hex(got)
            result["parsed"] = _safe_decode(got)
            result["reason"] = "期望无响应，但收到帧"
        return result

    # 3) 无期望：仅下发/仅等待（recv_only 且无 expect 时，等待任意一帧）
    if expect is None and not expect_history:
        if recv_only:
            got = io.recv_frame(timeout=timeout)
            if got is None:
                result["reason"] = f"等待帧超时({timeout}s)"
                return result
            result["matched"] = frame_to_hex(got)
            result["parsed"] = _safe_decode(got)
            result["result"] = "pass"
            result["reason"] = "收到一帧"
            _auto_reply(io, responder, got)
            return result
        result["result"] = "pass"
        result["reason"] = "仅下发，无接收断言"
        return result

    # 4) expect_history：在超时内轮询历史帧，确认出现过匹配帧
    # 语义：rx_history() 是只读累积记录（读线程收到即入历史），匹配**不消费**历史帧，
    #       只回答"超时窗口内是否出现过该帧"。因此：
    #       - 与 recv_only 组合：recv_only 只影响是否 send（本分支本身不 send），
    #         两者语义等价于"只收不发的历史扫描"，可安全叠加。
    #       - 重复执行同一 history 匹配不会重复应答：一旦命中即返回，只 _auto_reply 一次。
    if expect_history:
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            for hf in io.rx_history():
                matched, decoded, reasons = match_frame(hf, expect)
                if matched:
                    result["matched"] = frame_to_hex(hf)
                    result["parsed"] = decoded
                    result["result"] = "pass"
                    result["reason"] = "历史帧匹配成功" + (f"：{'; '.join(reasons)}" if reasons else "")
                    _auto_reply(io, responder, hf)
                    return result
            seen = io.rx_history()
            time.sleep(0.05)
        result["reason"] = f"超时({timeout}s)，历史中未出现期望帧；历史帧数={len(seen)}"
        return result

    # 5) 期望接收一帧（主动下发后等待 CCO 回复；recv_only 则直接等待上报）
    deadline = time.time() + timeout
    bystanders = []  # REQS-0027：窗口内不匹配的旁听帧（CCO 插播主动上报等）
    while time.time() < deadline:
        got = io.recv_frame(timeout=max(0.05, deadline - time.time()))
        if got is None:
            continue
        result["matched"] = frame_to_hex(got)
        result["parsed"] = _safe_decode(got)
        # 先自动应答（若命中规则），再匹配 expect
        _auto_reply(io, responder, got)
        # 否认帧特判（REQS-0027 G2）：预期确认类应答（AFN=00H）时，到达的
        # 00H-F2 否认帧（FN=2）不满足 fn=1 的字面匹配，但业务上必须立即判定失败
        d = deny_info(_safe_decode(got))
        if d is not None and expect.get("afn") == 0x00:
            result["parsed"] = _safe_decode(got)
            result["deny"] = d
            result["result"] = "fail"
            result["reason"] = f"否认（{d['text']}）"
            return result
        matched, decoded, reasons = match_frame(got, expect)
        result["parsed"] = decoded
        if matched:
            result["result"] = "pass"
            result["reason"] = "匹配成功" + (f"：{'; '.join(reasons)}" if reasons else "")
            d2 = deny_info(decoded)
            if d2 is not None:
                # 00H-F2 否认帧：匹配命中但业务失败，附否认码人话（result 仍为 fail，
                # 上层按 result["deny"]["code"] 细分失败口径）
                result["deny"] = d2
                result["result"] = "fail"
                result["reason"] = f"否认（{d2['text']}）"
            return result
        # 不匹配：跳过继续等（CCO 会插播主动上报帧，如 10H-F1 从节点数量，
        # 需跳过直至出现期望帧或超时）。记录旁听帧便于诊断。
        result["received_hex"] = (result.get("received_hex", []) or [])
        result["received_hex"].append(frame_to_hex(got))
        bystanders.append({
            "hex": frame_to_hex(got),
            "afn": (f"{_afn_of(decoded):02X}" if _afn_of(decoded) is not None else None),
            "fn": _fn_of(decoded),
        })
        # 若 recv_only 也继续等；send+expect 同样跳过无关帧（修复：真实 CCO 插播主动帧）
    rx_count = len(result.get("received_hex", []) or [])
    result["reason"] = (f"超时({timeout}s)未收到期望帧"
                        + (f"，期间收到 {rx_count} 帧未匹配" if rx_count else ""))
    if bystanders:
        result["bystanders"] = bystanders
    return result


def _afn_of(decoded: dict):
    if isinstance(decoded.get("afn"), int):
        return decoded["afn"]
    raw = decoded.get("fields", {}).get("AFN", {}).get("raw")
    return raw if isinstance(raw, int) else None


def _fn_of(decoded: dict):
    if isinstance(decoded.get("fn"), int):
        return decoded["fn"]
    raw = decoded.get("fields", {}).get("FN", {}).get("raw")
    return raw if isinstance(raw, int) else None


def _safe_decode(raw: bytes) -> dict:
    try:
        return decode_frame(raw)
    except Exception:
        from sim_concentrator.frame_codec import decode_local_13762_frame
        try:
            return decode_local_13762_frame(raw)
        except Exception:
            return {"raw_hex": raw.hex()}


def _auto_reply(io: Optional[SerialIO], responder: Optional[Responder], raw: bytes) -> None:
    """若命中应答规则，自动回帧（模拟集中器应答 CCO 主动上报）。

    异常不中断执行流程，但会记录日志以便排查（不再静默吞掉）。
    """
    if responder is None or io is None:
        return
    try:
        reply = responder.reply_for(raw)
    except Exception:
        logger.exception("responder.reply_for 异常，已跳过自动应答；帧=%s",
                         raw.hex()[:64])
        return
    if reply is not None:
        journal = getattr(io, "journal", None)
        try:
            if journal is not None:
                with journal.scope(None, "auto_reply"):
                    io.send_frame(reply)
            else:
                io.send_frame(reply)
        except Exception:
            logger.exception("自动应答发送失败；帧=%s 应答=%s",
                             raw.hex()[:64], reply.hex()[:64])


# ---------------------------------------------------------------------------
# 任务级执行
# ---------------------------------------------------------------------------
def execute_task(task: dict, io: Optional[SerialIO] = None, *,
                 journal=None) -> dict:
    """执行整个验证任务，返回完整结论 JSON。

    若 io 未提供，则按 task 的 port/baudrate 自建串口并独占打开。
    journal：会话帧日志（FrameJournal，可选）；io 自带 journal 时优先用 io 的。
    任务期间产生的所有 tx/rx 帧以 run_id 打标，响应附带 frames_seq 区间。
    """
    steps = task.get("steps", [])
    own_io = io is None
    port_identity = getattr(io, "port_identity", None) if io is not None else None
    mapping_id = ""
    if own_io:
        resolved = resolve_serial_config(
            task.get("port"),
            mapping_id=task.get("mapping_id"),
            baudrate=task.get("baudrate"),
            bytesize=task.get("bytesize"),
            parity=task.get("parity"),
            stopbits=task.get("stopbits"),
        )
        port = resolved["port"]
        baudrate = resolved["baudrate"]
        mapping_id = resolved["mapping_id"]
        port_identity = resolved["port_identity"]
        io = SerialIO(
            port=port,
            baudrate=baudrate,
            bytesize=resolved["bytesize"],
            parity=resolved["parity"],
            stopbits=resolved["stopbits"],
            port_identity=port_identity,
            journal=journal,
        )
    else:
        port = task.get("port") or getattr(io, "port", "COM3")
        baudrate = task.get("baudrate") or getattr(io, "baudrate", 115200)
        mapping_id = str((port_identity or {}).get("mapping_id", ""))
    journal = journal or getattr(io, "journal", None)
    run_id = f"run-{task.get('id', 'verify.task')}-{uuid.uuid4().hex[:8]}"
    start_seq = journal.last_seq if journal is not None else 0

    responder = Responder(override_rules=task.get("responders", [])) \
        if task.get("enable_responder", True) else None

    # 语义化构帧：加载 task.profile 指向的全局信息（ADR-5）
    profile = load_profile(task.get("profile"))
    if profile and task.get("profile_overrides"):
        profile = {**profile, **task["profile_overrides"]}

    opened = False
    try:
        if own_io:
            io.open()
            opened = True

        scope_ctx = journal.scope(run_id, "step_send") if journal is not None else nullcontext()
        with scope_ctx:
            step_results = []
            seq_counter = 0
            for idx, step in enumerate(steps):
                # 若本步声明了自有 responder，则临时挂载；否则用任务级 responder
                step_r = responder
                if step.get("responders"):
                    step_r = Responder(override_rules=step["responders"])
                # 兼容旧结构：本地协议下行帧自动分配递增帧序号
                # （对齐 GW-CASS，CCO 响应回显 serial_num）
                step = dict(step)
                if step.get("send") and step["send"].get("format") == "local":
                    send = dict(step["send"])
                    if "seq" not in send:
                        seq_counter += 1
                        send["seq"] = seq_counter
                    step["send"] = send
                r = run_step(io, step_r, step, idx, profile=profile)
                step_results.append(r)
                # 任一步失败即中止（默认），除非 task.fail_fast=false
                if r["result"] == "fail" and task.get("fail_fast", True):
                    break

        pass_count = sum(1 for s in step_results if s["result"] == "pass")
        fail_count = sum(1 for s in step_results if s["result"] == "fail")
        verdict = "pass" if fail_count == 0 and step_results else "fail"

        result = {
            "task_id": task.get("id", "verify.task"),
            "port": port,
            "baudrate": baudrate,
            "mapping_id": mapping_id,
            "port_identity": port_identity,
            "steps": step_results,
            "summary": {
                "total": len(step_results),
                "pass": pass_count,
                "fail": fail_count,
                "verdict": verdict,
            },
        }
        if journal is not None:
            result["session_id"] = journal.session_id
            result["run_id"] = run_id
            result["frames_seq"] = (
                [start_seq + 1, journal.last_seq] if journal.last_seq > start_seq else []
            )
        return result
    finally:
        if own_io and opened:
            io.close()


def run_single_step(io: SerialIO, *, send: Optional[dict] = None,
                    profile: Optional[dict] = None,
                    expect: Optional[dict] = None,
                    expect_timeout: Optional[float] = None,
                    expect_no_reply: bool = False,
                    recv_only: bool = False,
                    enable_responder: bool = True,
                    name: str = "单步",
                    seq: Optional[int] = None,
                    run_id: Optional[str] = None,
                    auto_expect: bool = True) -> dict:
    """单步语义执行（AI 单步下发 / 感知主动上报，ADR-5 语义）。

    - send：afn/fn + params（raw 整帧已移除，传入即报错）；
    - recv_only=True：不下发，只等待并匹配一帧（等 CCO 主动上报）；
    - send 与 recv_only 二选一；expect / expect_no_reply 同 run_step 语义。
    - auto_expect（REQS-0027 G2）：expect 未显式给出时按 expect_rules 自动生成
      默认 expect，超时未指定时按 per-Fn 档位取值（单抄 59s/并抄 99s/默认 5s）；
      显式 expect / expect_timeout 优先（G2 契约：显式可覆盖）。
    帧以 kind=manual_send、run_id 打入会话帧日志。
    """
    if not send and not recv_only:
        raise ScenarioCodecError("单步必须提供 send 或 recv_only=true（等待一帧）")
    if send and recv_only:
        raise ScenarioCodecError("send 与 recv_only 只能二选一")
    if send and send.get("raw"):
        raise ScenarioCodecError("send.raw 已移除（ADR-5 用例语义化），请改用 afn/fn + params")

    pairing: Optional[dict] = None
    resolved_timeout = expect_timeout
    resolved_expect = expect
    if auto_expect and send and expect is None and not recv_only and not expect_no_reply:
        from sim_concentrator import expect_rules
        afn = send.get("afn")
        fn = send.get("fn")
        gen_expect, rule = expect_rules.default_expect(afn, fn)
        if gen_expect is not None:
            resolved_expect = gen_expect
            meta = expect_rules.timeout_for(afn, fn)
            if resolved_timeout is None:
                resolved_timeout = meta["seconds"]
            pairing = {
                "rule_id": rule.get("id"),
                "desc": rule.get("desc"),
                "expect": gen_expect,
                "form": gen_expect.get("form"),
                "timeout": meta,
                "expect_source": "auto",
            }
        elif expect_rules.is_report_afn(afn):
            pairing = {
                "rule_id": None,
                "desc": "主动上报 AFN：无预期应答（仅下发确认）",
                "expect": None,
                "form": None,
                "timeout": None,
                "expect_source": "none",
            }
    if resolved_timeout is None:
        resolved_timeout = 5.0

    step = {
        "name": name,
        "send": send,
        "expect": resolved_expect,
        "expect_timeout": resolved_timeout,
        "expect_no_reply": expect_no_reply,
        "recv_only": recv_only,
    }
    responder = Responder() if enable_responder else None
    journal = getattr(io, "journal", None)
    resolved_run_id = run_id or (
        f"manual-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    start_seq = journal.last_seq if journal is not None else 0
    scope_ctx = journal.scope(resolved_run_id, "manual_send") if journal is not None else nullcontext()
    with scope_ctx:
        result = run_step(io, responder, step, 0, profile=profile, seq=seq)
    if pairing is not None:
        result["pairing"] = pairing
        result["bystanders"] = result.get("bystanders", [])
    out = {"run_id": resolved_run_id, "step": result}
    if journal is not None:
        out["session_id"] = journal.session_id
        out["frames_seq"] = (
            [start_seq + 1, journal.last_seq] if journal.last_seq > start_seq else []
        )
    return out


def load_task(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
