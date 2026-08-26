# -*- coding: utf-8 -*-
"""hf-collect-analysis 技能入口：包装 tools/taiti/高频采集/run.py。

用法（与工具 CLI 一致）：
    python run_analysis.py taish <台体日志>
    python run_analysis.py cco <CCO日志> <表地址>... [--start ..] [--end ..]
    python run_analysis.py sniff <侦听台报文> <表地址>... [--start ..] [--end ..]
    python run_analysis.py cross <CCO日志> <侦听台报文> <表地址>... [--start ..] [--end ..]
"""
from __future__ import annotations

import sys
from pathlib import Path

# 仓库根 = skills/hf-collect-analysis/scripts -> parents[3]
_REPO = Path(__file__).resolve().parents[3]
_HF = _REPO / "tools" / "taiti" / "高频采集"
if str(_HF) not in sys.path:
    sys.path.insert(0, str(_HF))
for _sub in ("台体", "CCO", "侦听台"):
    _p = str(_HF / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run as cli  # noqa: E402


def main(argv=None) -> int:
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
