#!/usr/bin/env python3
"""REQS-0032 AI 免配置入口：自插仓库 sys.path 后转调 parser_lib.cli。

用法（AI / 技能轻量档引用此路径，无需配 PYTHONPATH）：
    python3 tools/scripts/appframe.py parse  --hex "68 ..." [--protocol 645]
    python3 tools/scripts/appframe.py build  --protocol 645 --params '{...}'
    python3 tools/scripts/appframe.py verify --hex "68 ..." [--expect '{...}']
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _sub in ("apps", "libs"):
    _p = str(_REPO_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from parser_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
