"""检测用例库只读加载与查询（REQS-0025 G1/B1）。

数据文件 data/cases.json 由 generate.py 从蒸馏库半自动转换并随库分发；
运行时只读本文件目录下的 JSON，不做任何加工拷贝（与 dict_api 同一事实契约惯例）。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

_DATA_PATH = Path(__file__).resolve().parent / "data" / "cases.json"

_lock = threading.Lock()
_cache: Optional[dict] = None


def data_path() -> Path:
    return _DATA_PATH


def load_library() -> dict:
    """加载整个用例库（进程内缓存；文件改动后重新调用 reload 才生效）。"""
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                _cache = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return _cache


def reload_library() -> None:
    global _cache
    with _lock:
        _cache = None


def meta() -> dict:
    return load_library()["meta"]


def categories() -> list[dict]:
    return load_library()["categories"]


def entries(category: Optional[str] = None, entry_type: Optional[str] = None,
            q: Optional[str] = None) -> list[dict]:
    """按分类/条目类型/关键词过滤条目（关键词模糊匹配 id/名称/目的/帧类型/来源）。"""
    items = load_library()["entries"]
    if category:
        items = [e for e in items if e.get("category") == category]
    if entry_type:
        items = [e for e in items if e.get("entry_type") == entry_type]
    if q:
        needle = q.lower()
        items = [
            e for e in items
            if needle in json.dumps(e, ensure_ascii=False).lower()
        ]
    return items


def get_entry(entry_id: str) -> Optional[dict]:
    for e in load_library()["entries"]:
        if e.get("id") == entry_id:
            return e
    return None


def param_tables(group: Optional[str] = None) -> list[dict]:
    """参数表行（entry_type=param_table）：测试模式 1~13、安全测试模式 1~12、检测线扩展等。"""
    items = [e for e in load_library()["entries"] if e.get("entry_type") == "param_table"]
    if group:
        items = [e for e in items if e.get("group") == group]
    return items
