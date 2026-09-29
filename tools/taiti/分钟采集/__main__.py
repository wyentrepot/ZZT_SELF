# -*- coding: utf-8 -*-
"""分钟采集离线分析 CLI（python -m 入口，等价 run.py）。

用法见 run.py 的 __doc__。
"""
from __future__ import annotations

import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
