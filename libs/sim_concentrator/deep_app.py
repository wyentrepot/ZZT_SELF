# -*- coding: utf-8 -*-
"""模拟集中器深化应用核心（REQS-0030，应用层 1376.2）。

向导式流程的后端能力，全部构帧实现、不依赖真实日志：
1. 查档案（10H-F2 从节点信息）：地址 + 信号品质/中继级别，临时存储、可导出 Excel；
   每次必须从模块实时获取，复位不保存。
2. 查在网（10H-F1 从节点数量）：网络规模口径。
3. 最大并发数前置校验：对照 CCO 并发上限 20（否认 109 口径），超限不真发。

帧识别口径见 concurrent_match.py；周期统计见 period_stats.py。
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sim_concentrator.frame_codec import decode_frame  # noqa: E402
from sim_concentrator.scenario_codec import build_send  # noqa: E402

MAX_CONCURRENT_CAP = 20  # CCO concurrent_tab[20]（蒸馏卡 02-并发抄表；否认 109）


def precheck_max_concurrent(max_concurrent: int) -> int:
    """最大并发数前置校验：1~20，超限直接拒绝（对照否认 109 口径，不真发）。"""
    n = int(max_concurrent)
    if n < 1 or n > MAX_CONCURRENT_CAP:
        raise ValueError(
            f"最大并发数超限：{n}（允许 1~{MAX_CONCURRENT_CAP}，"
            f"CCO 并发帧上限 20，超限否认 109）")
    return n


def frame_afn_fn(raw: bytes) -> Optional[tuple]:
    """从整帧字节提取 (afn, fn)；非 1376.2 单 68 帧返回 None。"""
    try:
        fields = decode_frame(raw)["fields"]
        afn = fields["AFN"]["raw"]
        dt1, dt2 = fields["数据单元标识"]["raw"]
        fn = (dt2 & 0x1F) * 32 + (dt1 & 0x1F)
        return afn, fn
    except Exception:
        return None


def _app_payload(raw: bytes) -> bytes:
    """整帧 → 应用数据单元（DT 之后、CS 之前）。68 L(2) C(1) R(6) AFN DT1 DT2 后开始。"""
    return raw[13:-2]


def _wait_reply(io, afn: int, fn: int, *, timeout: float,
                baseline: int = 0) -> Optional[bytes]:
    """在 rx_history 基线之后等待指定 AFN/Fn 应答帧（不消费历史）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = io.rx_history()
        for raw in hist[baseline:]:
            got = frame_afn_fn(raw)
            if got == (afn, fn):
                return raw
        time.sleep(0.05)
    return None


def _resp_contract(afn_code: str, fn_no: str) -> dict:
    """从解析库元数据取 resp 契约（与 test_p10_resp 同源）。"""
    import json
    root = Path(__file__).resolve().parent.parent / "parser_lib" / "adapters" / \
        "adapter_10376" / "metadata" / "afn_fn.json"
    meta = json.loads(root.read_text(encoding="utf-8"))
    for a in meta["afn"]:
        if a["code"] == afn_code:
            for f in a["fns"]:
                if f["no"] == fn_no:
                    return f.get("resp", {})
    return {}


class ArchiveSession:
    """临时档案会话：每次查询全量重建，复位不保存（REQS-0030 用户决策 #2）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.fetched_at: Optional[str] = None
        self.nodes: List[Dict[str, Any]] = []

    def replace(self, nodes: List[Dict[str, Any]]) -> None:
        with self.lock:
            self.fetched_at = datetime.now().isoformat(timespec="seconds")
            self.nodes = nodes

    def snapshot(self) -> dict:
        with self.lock:
            return {"fetched_at": self.fetched_at, "nodes": list(self.nodes),
                    "total": len(self.nodes)}

    def export_excel(self, path: str) -> str:
        """导出当前临时档案为 Excel（openpyxl；导出不影响临时性）。"""
        from openpyxl import Workbook
        snap = self.snapshot()
        wb = Workbook()
        ws = wb.active
        ws.title = "从节点档案"
        ws.append(["序号", "从节点地址", "侦听信号品质", "中继级别", "在网判定",
                   "从节点信息(hex)", "获取时间"])
        for i, n in enumerate(snap["nodes"], 1):
            ws.append([i, n["addr"], n["signal"], n["relay"],
                       "在线" if n["online"] else "离线",
                       n["node_info_hex"], snap["fetched_at"] or ""])
        wb.save(path)
        return path


def _node_from_record(rec: dict) -> dict:
    """F2 记录行 → 节点 dict（低字节 D7~D4 信号品质 / D3~D0 中继级别）。"""
    info = rec.get("从节点信息", {})
    raw = info.get("hex", "") if isinstance(info, dict) else str(info)
    try:
        # extractor 的 hex 是线上字节序，节点信息为 16 位小端值
        v = int.from_bytes(bytes.fromhex(raw), "little") if raw else 0
    except ValueError:
        v = 0
    low = v & 0xFF
    signal, relay = (low >> 4) & 0x0F, low & 0x0F
    addr_wire = str(rec.get("从节点地址", ""))
    # 线上 BCD 字节序 → 人读地址（逐字节反转，与 simcon 档案口径一致）
    addr = addr_wire[::-1] if len(addr_wire) == 12 else addr_wire
    return {
        "addr": addr,
        "node_info_hex": raw,
        "signal": signal,
        "relay": relay,
        "online": signal > 0,  # 有侦听信号品质即在网（10H-F2 口径）
    }


def query_archive(io, *, start: int = 0, count: int = 200,
                  timeout: float = 5.0, profile: Optional[dict] = None,
                  session: Optional[ArchiveSession] = None,
                  seq: int = 1) -> dict:
    """查档案：构帧下发 10H-F2，等应答，解析记录行并写入临时会话。

    必须真实从模块获取（REQS-0030 决策 #2）；无应答抛 TimeoutError 不用旧数据顶替。
    """
    raw = build_send({"afn": "10", "fn": "F2",
                      "params": {"start": start, "count": count}},
                     profile or {}, seq=seq)
    baseline = len(io.rx_history())
    io.send_frame(raw)
    reply = _wait_reply(io, 0x10, 2, timeout=timeout, baseline=baseline)
    if reply is None:
        raise TimeoutError(f"10H-F2 档案应答超时（{timeout}s）")
    from sim_concentrator.record_extractor import extract_response
    out = extract_response(_app_payload(reply),
                           _resp_contract("10H", "F2"))
    nodes = [_node_from_record(r) for r in out.get("records", [])]
    result = {"total": out.get("head", {}).get("从节点总数量", len(nodes)),
              "nodes": nodes}
    if session is not None:
        session.replace(nodes)
    return result


def query_online(io, *, timeout: float = 5.0,
                 profile: Optional[dict] = None, seq: int = 2) -> dict:
    """查在网（网络规模）：10H-F1 从节点总数量 / 路由支持最大容量。"""
    raw = build_send({"afn": "10", "fn": "F1", "params": {}},
                     profile or {}, seq=seq)
    baseline = len(io.rx_history())
    io.send_frame(raw)
    reply = _wait_reply(io, 0x10, 1, timeout=timeout, baseline=baseline)
    if reply is None:
        raise TimeoutError(f"10H-F1 在网应答超时（{timeout}s）")
    from sim_concentrator.record_extractor import extract_response
    out = extract_response(_app_payload(reply),
                           _resp_contract("10H", "F1"))
    head = out.get("head", {})
    return {"total": head.get("从节点总数量"),
            "capacity": head.get("路由支持最大从节点数量")}
