#!/usr/bin/env python3
"""厂测上位机 CLI 入口：自插仓库 libs/ 后转调 factory_test.cli。

用法：
    python3 tools/scripts/factory_test.py parse --hex "A5 FE 10 02 00 01 56 A3 8B 32"
    python3 tools/scripts/factory_test.py build --ctrl 0x10 --di 0x0100
    python3 tools/scripts/factory_test.py enter
    python3 tools/scripts/factory_test.py read  --di 0x0100
    python3 tools/scripts/factory_test.py write --di 0x0100 --data 32
    python3 tools/scripts/factory_test.py rf-call --enter
    python3 tools/scripts/factory_test.py test --enter --rf-call

默认串口为工作台串口桥 PTY /run/hplc/serial/factory（Windows COM7，9600 8N1）。
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _sub in ("apps", "libs"):
    _p = str(_REPO_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from factory_test.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
