"""sim_concentrator CLI 入口：python -m sim_concentrator <subcommand>。

支持子命令：
    verify <task.json> [--port COM3] [--baud 115200] [--json]
    responders [--json]
    ports [--json]

复用 cli.main（与 REST API 共用 execute_task 执行核心）。
"""

import sys

from sim_concentrator.cli import main

if __name__ == "__main__":
    sys.exit(main())
