"""验证任务执行器：下发 → 接收 → 匹配 → 解析 → 判定闭环。

一个验证任务（VerifyTask）由若干步骤组成，每步：
- 构造并下发一帧（send）；
- 可选：期望收到一帧并匹配（expect）；
- 可选：该步骤期望无响应（expect_no_reply）。

执行结果：逐步判定（Pass/Fail + 原因）+ 汇总结论。

应答引擎在任务执行期间挂载（内置 + 任务覆盖规则），收到模块上行帧时
自动应答，从而验证"模块上行 → 模拟集中器应答"闭环。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Dict, List, Optional

from sim_concentrator.frame_codec import (
    build_13762_frame,
    decode_frame,
    frame_to_hex,
    hex_to_bytes,
)
from sim_concentrator.matcher import match_frame
from sim_concentrator.responder import Responder
from sim_concentrator.serial_io import SerialIO


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
        "afn": 0x02 | "02",
        "seq": 1,
        "rtsa": "070919051620" | [0x20,0x16,...],   # 人读顺序 hex 或字节列表
        "msaa": 1,
        "pw": 0,
        "userdata": "00 01 68..." | "000168..." | [bytes],
    }

    rtsa 缺省时用 6 字节零地址（模拟场景下未指定终端地址的兜底，避免构帧崩溃）。
    """
    send = send or {}
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

    # 1) 构造并下发
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

    # 2) 接收并匹配（或期望无响应）
    expect_no_reply = step.get("expect_no_reply", False)
    timeout = step.get("expect_timeout", 5.0)
    expect = step.get("expect")

    if expect_no_reply:
        got = io.recv_frame(timeout=timeout)
        if got is None:
            result["result"] = "pass"
            result["reason"] = "期望无响应，符合"
        else:
            result["matched"] = frame_to_hex(got)
            result["parsed"] = decode_frame(got)
            result["reason"] = "期望无响应，但收到帧"
        return result

    if expect is None:
        # 无期望：发送成功即 pass（记录已发）
        result["result"] = "pass"
        result["reason"] = "仅下发，无接收断言"
        return result

    # 3) 期望接收一帧
    got = io.recv_frame(timeout=timeout)
    if got is None:
        result["reason"] = f"超时({timeout}s)未收到期望帧"
        return result

    result["matched"] = frame_to_hex(got)
    result["parsed"] = decode_frame(got)
    matched, decoded, reasons = match_frame(got, expect)
    result["parsed"] = decoded
    if matched:
        result["result"] = "pass"
        result["reason"] = "匹配成功" + (f"：{'; '.join(reasons)}" if reasons else "")
    else:
        result["result"] = "fail"
        result["reason"] = "匹配失败：" + "; ".join(reasons)
    return result


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
        for idx, step in enumerate(steps):
            # 若本步声明了自有 responder，则临时挂载；否则用任务级 responder
            step_r = responder
            if step.get("responders"):
                step_r = Responder(override_rules=step["responders"])
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
