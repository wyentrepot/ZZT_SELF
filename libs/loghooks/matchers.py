"""匹配器：text 正则 / field 字段匹配 + 行号弱约束 + capture 提取。

一条规则命中后返回 MatchResult，包含捕获值和行号漂移信息。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .rules import MatchDef, Rule
from .sources import ParsedLine


@dataclass
class MatchResult:
    """单条规则对一行的匹配结果。"""

    matched: bool = False
    captures: Dict[str, Any] = field(default_factory=dict)  # 按规则 capture 映射后的字段
    line_drift: bool = False  # 行号漂移
    line_actual: Optional[int] = None
    line_expected: Optional[int] = None
    # 内部使用
    _raw_groups: Tuple[str, ...] = field(default_factory=tuple)  # 原始捕获组


def _compile(pattern: str, flags: List[str]) -> re.Pattern:
    flag_bits = 0
    for f in flags:
        fl = f.lower()
        if fl == "i":
            flag_bits |= re.IGNORECASE
        elif fl == "m":
            flag_bits |= re.MULTILINE
        elif fl == "s":
            flag_bits |= re.DOTALL
    return re.compile(pattern, flag_bits)


def _extract_field_value(obj: Any, path: str) -> Optional[Any]:
    """按点号路径 + #中文名 从 dict 中取值。

    #字段名 会在 application.fields 中按中文名模糊匹配。
    """
    if not isinstance(obj, dict):
        return None
    if path.startswith("#"):
        fields = obj.get("application", {}).get("fields", {})
        for key, val in fields.items():
            if str(key).endswith(path[1:]):
                return val.get("value") if isinstance(val, dict) else val
        return None
    parts = path.split(".")
    current: Any = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def match_field_value(actual: Any, op: str, expected: Any) -> bool:
    """field 匹配操作符求值。"""
    actual_str = str(actual)
    expected_str = str(expected)
    if op in ("==", "="):
        return actual_str == expected_str
    if op == "!=":
        return actual_str != expected_str
    if op == "contains":
        return expected_str in actual_str
    if op == "startswith":
        return actual_str.startswith(expected_str)
    if op == "regex":
        try:
            return re.search(expected_str, actual_str) is not None
        except re.error:
            return False
    return False


def match_line(line: ParsedLine, match_def: MatchDef) -> MatchResult:
    """对一行执行规则匹配，返回匹配结果。

    匹配分三层：
    1. 内容主锚（text pattern / field 操作符）——硬条件
    2. 文件约束（可选）——软约束，不阻断
    3. 行号弱约束（line + line_tolerance）——仅标记漂移，不阻断
    """
    result = MatchResult()

    # 1. 内容主锚
    if match_def.mode == "text":
        if line.text is None:
            return result
        pattern = _compile(match_def.pattern or "", match_def.flags)
        m = pattern.search(line.text)
        if not m:
            return result
        result._raw_groups = m.groups()
    elif match_def.mode == "field":
        if line.fields is None:
            return result
        actual = _extract_field_value(line.fields, match_def.field or "")
        if actual is None:
            return result
        if not match_field_value(actual, match_def.op or "==", match_def.value):
            return result

    result.matched = True

    # 2. 行号弱约束（line + line_tolerance）
    if match_def.line is not None and line.line is not None:
        expected = match_def.line
        actual = line.line
        tolerance = match_def.line_tolerance
        if abs(actual - expected) > tolerance:
            result.line_drift = True
            result.line_actual = actual
            result.line_expected = expected

    return result


def apply_capture(line: ParsedLine, match_def: MatchDef, match_result: MatchResult,
                  rule_capture: Dict[str, str]) -> Dict[str, Any]:
    """按规则 capture 配置提取事件字段。

    text 模式：capture 值为捕获组序号（int）。
    field 模式：capture 值为 simple dict 字段路径（str）。
    """
    captured: Dict[str, Any] = {}

    for name, spec in rule_capture.items():
        if match_def.mode == "text":
            try:
                idx = int(spec) - 1  # 规则中序号从 1 开始（对应正则 group 1）
                if 0 <= idx < len(match_result._raw_groups):
                    captured[name] = match_result._raw_groups[idx]
            except (ValueError, TypeError):
                captured[name] = str(spec)
        elif match_def.mode == "field":
            val = _extract_field_value(line.fields or {}, str(spec))
            if val is not None:
                captured[name] = val

    return captured


def format_message(template: str, captures: Dict[str, Any]) -> str:
    """用捕获值填充事件消息模板。

    支持 {field_name} 占位符。
    """
    try:
        return template.format(**captures)
    except KeyError:
        return template