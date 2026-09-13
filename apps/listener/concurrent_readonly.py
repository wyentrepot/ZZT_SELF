# -*- coding: utf-8 -*-
"""侦听台并发抄表只读统计（REQS-0030，P2）。

被动侦听口径：从 1376.2 收发库（data/listener_13762.sqlite frame_log）读取
AFN=F1H/FN=F1H 并发抄表帧（tx/rx 都是侦听到的网络收发帧，本模块不发任何帧），
按下发/应答配对成"抄读尝试"后交给 period_stats 周期聚合，与模拟集中器侧同口径：
最大并发数 / 成功数成功率 / 平均耗时 / 重复下发。

配对规则：
- 下行帧（dir=tx）从内嵌 645/698 帧地址域提取表地址 → 每表一条在飞记录；
- 上行帧（dir=rx）优先取链路层地址域 A1（失败帧 L=0 口径），其次内嵌帧地址；
  上行数据单元长度=0 判失败（蒸馏卡 88：长度域 0、源地址为失败电表）。
- 未配对成功的下行在 5 分钟生命周期后按超时结清（CCO concurrent_list 兜底口径）。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from sim_concentrator.concurrent_match import parse_data_unit
from sim_concentrator.period_stats import aggregate

LIFETIME_SECONDS = 300.0  # CCO 队列条目 5 分钟自消亡（蒸馏卡 02-并发抄表）


def _addr_from_hex(hex_str: str) -> str:
    """线上 BCD 地址 hex → 人读（逐字节反转：01 02 03 04 05 06 → 06 05 04 03 02 01）。"""
    h = (hex_str or "").replace(" ", "")
    if len(h) == 12:
        return "".join(h[i:i + 2] for i in range(10, -1, -2))
    return h


def _nested_addrs(parsed: dict) -> List[str]:
    """从 parsed.nested 的内嵌 645/698 帧地址域提取表地址（可能一帧多表）。"""
    addrs = []
    for n in parsed.get("nested", []) or []:
        f = (n.get("fields") or {}).get("地址域A") or {}
        raw = f.get("raw") or f.get("hex") or ""
        if isinstance(raw, list):
            raw = "".join(f"{b:02X}" for b in raw)
        if raw:
            addrs.append(_addr_from_hex(str(raw)))
    return addrs


def _link_addr(parsed: dict) -> Optional[str]:
    """链路层地址域 A1（1376.2 地址域）→ 人读地址；无则 None。"""
    f = (parsed.get("fields") or {}).get("地址域A") or {}
    raw = f.get("raw") or f.get("hex") or ""
    if isinstance(raw, list):
        raw = "".join(f"{b:02X}" for b in raw)
    raw = str(raw).replace(" ", "")
    if len(raw) < 12:
        return None
    return _addr_from_hex(raw[:12])


def _payload(parsed: dict, frame_hex: str) -> str:
    """应用数据单元 hex：按解析结构的地址域长度定位 AFN（4=68+L2+C，+信息域6B，
    +地址域A 字节数），跳过 AFN+DT(3B) 后取到 CS/16 前。旧"偏移 13"只对无地址域
    帧成立，真实下发帧（batch.py 带地址构帧）会切错位，故作回退路径保留。"""
    hex_str = (parsed.get("raw_hex") or frame_hex or "").replace(" ", "")
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        raw = b""
    if len(raw) >= 15 and raw[0] == 0x68:
        addr_hex = str((parsed.get("fields") or {}).get("地址域A", {}).get("hex") or "")
        addr_hex = addr_hex.replace(" ", "").upper()
        addr_len = len(addr_hex) // 2 if len(addr_hex) % 2 == 0 else 0
        pos = 4 + 6 + addr_len  # AFN 起始
        if 0 < pos < len(raw) - 3:
            return raw[pos + 3:len(raw) - 2].hex().upper()
    return hex_str[26:-4]  # 回退：每字节2字符，前13字节、后CS+16（无地址域合成帧）


def _epoch(ts: str) -> Optional[float]:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def collect_attempts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """frame_log 行 → 配对后的抄读尝试列表（供 aggregate）。

    duplicate 标记：该尝试是"同表上一次未结清时再次下发"产生的
    （CCO 否认 111 的业务底；由 period_stats 计入 duplicate_count）。
    """
    attempts: List[Dict[str, Any]] = []
    open_map: Dict[str, float] = {}   # addr -> start_epoch（最近一次未结清下发）
    dup_flag: Dict[str, bool] = {}    # addr -> 在飞尝试是否为未结清重发
    for row in sorted(rows, key=lambda r: (r.get("ts") or "", r.get("id") or 0)):
        try:
            parsed = row.get("parsed")
            parsed = json.loads(parsed) if isinstance(parsed, str) else (parsed or {})
        except (TypeError, ValueError):
            parsed = {}
        ep = _epoch(row.get("ts") or "")
        if ep is None:
            continue
        d = (row.get("dir") or "").lower()
        if d in ("tx", "down"):
            addrs = _nested_addrs(parsed) or ["?"]
            for addr in addrs:
                if addr in open_map:
                    # 前一次未结清又下发：旧尝试按生命周期兜底结清
                    attempts.append({
                        "meter": addr, "start_epoch": open_map.pop(addr),
                        "end_epoch": ep, "status": "timeout",
                        "duplicate": dup_flag.get(addr, False)})
                    dup_flag[addr] = True   # 本次下发即重复下发
                else:
                    dup_flag[addr] = False
                open_map[addr] = ep
        elif d in ("rx", "up"):
            addr = _link_addr(parsed) or (_nested_addrs(parsed) or ["?"])[0]
            unit = parse_data_unit("up", _payload(parsed, row.get("frame_hex") or ""))
            status = "timeout" if unit.get("failed") else "success"
            start = open_map.pop(addr, None)
            attempts.append({
                "meter": addr, "start_epoch": start if start is not None else ep,
                "end_epoch": ep, "status": status,
                "duplicate": bool(dup_flag.pop(addr, False)) if start is not None else False})
    # 兜底：超过 5 分钟生命周期仍未结清的下发按超时结清
    latest = max((a["end_epoch"] for a in attempts), default=0.0)
    for addr, start in list(open_map.items()):
        if latest - start >= LIFETIME_SECONDS:
            attempts.append({"meter": addr, "start_epoch": start,
                             "end_epoch": start + LIFETIME_SECONDS, "status": "timeout",
                             "duplicate": dup_flag.pop(addr, False)})
    return attempts


def concurrent_stats(db_path: str, period: str = "15m") -> dict:
    """只读统计入口：返回周期聚合结果（与模拟集中器 /batch/stats 同口径）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, ts, dir, parsed, frame_hex FROM frame_log "
            "WHERE afn='F1' AND fn='F1' ORDER BY ts")]
    finally:
        con.close()
    attempts = collect_attempts(rows)
    result = aggregate(attempts, period=period)
    result["source"] = "listener_frame_log"
    result["frames_total"] = len(rows)
    return result
