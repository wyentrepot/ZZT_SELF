"""libs.case_library —— 国网双模检测用例库（REQS-0025 G1/B1）。

- data/cases.json：generate.py 从蒸馏库 06_测试用例.md 半自动转换的只读资产（随库分发）
- library.py：加载/查询（dict_api /api/dict/cases 的数据层）
- generate.py：再生成脚本（--src 指定蒸馏 md；南网/ 目录禁止引用）
"""
from .library import (categories, entries, get_entry, load_library, meta,
                      param_tables, reload_library)

__all__ = ["categories", "entries", "get_entry", "load_library", "meta",
           "param_tables", "reload_library"]
