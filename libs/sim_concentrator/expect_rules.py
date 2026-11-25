"""应答预期规则库加载器（REQS-0027 G2/G3）。

expect_rules.json 声明三件事，UI 与 runner 共读：
- rules：下行 AFN/Fn → 预期应答 {afn, fn, dir, form}（self=同帧回源）；
- timeout_tiers：per-Fn 超时档位（默认 5s / 单抄 59s / 并抄 99s，出处标注）；
- deny_codes：00H-F2 错误状态字 → 人话（6D 超最大并发 / 6E 超条数 / 6F 正在抄读中）。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_DATA: Optional[dict] = None
_LOCK = threading.Lock()


def _norm_afn(v: Any) -> int:
    if isinstance(v, int):
        return v & 0xFF
    return int(str(v), 16)


def _path() -> Path:
    return Path(__file__).resolve().parent / "expect_rules.json"


def load(refresh: bool = False) -> dict:
    """加载规则库（进程内缓存；refresh=True 强制重读，便于测试与热更）。"""
    global _DATA
    with _LOCK:
        if _DATA is None or refresh:
            with open(_path(), "r", encoding="utf-8") as f:
                _DATA = json.load(f)
        return _DATA


def rule_for(afn: Any, fn: Any = None) -> Optional[dict]:
    """按下行 AFN/Fn 取规则：Fn 级规则优先于 AFN 级。"""
    doc = load()
    a = _norm_afn(afn)
    f = _norm_fn(fn) if fn is not None else None
    fallback = None
    for r in doc.get("rules", []):
        m = r.get("match", {})
        if _norm_afn(m.get("afn")) != a:
            continue
        mf = m.get("fn")
        if mf is not None and _norm_fn(mf) == f:
            return r
        if mf is None and fallback is None:
            fallback = r
    return fallback


def _norm_fn(v: Any) -> int:
    # 规则中 Fn 一律为十进制整数（"F" 前缀只出现在 AFN，如 "F1"=并发抄表）
    if isinstance(v, int):
        return v
    return int(str(v).strip().upper().lstrip("F"), 10)


def default_expect(afn: Any, fn: Any = None) -> Tuple[Optional[dict], Optional[dict]]:
    """返回 (expect, rule)：expect 为 matcher 可用的期望结构；无规则返回 (None, None)。

    "self" 表示预期同 AFN 同 Fn 回源（查询/抄读类）。
    """
    rule = rule_for(afn, fn)
    if rule is None:
        return None, None
    e = rule.get("expect", {})
    a = _norm_afn(afn)
    f = _norm_fn(fn) if fn is not None else 0
    out = {"dir": e.get("dir", "up")}
    ea = e.get("afn", "self")
    out["afn"] = a if ea == "self" else _norm_afn(ea)
    ef = e.get("fn", "self")
    out["fn"] = f if ef == "self" else int(ef)
    if e.get("form"):
        out["form"] = e["form"]
    return out, rule


def timeout_for(afn: Any, fn: Any = None) -> dict:
    """返回 {"seconds", "tier", "note"}：按规则取档位，无规则用 default。"""
    doc = load()
    rule = rule_for(afn, fn)
    tier_id = (rule or {}).get("timeout_tier", "default")
    tier = doc.get("timeout_tiers", {}).get(tier_id) or doc["timeout_tiers"]["default"]
    return {
        "seconds": float(tier["seconds"]),
        "tier": tier_id,
        "note": tier.get("note", ""),
        "rule_id": (rule or {}).get("id"),
    }


def deny_text(code: Any) -> str:
    """00H-F2 错误状态字 → 人话；未知值给原始编号。"""
    doc = load()
    codes = doc.get("deny_codes", {})
    try:
        c = int(code)
    except (TypeError, ValueError):
        return str(code)
    return codes.get(str(c), f"未知错误状态字 {c}")


def seq_checked(afn: Any, fn: Any = None) -> bool:
    """该 AFN/Fn 的应答是否必须镜像请求 seq（用于单步/验证的 seq 校验）。

    规则来自 expect_rules.json 的 seq_match：enabled=False 时一律不校验；
    exempt 命中的 AFN/Fn 不校验（实测应答 seq 不跟随请求，如 02H-F1/15H-F1）。
    """
    doc = load()
    policy = doc.get("seq_match") or {}
    if not policy.get("enabled", True):
        return False
    try:
        a = _norm_afn(afn)
    except (TypeError, ValueError):
        return False
    f = None
    if fn is not None:
        try:
            f = _norm_fn(fn)
        except (TypeError, ValueError):
            f = None
    for e in policy.get("exempt") or []:
        try:
            ea = _norm_afn(e.get("afn"))
        except (TypeError, ValueError):
            continue
        if ea != a:
            continue
        ef = e.get("fn")
        if ef is None:
            return False
        try:
            if _norm_fn(ef) == f:
                return False
        except (TypeError, ValueError):
            continue
    return True


def seq_policy_note(afn: Any, fn: Any = None) -> str:
    """seq 校验口径的说明（写进 step 结果，便于 AI/人判断为什么没校验 seq）。"""
    doc = load()
    policy = doc.get("seq_match") or {}
    if not policy.get("enabled", True):
        return "seq 校验已全局关闭（expect_rules.seq_match.enabled=false）"
    try:
        a = _norm_afn(afn)
    except (TypeError, ValueError):
        return "afn 不可解析，跳过 seq 校验"
    f = None
    if fn is not None:
        try:
            f = _norm_fn(fn)
        except (TypeError, ValueError):
            f = None
    for e in policy.get("exempt") or []:
        try:
            if _norm_afn(e.get("afn")) != a:
                continue
        except (TypeError, ValueError):
            continue
        ef = e.get("fn")
        if ef is None or _norm_fn_safe(ef) == f:
            return "该 AFn/Fn 在 seq_match.exempt 内，不校验 seq：" + str(e.get("note") or "")
    return ""


def _norm_fn_safe(v: Any) -> Optional[int]:
    try:
        return _norm_fn(v)
    except (TypeError, ValueError):
        return None


def is_report_afn(afn: Any) -> bool:
    """06H 主动上报：集中器侧下发无预期应答（no_expect_afn）。"""
    doc = load()
    try:
        a = _norm_afn(afn)
    except (TypeError, ValueError):
        return False
    return any(_norm_afn(x) == a for x in doc.get("no_expect_afn", []))
