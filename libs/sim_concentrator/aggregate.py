"""抄读汇总与主动上报计数聚合（REQS-0027 G4/G6 数据面）。

- collect_readings：单抄（batch mode=single）/ 并抄（mode=batch）/ 查询快照
  共用一张明细表（表地址/AFN-Fn/数据项/值/时间/耗时/结果），失败按否认码
  细分；顶部统计行给出 下发/应答/成功/失败/成功率。
- report_buckets：AFN=06H 主动上报按类型分桶（F1 从节点信息 / F2 抄读数据 /
  F3 路由工况 / F4 信息+设备类型 / F5 事件，其中停复电子类 01H/02H 单列）。

口径：超时（无应答）计入失败；应答数 = 成功 + 否认（收到了明确应答）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sim_concentrator.store import ListenerStore

_REPORT_FN_NAMES = {
    "1": "从节点信息上报",
    "2": "抄读数据上报",
    "3": "路由工况变动",
    "4": "从节点信息及设备类型",
    "5": "从节点事件",
}

_DENY_KEYS = {
    "6D": "6D 超最大并发",
    "6E": "6E 超条数",
    "6F": "6F 正在抄读中",
}


def _empty_stats() -> dict:
    return {"sent": 0, "replied": 0, "success": 0, "failed": 0,
            "success_rate": None, "deny_breakdown": {}}


def _stat_row(stats: dict, row: dict) -> None:
    stats["sent"] += 1
    status = row.get("status")
    if status == "success":
        stats["success"] += 1
        stats["replied"] += 1
    elif status == "deny":
        stats["failed"] += 1
        stats["replied"] += 1
        key = row.get("deny_code") or "deny"
        label = _DENY_KEYS.get(key, f"{key} 否认")
        stats["deny_breakdown"][label] = stats["deny_breakdown"].get(label, 0) + 1
    else:
        stats["failed"] += 1
        label = "timeout 超时/无应答" if status == "timeout" else (status or "error")
        stats["deny_breakdown"][label] = stats["deny_breakdown"].get(label, 0) + 1


def _finalize(stats: dict) -> dict:
    if stats["sent"]:
        stats["success_rate"] = round(stats["success"] / stats["sent"], 4)
    return stats


def collect_readings(store: Optional[ListenerStore], jobs: Optional[Dict[str, Any]] = None,
                     *, source: Optional[str] = None,
                     result: Optional[str] = None,
                     since: Optional[str] = None,
                     limit: int = 1000) -> dict:
    """统一抄读数据表格（G4）：并发任务明细 + 收发库快照/抄读上报合并。

    source: batch|snapshot|report 过滤；result: success|deny|timeout|error 过滤；
    since: ISO 时间下限。返回 {"stats":…, "rows":[…]}（rows 新→旧）。
    """
    stats = _empty_stats()
    rows: List[dict] = []

    for job in (jobs or {}).values():
        snap = job.snapshot()
        for r in snap.get("rows", []):
            rows.append({
                "source": "batch",
                "source_label": f"并发任务 {snap.get('job_id','')}（{snap.get('mode','')}）",
                "meter": r.get("meter"),
                "afn_fn": r.get("afn_fn"),
                "item": "抄读数据" if r.get("status") == "success" else "—",
                "value": (r.get("reply_hex") or "")[:48],
                "ts": r.get("ts"),
                "elapsed_ms": r.get("elapsed_ms"),
                "status": r.get("status"),
                "deny_code": r.get("deny_code"),
                "deny_text": r.get("deny_text"),
            })

    if store is not None:
        try:
            for s in store.list_snapshots(limit=200):
                for it in store.snapshot_items(s["id"]):
                    payload = {}
                    try:
                        payload = json.loads(it.get("payload_json") or "{}")
                    except Exception:
                        pass
                    rows.append({
                        "source": "snapshot",
                        "source_label": f"查询快照 {s.get('afn','')} {s.get('fn','')}",
                        "meter": it.get("addr") or payload.get("从节点地址") or "—",
                        "afn_fn": f"{s.get('afn','')} {s.get('fn','')}",
                        "item": "快照明细",
                        "value": json.dumps(payload, ensure_ascii=False)[:96],
                        "ts": s.get("ts"),
                        "elapsed_ms": None,
                        "status": "success" if s.get("status") != "error" else "error",
                        "deny_code": None,
                        "deny_text": None,
                    })
            try:
                for m in store.query(
                        "SELECT ts, seq_no, proto_type, payload_json FROM report_meter_data "
                        "ORDER BY id DESC LIMIT 200"):
                    rows.append({
                        "source": "report",
                        "source_label": "06H-F2 抄读数据上报",
                        "meter": m.get("seq_no") or "—",
                        "afn_fn": "06H F2",
                        "item": "上报抄读数据",
                        "value": (m.get("payload_json") or "")[:96],
                        "ts": m.get("ts"),
                        "elapsed_ms": None,
                        "status": "success",
                        "deny_code": None,
                        "deny_text": None,
                    })
            except Exception:
                pass
        except Exception:
            pass

    if source:
        rows = [r for r in rows if r["source"] == source]
    if result:
        rows = [r for r in rows if r["status"] == result]
    if since:
        rows = [r for r in rows if (r.get("ts") or "") >= since]
    rows.sort(key=lambda r: r.get("ts") or "", reverse=True)
    rows = rows[: max(1, int(limit))]
    for r in rows:
        _stat_row(stats, r)
    return {"stats": _finalize(stats), "rows": rows}


def report_buckets(store: Optional[ListenerStore], *, limit: int = 500) -> dict:
    """主动上报分类型计数（G6）：F1-F5 各桶 + 停复电子类单列。"""
    buckets: Dict[str, dict] = {
        "F1": {"name": _REPORT_FN_NAMES["1"], "count": 0, "items": []},
        "F2": {"name": _REPORT_FN_NAMES["2"], "count": 0, "items": []},
        "F3": {"name": _REPORT_FN_NAMES["3"], "count": 0, "items": []},
        "F4": {"name": _REPORT_FN_NAMES["4"], "count": 0, "items": []},
        "F5": {"name": _REPORT_FN_NAMES["5"], "count": 0, "items": []},
        "F5_power": {"name": "停复电事件（协议类型 04H）", "count": 0, "items": []},
    }
    if store is None:
        return {"buckets": buckets, "total": 0}

    def _addr_of(payload: dict) -> str:
        recs = payload.get("records") or []
        for rec in recs:
            a = rec.get("从节点地址") or rec.get("通信单元地址") or rec.get("地址")
            if a:
                return str(a)
        return "—"

    events = store.list_report_events(limit=max(50, int(limit)))
    for ev in events:
        fn = str(ev.get("fn") or "").lstrip("Ff")
        payload: dict = {}
        try:
            payload = json.loads(ev.get("payload_json") or "{}")
        except Exception:
            pass
        item = {
            "ts": ev.get("ts"),
            "afn_fn": f"{ev.get('afn','06H')} F{fn}",
            "meter": _addr_of(payload),
            "summary": (json.dumps(payload, ensure_ascii=False)[:120]),
            "payload": payload,
        }
        key = f"F{fn}" if f"F{fn}" in buckets else None
        if key is None:
            continue
        # 停复电子类（F5 且 协议类型=04H）：事件类型 01H=停电 / 02H=复电
        sub = None
        if fn == "5":
            proto = str(payload.get("通信协议类型") or
                        (payload.get("head") or {}).get("通信协议类型") or "")
            etype = str(payload.get("事件类型") or
                        (payload.get("head") or {}).get("事件类型") or "")
            for rec in payload.get("records") or []:
                proto = proto or str(rec.get("通信协议类型") or "")
                etype = etype or str(rec.get("事件类型") or "")
            if proto in ("04H", "04", "4"):
                sub = "停电" if etype in ("01H", "01", "1") else (
                    "复电" if etype in ("02H", "02", "2") else "停复电")
                buckets["F5_power"]["count"] += 1
                buckets["F5_power"]["items"].append({**item, "event": sub})
        buckets[key]["count"] += 1
        buckets[key]["items"].append(item)
    total = sum(b["count"] for k, b in buckets.items() if k != "F5_power")
    return {"buckets": buckets, "total": total}
