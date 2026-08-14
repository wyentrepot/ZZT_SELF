"""python -m loghooks 入口。

目录分层后 loghooks 位于 libs/，运行 `python -m loghooks` 时需先把 libs/
加入 sys.path 才能定位包本身。
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent  # libs → 仓库根
for _d in ("", "apps", "libs"):
    _p = str(_REPO_ROOT / _d) if _d else str(_REPO_ROOT)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .cli import main

if __name__ == "__main__":
    sys.exit(main())