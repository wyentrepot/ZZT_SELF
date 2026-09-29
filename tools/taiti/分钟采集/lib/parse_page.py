# -*- coding: utf-8 -*-
"""分钟采集分析 — 页面导出 xlsx 读取（归一化）。

页面口径是"集中器实收"结果。注意踩坑：
- 日期格式 `2026/9/20` 不补零，必须归一化；冻结周期取分钟精度。
"""
from __future__ import annotations

import re
from collections import defaultdict

try:
    import openpyxl  # noqa: F401
    _HAS_OPENPYXL = True
except Exception:  # pragma: no cover
    _HAS_OPENPYXL = False


def _norm(s) -> str | None:
    m = re.match(r"(\d+)/(\d+)/(\d+) (\d+):(\d+)", str(s))
    if not m:
        return None
    y, mo, d, hh, mi = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d} {int(hh):02d}:{int(mi):02d}"


def read_page_xlsx(path: str) -> dict[str, set[str]]:
    """返回 {冻结周期 'YYYY-MM-DD HH:MM': set(表地址)}。

    若 openpyxl 不可用，抛 RuntimeError 并给出提示。
    """
    if not _HAS_OPENPYXL:
        raise RuntimeError("缺少 openpyxl，无法读取页面 xlsx（pip install openpyxl 或换用系统 python3）")
    wb = openpyxl.load_workbook(path, read_only=True)
    page: dict[str, set[str]] = defaultdict(set)
    for r in wb["Sheet1"].iter_rows(values_only=True):
        if len(r) < 4 or not r[3]:
            continue
        k = _norm(str(r[2]))
        if k:
            page[k[:16]].add(str(r[3]))
    return dict(page)


def page_stats(page: dict[str, set[str]]) -> list[tuple[str, int]]:
    return [(k, len(v)) for k, v in sorted(page.items())]
