"""sim_meter CLI 入口：python -m sim_meter <subcommand>。

复用 cli.main；目录分层后 sim_meter 位于 libs/，运行时先把仓库根、
apps/、libs/ 加入 sys.path 再定位包（与 sim_concentrator.__main__ 一致）。
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent  # libs → 仓库根
for _d in ("", "apps", "libs"):
    _p = str(_REPO_ROOT / _d) if _d else str(_REPO_ROOT)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sim_meter.cli import main

if __name__ == "__main__":
    sys.exit(main())
