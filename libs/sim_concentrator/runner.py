"""验证任务执行器：下发 → 接收 → 匹配 → 解析 → 判定闭环。

一个验证任务（VerifyTask）由若干步骤组成，每步：
- 构造并下发一帧（send）；支持 CCO 本地协议（send.format="local"）。
- 可选：期望收到一帧并匹配（expect）。
- 可选：recv_only 步骤（不 send，只等待并匹配一帧——用于验证 CCO 主动上报）。
- 可选：expect_history 步骤（在超时内轮询历史帧，确认是否出现过某类上报）。
- 可选：该步骤期望无响应（expect_no_reply）。
- 可选（REQS-0037）：wait_mclt_stored（等 06H-F230 存数入库）与
  read_mclt_11e3（按冻结时刻构造 11E3 并校验 task_id 回显），两步间通过
  步骤上下文传递冻结时刻（详见 mclt_e3/scenario_codec 注释）。

应答引擎在任务执行期间挂载（内置 + 任务覆盖规则）：收到的每一帧若命中
应答规则，立即自动应答（统一回 00H-F1 确认），从而验证"模块上行 → 模拟
集中器应答"闭环；同一帧仍可被 expect / expect_history 匹配验证。

步骤上下文（step context）：execute_task 维护一个 dict 随步骤传递，
wait_mclt_stored 命中后写入 {"mclt_fz_time": "YYYY-MM-DD HH:MM:SS",
"mclt_task_id": X}；read_mclt_11e3 的 freeze_time="auto" 从中取值。
"""
from __future__ import annotations

import json
import logging
import re
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
from sim_concentrator.expect_rules import is_report_afn
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
# REQS-0037：wait_mclt_stored / read_mclt_11e3 步骤执行
# ---------------------------------------------------------------------------
def _store_of(io, ctx: Optional[dict] = None) -> object:
    """取 ListenerStore：优先任务注入（ctx["mclt_store"]），其次 io.journal.store
    （sink 落库链路，api.py 经 SessionManager 挂 store）。缺失返回 None。"""
    if ctx is not None and ctx.get("mclt_store") is not None:
        return ctx["mclt_store"]
    journal = getattr(io, "journal", None)
    store = getattr(journal, "store", None)
    if store is not None:
        return store
    return getattr(journal, "_store_ref", None)


def _latest_mclt_row(store, task_id: int, proto=None) -> Optional[dict]:
    """取 store 中该 task_id 最近一条 F230 记录（proto 可选过滤）。

    store 为 None（库未启用）或查询异常返回 None（由调用方按"未存数"处理）。
    """
    if store is None:
        return None
    try:
        rows = store.list_report_mclt_data(limit=200)
    except Exception:
        return None
    tid = str(task_id)
    for r in rows:  # 新→旧
        if str(r.get("task_id") or "") != tid:
            continue
        if proto is not None and str(r.get("proto_type") or "") != str(proto):
            continue
        if not r.get("collect_time"):
            continue
        return r
    return None


def run_wait_mclt_stored(step: dict, io, ctx: Optional[dict] = None) -> dict:
    """wait_mclt_stored：轮询 store 中该任务 F230 记录直至命中或超时。

    命中即通过，并把冻结时刻写入 ctx（{"mclt_fz_time","mclt_task_id",
    "mclt_row"}）供 read_mclt_11e3 引用。超时按确定性失败处理，报错信息
    含「等到 timeout_s 仍未出现 task_id=X 的 F230」。
    """
    cfg = step.get("wait_mclt_stored") or {}
    task_id = cfg.get("task_id")
    proto = cfg.get("proto")
    timeout = cfg.get("timeout_s", 900)
    poll = cfg.get("poll_s", 5)
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        return {"index": -1, "name": step.get("name", "wait_mclt_stored"),
                "sent_hex": "", "matched": None, "parsed": None,
                "result": "fail",
                "reason": f"wait_mclt_stored.task_id 非法: {task_id!r}"}
    timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else 900.0
    poll = float(poll) if isinstance(poll, (int, float)) and poll > 0 else 5.0

    store = _store_of(io, ctx)
    deadline = time.time() + timeout
    last = None
    while True:
        last = _latest_mclt_row(store, task_id, proto)
        if last is not None:
            break
        if time.time() >= deadline:
            tip = "" if store is not None else \
                "（当前会话未挂 ListenerStore，F230 不会入库；请经 simcon API/带 store 的 journal 收帧）"
            return {"index": -1, "name": step.get("name", "wait_mclt_stored"),
                    "sent_hex": "", "matched": None, "parsed": None,
                    "result": "fail",
                    "reason": (f"等到 {int(timeout)}s 仍未出现 task_id={task_id} 的 F230"
                               + (f"（proto={proto}）" if proto is not None else "")
                               + f"；轮询间隔 {poll:g}s{tip}")}
        time.sleep(poll)

    fz = str(last.get("collect_time"))
    if ctx is not None:
        ctx["mclt_fz_time"] = fz
        ctx["mclt_task_id"] = task_id
        ctx["mclt_row"] = last
    return {
        "index": -1, "name": step.get("name", "wait_mclt_stored"),
        "sent_hex": "", "matched": "", "parsed": None,
        "result": "pass",
        "reason": (f"task_id={task_id} 已存数：collect_time={fz}，"
                   f"proto={last.get('proto_type')}，data_len={last.get('data_len')}"),
        "mclt": {"task_id": task_id, "collect_time": fz,
                 "proto_type": last.get("proto_type"),
                 "data_len": last.get("data_len"),
                 "frame_id": last.get("frame_id")},
    }


def build_11e3_read_step_send(cfg: dict, ctx: Optional[dict] = None) -> bytes:
    """read_mclt_11e3 → 11E3 下行 APS 域字节（mclt_e3.build_11e3_down）。

    freeze_time="auto" 从 ctx["mclt_fz_time"] 取（wait_mclt_stored 写入）；
    上下文没有则报 ScenarioCodecError（防发早——正是台架漏测根因）。
    """
    from sim_concentrator.mclt_e3 import build_11e3_down
    task_id = cfg.get("task_id")
    freeze = cfg.get("freeze_time", "auto")
    if freeze == "auto" or freeze is None:
        fz = (ctx or {}).get("mclt_fz_time")
        if not fz:
            raise ScenarioCodecError(
                "freeze_time=auto 但上下文无冻结时刻：前置 wait_mclt_stored "
                "步骤缺失或未命中（11E3 发早会导致内层 698 缺失，REQS-0037 台架教训）")
        freeze = str(fz)
    kw = {
        "task_id": int(task_id),
        "freeze_time": str(freeze),
        "protocol": int(cfg.get("protocol", 3) or 3),
    }
    if cfg.get("dst"):
        kw["dst"] = str(cfg["dst"])
    if cfg.get("addr_wire"):
        kw["addr_wire"] = str(cfg["addr_wire"])
    if cfg.get("meter_type") is not None:
        kw["meter_type"] = int(cfg["meter_type"])
    return build_11e3_down(**kw)


def run_read_mclt_11e3(step: dict, io, responder: Optional[Responder],
                       ctx: Optional[dict] = None) -> dict:
    """read_mclt_11e3：按冻结时刻发 11E3 下行并校验上行 task_id 回显。

    expect 复用 matcher 机制：afn="11E3" 特判为 APS 域 11E3 帧解析
    （frame[0]==0x11 && frame[1]==0xE3），并断言外层 task_id 回显。
    """
    cfg = step.get("read_mclt_11e3") or {}
    task_id = cfg.get("task_id")
    name = step.get("name", "read_mclt_11e3")
    result = {"index": -1, "name": name, "sent_hex": "", "matched": None,
              "parsed": None, "result": "fail", "reason": ""}
    timeout = step.get("expect_timeout", 60)
    timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else 60.0

    try:
        raw = build_11e3_read_step_send(cfg, ctx)
    except Exception as e:
        result["reason"] = f"构帧失败: {e!r}"
        return result
    result["sent_hex"] = raw.hex()
    try:
        io.send_frame(raw)
    except Exception as e:
        result["reason"] = f"发送失败: {e!r}"
        return result

    from sim_concentrator.mclt_e3 import parse_11e3_up
    deadline = time.time() + timeout
    samples = []
    while time.time() < deadline:
        got = io.recv_frame(timeout=max(0.05, deadline - time.time()))
        if got is None:
            continue
        _auto_reply(io, responder, got)
        up = parse_11e3_up(got)
        if up is None:
            samples.append(got.hex()[:32])
            continue
        result["matched"] = got.hex()
        result["parsed"] = {
            "task_id": up.task_id, "protocol": up.protocol,
            "result": up.result, "di_cnt": up.di_cnt,
            "bufflen": up.bufflen, "fz_time": up.fz_time_text,
            "src_wire": up.src_wire, "ssn": up.ssn,
        }
        if up.task_id != task_id:
            result["reason"] = (
                f"11E3 上行外层 task_id 回显不匹配: 期望{task_id}, 实际{up.task_id}")
            return result
        if not up.ok:
            result["reason"] = (
                f"11E3 上行 result={up.result}（非 0，失败响应；"
                f"di_cnt={up.di_cnt} bufflen={up.bufflen}，冻结时刻 {up.fz_time_text}）")
            return result
        if ctx is not None:
            ctx["mclt_11e3_up"] = result["parsed"]
        result["result"] = "pass"
        result["reason"] = (
            f"匹配成功：task_id={up.task_id} 回显一致，result=0，"
            f"di_cnt={up.di_cnt}，bufflen={up.bufflen}，fz={up.fz_time_text}")
        return result
    result["reason"] = (f"超时({timeout:g}s)未收到 11E3 上行"
                        + (f"；期间收到非 11E3 帧 {len(samples)} 条" if samples else ""))
    if samples:
        result["bystanders"] = [{"hex": s} for s in samples[-3:]]
    return result


def _is_11e3_up_frame(raw: bytes) -> bool:
    """expect afn="11E3" 的帧形态判定（APS 域 11 E3 + dir=1）。"""
    return len(raw) >= 6 and raw[0] == 0x11 and raw[1] == 0xE3 and bool(raw[4] & 0x80)


# ---------------------------------------------------------------------------
# 单步执行
# ---------------------------------------------------------------------------
def run_step(io: SerialIO, responder: Optional[Responder],
             step: dict, idx: int, profile: Optional[dict] = None,
             seq: Optional[int] = None,
             ctx: Optional[dict] = None) -> dict:
    """执行一步，返回判定结果 dict。

    profile：task.profile 加载的全局信息（传给 build_send_frame 构帧）。
    seq：构帧时 info.seq 覆盖值（缺省用 idx+1；单步下发由调用方传会话内递增序号）。
    ctx：步骤上下文（REQS-0037：wait_mclt_stored → read_mclt_11e3 传冻结时刻）。
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

    # REQS-0037：分钟采集等存数/按冻结时刻读数两语义步骤（非 1376.2 帧，单独执行）
    if step.get("wait_mclt_stored"):
        r = run_wait_mclt_stored(step, io, ctx)
        r["index"] = idx
        r["name"] = step.get("name", r["name"])
        return r
    if step.get("read_mclt_11e3"):
        r = run_read_mclt_11e3(step, io, responder, ctx)
        r["index"] = idx
        r["name"] = step.get("name", r["name"])
        return r

    recv_only = step.get("recv_only", False)
    expect_history = step.get("expect_history", False)
    # expect_timeout 显式传 null/非法值时回缺省，避免 recv_frame(timeout=None)
    # 在静默串口上永久阻塞（任务总时长由 AI 层看门狗兜底，这里保证单步有界）。
    timeout = step.get("expect_timeout", 5.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 5.0
    expect = step.get("expect")
    is_query = (expect is not None) and not expect_history
    # 本次请求的 R 域 seq（构帧时注入的就是它，应答必须镜像）；recv_only 无请求 → None
    expect_seq = int(seq) & 0xFF if (seq is not None and not recv_only) else None

    # 1) 构造并下发（recv_only 跳过；expect_history 且无 send 时不构帧，纯历史扫描）
    if not recv_only and not (expect_history and not step.get("send")):
        try:
            raw = build_send_frame(step.get("send", {}), profile=profile,
                                   seq=seq if seq is not None else idx + 1)
        except Exception as e:
            result["reason"] = f"构帧失败: {e!r}"
            return result
        result["sent_hex"] = frame_to_hex(raw)
        # 发送前清空接收队列：上一步/上一次请求遗留的应答若还躺在队列里，
        # 会被当成本次应答（2026-11-19 实测 sent seq=05 / matched seq=02 的假 pass）。
        # 只清队列不动历史（history 供 expect_history 分支使用）。
        drain = getattr(io, "drain_rx", None)
        if callable(drain):
            try:
                drained = drain()
                if drained:
                    result["drained_stale_frames"] = int(drained)
            except Exception:  # noqa: BLE001 - 清理失败不影响主流程
                logger.exception("drain_rx 失败，继续发送")
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
    mismatch_samples = []  # REQS-0036：未匹配原因采样（去重，超时反馈给 AI 自纠）
    while time.time() < deadline:
        got = io.recv_frame(timeout=max(0.05, deadline - time.time()))
        if got is None:
            continue
        result["matched"] = frame_to_hex(got)
        result["parsed"] = _safe_decode(got)
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
        # seq 校验（2026-11-19）：AFN/Fn 命中还要求 R 域 seq 等于本次请求的 seq，
        # 否则是上一轮请求的迟到应答，不能算「匹配成功」。exempt 的 AFN/Fn（02H-F1
        # 并发单抄、15H-F1 文件分段）实测不镜像 seq，按 expect_rules.seq_match 放行。
        if matched and expect_seq is not None and _seq_checked_for(expect):
            got_seq = _seq_of(decoded)
            if got_seq is not None and got_seq != expect_seq:
                matched = False
                reasons = [f"seq 不匹配: 期望{expect_seq}, 实际{got_seq}（疑似上一轮迟到应答）"]
                result["seq_mismatch"] = {"expected": expect_seq, "actual": got_seq}
        # 自动应答只针对「不是本次请求应答」的帧 + 主动上报帧。
        # 把应答帧也回一帧会让对端把回帧当成新请求，形成乒乓风暴
        # （2026-11-19 实测：simcon 以集中器身份发 03H-F1，CCO 的 03H-F1 应答
        #  命中 builtin.03xx_query → 回 03H 查询帧 → CCO 再应答，tx/rx 无限增长，
        #  并让队列里的旧应答被后续步骤误判为「匹配成功」）。
        if not matched or is_report_afn(_afn_of(decoded)):
            _auto_reply(io, responder, got)
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
        if not matched and reasons:
            sample = "; ".join(reasons)
            if sample not in mismatch_samples:
                mismatch_samples.append(sample)
        # 若 recv_only 也继续等；send+expect 同样跳过无关帧（修复：真实 CCO 插播主动帧）
    rx_count = len(result.get("received_hex", []) or [])
    result["reason"] = (f"超时({timeout}s)未收到期望帧"
                        + (f"，期间收到 {rx_count} 帧未匹配" if rx_count else ""))
    if mismatch_samples:
        # 把逐帧不匹配的原因透出（如 "AFN 不匹配: 期望0x0A, 实际0x10"），
        # 调用方（AI）据此自纠 expect 记法，而不是只看到"超时"干等。
        result["reason"] += "；未匹配原因: " + " | ".join(mismatch_samples[-3:])
    if bystanders:
        result["bystanders"] = bystanders
    return result


def _seq_of(decoded: dict) -> Optional[int]:
    """取 R 域里的报文序列号 seq（解析失败返回 None）。"""
    info = (decoded.get("fields") or {}).get("信息域R") or {}
    desc = str(info.get("desc") or "")
    m = re.search(r"seq=(\d+)", desc)
    if m:
        return int(m.group(1))
    value = str(info.get("value") or info.get("raw") or "")
    if len(value) == 12 and all(c in "0123456789abcdefABCDEF" for c in value):
        try:
            return int(value, 16) & 0xFF
        except ValueError:
            return None
    return None


def _seq_checked_for(expect: dict) -> bool:
    """本次 expect 是否要求校验 seq（按请求侧 AFN/Fn 查 expect_rules.seq_match）。"""
    try:
        from sim_concentrator.expect_rules import seq_checked
        return bool(seq_checked(expect.get("afn"), expect.get("fn")))
    except Exception:  # noqa: BLE001 - 规则库异常时按严格校验
        return True


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

    # REQS-0037：任务可直接注入 ListenerStore（测试/无 journal 场景），
    # 供 wait_mclt_stored 轮询 report_mclt_data。
    step_ctx_base: dict = {}
    if task.get("store") is not None:
        step_ctx_base["mclt_store"] = task["store"]

    opened = False
    try:
        if own_io:
            io.open()
            opened = True

        scope_ctx = journal.scope(run_id, "step_send") if journal is not None else nullcontext()
        with scope_ctx:
            step_results = []
            seq_counter = 0
            step_ctx: dict = dict(step_ctx_base)  # REQS-0037：步骤间上下文（冻结时刻等）
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
                r = run_step(io, step_r, step, idx, profile=profile, ctx=step_ctx)
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
