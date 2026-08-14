"""sim_concentrator CLI 入口：python -m sim_concentrator <subcommand>。

支持子命令：
    verify <task.json> [--port COM3] [--baud 115200] [--json]
    responders [--json]
    ports [--json]

复用 cli.main（与 REST API 共用 execute_task 执行核心）。
"""

import sys
from pathlib import Path

# 目录分层后 sim_concentrator 位于 libs/，运行 `python -m sim_concentrator`
# 时需先把 libs/ 加入 sys.path 才能定位包本身。
_REPO_ROOT = Path(__file__).resolve().parent.parent  # libs → 仓库根
for _d in ("", "apps", "libs"):
    _p = str(_REPO_ROOT / _d) if _d else str(_REPO_ROOT)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sim_concentrator.cli import main

if __name__ == "__main__":
    sys.exit(main())
