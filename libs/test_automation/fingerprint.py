"""用例包不可变指纹（docs/03 §4：加载时计算不可变指纹）。

``case_fingerprint(case)`` 将 CasePackage 规范化后取 sha256：
- 使用 sort_keys 使结果与键顺序无关；
- 内部对象按模型字段序列化（pydantic ``model_dump``），再整体规范化；
- 因此内容（含参数、断言顺序）变化会导致指纹变化。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import CasePackage


def _normalize(value: Any) -> str:
    """将任意可 JSON 序列化值规范化为字符串（键排序、紧凑、UTF-8）。"""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def case_fingerprint(case: CasePackage) -> str:
    """计算用例包的不可变 sha256 指纹（64 位十六进制小写）。"""
    payload = _normalize(case.model_dump())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
