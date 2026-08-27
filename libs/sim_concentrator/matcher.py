"""接收帧匹配与判定：验证任务期望条件 → 对接收帧做匹配/断言。

统一单 68 帧（Q/GDW 10376.2，ADR-44）：CCO 本地协议 = 单 68 标准帧，
不再区分 local/standard。

期望条件（expect）结构（对齐 loghooks 规则 match 的字段语义）：
    {
      "afn": 0x02,                  # 期望 AFN（可选）
      "fn": 230,                    # 期望 FN（可选）
      "dir": "up",                  # "up"(上行) | "down"(下行)（可选）
      "format": "standard",         # 兼容字段："local"/"standard" 均指向单68（可选）
      "nested": true,               # 期望含嵌套 645/698 帧（可选）
      "fields": {"FN": 1},          # 期望信封字段值（可选，按 DataField.raw/value）
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
    if "afn" in decoded and isinstance(decoded["afn"], int):
        return decoded["afn"]
    afn_field = decoded.get("fields", {}).get("AFN", {})
    raw = afn_field.get("raw")
    if isinstance(raw, int):
        return raw
    return None


def _fn_of(decoded: dict) -> Optional[int]:
    if "fn" in decoded and isinstance(decoded["fn"], int):
        return decoded["fn"]
    fn_field = decoded.get("fields", {}).get("FN", {})
    raw = fn_field.get("raw")
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

    # 帧格式（单 68 统一；兼容 local/standard 标记，均视为标准单68）
    want_fmt = expect.get("format")
    if want_fmt in ("local", "standard"):
        want_fmt = "standard"
    if want_fmt is not None and want_fmt not in ("standard", "auto"):
        reasons.append(f"未知帧格式 {want_fmt}")
        return False, decoded, reasons

    # AFN
    want_afn = expect.get("afn")
    if want_afn is not None:
        want = int(want_afn, 16) if isinstance(want_afn, str) else want_afn
        got = _afn_of(decoded)
        if got != want:
            reasons.append(f"AFN 不匹配: 期望0x{want:02X}, 实际0x{got:02X}" if got is not None
                           else "AFN 不可解析")
            return False, decoded, reasons

    # FN
    want_fn = expect.get("fn")
    if want_fn is not None:
        got = _fn_of(decoded)
        if got != int(want_fn):
            reasons.append(f"FN 不匹配: 期望{int(want_fn)}, 实际{got}")
            return False, decoded, reasons

    # 方向（控制域 DIR 位）
    want_dir = expect.get("dir")
    if want_dir is not None:
        ctl = decoded.get("fields", {}).get("控制域C", {})
        got_dir = "up" if ctl.get("raw") is not None and ((ctl["raw"] >> 7) & 1) else "down"
        if got_dir != want_dir:
            reasons.append(f"方向不匹配: 期望{want_dir}, 实际{got_dir}")
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
