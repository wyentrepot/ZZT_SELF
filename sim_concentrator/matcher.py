"""接收帧匹配与判定：验证任务期望条件 → 对接收帧做匹配/断言。

期望条件（expect）结构（对齐 loghooks 规则 match 的字段语义）：
    {
      "afn": 0x02,                  # 期望 AFN（可选）
      "dir": "up",                  # "up"(上行, DIR=1) | "down"(下行, DIR=0)（可选）
      "nested": true,               # 期望含嵌套 645/698 帧（可选）
      "fields": {"SEQ": 1},         # 期望信封字段值（可选，按 DataField.raw/value）
      "nested_fields": [            # 期望嵌套帧内字段（可选）
          {"structure": "645", "field": "(当前)正向有功总电能", "eq": 123456.78}
      ],
      "any": true,                  # 匹配任意帧（默认要求是 1376.2 帧）
    }

匹配结果返回 (matched: bool, decoded: dict, reasons: list[str])。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sim_concentrator.frame_codec import decode_frame


def _afn_of(decoded: dict) -> Optional[int]:
    afn_field = decoded.get("fields", {}).get("AFN", {})
    raw = afn_field.get("raw")
    if isinstance(raw, int):
        return raw
    return None


def _field_value(decoded: dict, name: str):
    """取信封字段值（优先 raw，其次 value）。"""
    f = decoded.get("fields", {}).get(name)
    if f is None:
        return None
    if f.get("raw") is not None:
        return f.get("raw")
    return f.get("value")


def match_frame(raw: bytes, expect: Optional[dict]) -> Tuple[bool, dict, List[str]]:
    """对一帧做匹配。expect=None 视为任意 1376.2 帧。"""
    reasons: List[str] = []
    try:
        decoded = decode_frame(raw)
    except Exception as e:
        return False, {}, [f"解析失败: {e!r}"]

    if expect is None:
        return True, decoded, reasons

    # AFN
    want_afn = expect.get("afn")
    if want_afn is not None:
        want = int(want_afn, 16) if isinstance(want_afn, str) else want_afn
        got = _afn_of(decoded)
        if got != want:
            reasons.append(f"AFN 不匹配: 期望0x{want:02X}, 实际0x{got:02X}" if got is not None
                           else "AFN 不可解析")
            return False, decoded, reasons

    # 信封字段
    for fname, want in (expect.get("fields") or {}).items():
        got = _field_value(decoded, fname)
        if got != want:
            reasons.append(f"字段 {fname} 不匹配: 期望{want!r}, 实际{got!r}")
            return False, decoded, reasons

    # 嵌套帧
    if expect.get("nested"):
        if not decoded.get("nested"):
            reasons.append("期望含嵌套 645/698 帧，但未解析到")
            return False, decoded, reasons

    # 嵌套字段断言
    for nf in (expect.get("nested_fields") or []):
        if not _match_nested_field(decoded, nf, reasons):
            return False, decoded, reasons

    return True, decoded, reasons


def _match_nested_field(decoded: dict, nf: dict, reasons: List[str]) -> bool:
    structure = nf.get("structure")
    fname = nf.get("field")
    want = nf.get("eq")
    op = nf.get("op", "eq")
    for nested in decoded.get("nested", []):
        if structure and nested.get("structure") != structure:
            continue
        for it in nested.get("items", []):
            if it.get("name") != fname:
                continue
            val = it.get("value")
            if _compare(val, want, op):
                return True
            reasons.append(f"嵌套字段 {fname}: 期望{op}{want!r}, 实际{val!r}")
            return False
    reasons.append(f"未找到嵌套字段 {fname} (structure={structure})")
    return False


def _compare(got, want, op: str) -> bool:
    if op == "eq":
        # 数值宽松比较：123456.78 == 123456.78 / '123456.78'
        try:
            return float(got) == float(want)
        except (TypeError, ValueError):
            return got == want
    if op == "contains":
        return want in got
    if op == "startswith":
        return str(got).startswith(str(want))
    return got == want
