#!/usr/bin/env python3
"""需求 0041 黑盒用例入口：自插仓库 sys.path 后转调 case_runner.cli。

用户视角只有一句话：**只输入参数，拿测试结果**。

用法（免配 PYTHONPATH）：
    python3 tools/scripts/case_run.py list [--blocks|--facts|--cases|--suites] [--json]
    python3 tools/scripts/case_run.py show <case_id|block_id|fact_id|suite_id>
    python3 tools/scripts/case_run.py run --case <case_id> [--params '<json>'] [--base-url URL] [--json]
    python3 tools/scripts/case_run.py run --suite <suite_id> [--params '<json>'] [--json]
    python3 tools/scripts/case_run.py probe --all [--json]
    python3 tools/scripts/case_run.py probe --block <block_id> / --fact <fact_id>
    python3 tools/scripts/case_run.py check [--json]

工作台地址（REQS 0042 D2-3 四级服务发现，本入口不内置任何端口）：
    1. --base-url
    2. 环境变量 WORKBENCH_BASE
    3. data/runtime/workbench.json（工作台启动时自己写；含 base_url/port/pid）
    4. 兜底本机默认端口（端口值只在 case_runner.cli.DEFAULT_PORT 一处定义，本入口不出现字面量）
    因此「零参可跑」：选好用例直接 run，不需要传工作台地址。

退出码：0 = 全部 pass；1 = 有 fail；2 = 有 unknown/skipped 但无 fail（未完成验证）；
        3 = 用法/参数错误；4 = 声明加载失败。
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _sub in ("apps", "libs"):
    _p = str(_REPO_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from case_runner.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
