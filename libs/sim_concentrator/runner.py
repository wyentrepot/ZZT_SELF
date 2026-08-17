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
from typing import Dict, List, Optional

from sim_concentrator.frame_codec import (
    build_13762_frame,
    build_local_13762_frame,
    decode_frame,
    frame_to_hex,
    hex_to_bytes,
)
from sim_concentrator.matcher import match_frame
from sim_concentrator.responder import Responder
from sim_concentrator.serial_io import SerialIO

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


def build_send_frame(send: Optional[dict] = None) -> bytes:
    """按 send 参数构造一帧。

    send = {
        # 标准 1376.2（双 68）：
        "afn": 0x02 | "02",
        "seq": 1,
        "rtsa": "070919051620" | [0x20,0x16,...],   # 人读顺序 hex 或字节列表
        "msaa": 1,
        "pw": 0,
        "userdata": "00 01 68..." | "000168..." | [bytes],

        # CCO 本地协议（单 68），format="local"：
        "format": "local",
        "afn": 0x10,                 # AFN 码
        "fn": 230,                   # Fn 码（自动编码为 DT1/DT2）
        "buff": "00 01" | [0x00,0x01],   # 数据区（可选）
        "ctrl": 0x03,                # 控制域（可选，默认 0x03 下行宽带载波）
        "info": [0]*6,               # 信息域 6B（可选）
    }

    未指定 format 时默认标准 1376.2。
    """
    send = send or {}

    if send.get("format") == "local":
        afn = _to_int(send.get("afn", 0x00))
        fn = _to_int(send.get("fn", 1), 10)
        bf = send.get("buff", b"")
        if isinstance(bf, str):
            buff = hex_to_bytes(bf)
        elif isinstance(bf, list):
            buff = bytes(bf)
        else:
            buff = bytes(bf or b"")
        # 下行查询：模拟集中器作为启动站下发，prm=1，ctrl=0x43
        # （对齐 GW-CASS Creat_3762_Frame('43',...)；默认不再是 0x03 从动站）
        ctrl = _to_int(send.get("ctrl", 0x43))
        seq = _to_int(send.get("seq", 1), 10)
        info = bytes(send.get("info", [0] * 6))
        return build_local_13762_frame(afn=afn, fn=fn, buff=buff,
                                       ctrl=ctrl, info=info, seq=seq)

    afn = _to_int(send.get("afn", 0x00))
    seq = _to_int(send.get("seq", 0), 10)
    msaa = _to_int(send.get("msaa", 0x01))
    pw = _to_int(send.get("pw", 0x0000))

    rtsa_raw = send.get("rtsa")
    if isinstance(rtsa_raw, str):
        rtsa = bytes.fromhex(rtsa_raw.replace(" ", ""))[::-1][:6]  # 人读顺序 → 线上字节
    elif rtsa_raw is None:
        rtsa = bytes(6)  # 未指定终端地址：全零兜底
    else:
        rtsa = bytes(rtsa_raw)[:6]

    ud = send.get("userdata", b"")
    if isinstance(ud, str):
        userdata = hex_to_bytes(ud)
    elif isinstance(ud, list):
        userdata = bytes(ud)
    else:
        userdata = bytes(ud)

    return build_13762_frame(afn=afn, seq=seq, rtsa=rtsa, msaa=msaa,
                             pw=pw, userdata=userdata)


def _rtsa_to_show(rtsa: bytes) -> str:
    return rtsa[::-1].hex().upper()


# ---------------------------------------------------------------------------
# 单步执行
# ---------------------------------------------------------------------------
def run_step(io: SerialIO, responder: Optional[Responder],
             step: dict, idx: int) -> dict:
    """执行一步，返回判定结果 dict。"""
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

    # 1) 构造并下发（recv_only 跳过）
    if not recv_only:
        try:
            raw = build_send_frame(step.get("send", {}))
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
    while time.time() < deadline:
        got = io.recv_frame(timeout=max(0.05, deadline - time.time()))
        if got is None:
            continue
        result["matched"] = frame_to_hex(got)
        result["parsed"] = _safe_decode(got)
        # 先自动应答（若命中规则），再匹配 expect
        _auto_reply(io, responder, got)
        matched, decoded, reasons = match_frame(got, expect)
        result["parsed"] = decoded
        if matched:
            result["result"] = "pass"
            result["reason"] = "匹配成功" + (f"：{'; '.join(reasons)}" if reasons else "")
            return result
        # 不匹配：若是 recv_only 继续等（可能是其它主动上报）；否则判 fail
        # 待办 3.2 语义确认：recv_only + expect 时，收到的任何不匹配帧都被视为
        # "其它上报"而跳过，持续接收直到 expect 匹配或超时——这正是"连续接收直到超时"。
        if not recv_only:
            result["reason"] = "匹配失败：" + "; ".join(reasons)
            return result
        result["reason"] = "收到帧但未匹配：" + "; ".join(reasons)
    result["reason"] = f"超时({timeout}s)未收到期望帧" + ("" if recv_only else "或匹配失败")
    return result


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
        try:
            io.send_frame(reply)
        except Exception:
            logger.exception("自动应答发送失败；帧=%s 应答=%s",
                             raw.hex()[:64], reply.hex()[:64])


# ---------------------------------------------------------------------------
# 任务级执行
# ---------------------------------------------------------------------------
def execute_task(task: dict, io: Optional[SerialIO] = None) -> dict:
    """执行整个验证任务，返回完整结论 JSON。

    若 io 未提供，则按 task 的 port/baudrate 自建串口并独占打开。
    """
    steps = task.get("steps", [])
    port = task.get("port", "COM3")
    baudrate = task.get("baudrate", 115200)

    own_io = io is None
    if own_io:
        io = SerialIO(port=port, baudrate=baudrate)

    responder = Responder(override_rules=task.get("responders", [])) \
        if task.get("enable_responder", True) else None

    opened = False
    try:
        if own_io:
            io.open()
            opened = True

        step_results = []
        seq_counter = 0
        for idx, step in enumerate(steps):
            # 若本步声明了自有 responder，则临时挂载；否则用任务级 responder
            step_r = responder
            if step.get("responders"):
                step_r = Responder(override_rules=step["responders"])
            # 本地协议下行帧自动分配递增帧序号（对齐 GW-CASS，CCO 响应回显 serial_num）
            step = dict(step)
            if step.get("send") and step["send"].get("format") == "local":
                send = dict(step["send"])
                if "seq" not in send:
                    seq_counter += 1
                    send["seq"] = seq_counter
                step["send"] = send
            r = run_step(io, step_r, step, idx)
            step_results.append(r)
            # 任一步失败即中止（默认），除非 task.fail_fast=false
            if r["result"] == "fail" and task.get("fail_fast", True):
                break

        pass_count = sum(1 for s in step_results if s["result"] == "pass")
        fail_count = sum(1 for s in step_results if s["result"] == "fail")
        verdict = "pass" if fail_count == 0 and step_results else "fail"

        return {
            "task_id": task.get("id", "verify.task"),
            "port": port,
            "baudrate": baudrate,
            "steps": step_results,
            "summary": {
                "total": len(step_results),
                "pass": pass_count,
                "fail": fail_count,
                "verdict": verdict,
            },
        }
    finally:
        if own_io and opened:
            io.close()


def load_task(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
