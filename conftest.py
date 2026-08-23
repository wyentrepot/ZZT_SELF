"""仓库级 pytest 配置：把仓库根、apps/、libs/ 加入 sys.path。

目录分层后包结构为 apps/（listener、module_log）与 libs/（shared、parser_lib、
loghooks、sim_concentrator），测试文件分布在各项目内。此 conftest 确保从任意
目录运行 pytest 都能 import 这些顶层包（导入名保持 shared.* / listener.* 不变）。
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
for _sub in ("", "apps", "libs"):
    _p = os.path.join(_REPO_ROOT, _sub) if _sub else _REPO_ROOT
    if _p not in sys.path:
        sys.path.insert(0, _p)


def hplc_test_data_root():
    """Return external HPLC fixtures when configured, else the legacy root.

    This helper is intentionally test-only: application packages must never
    discover or read fixture data through an environment variable.
    """
    configured = os.environ.get("HPLC_TEST_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(_REPO_ROOT) / "测试文件"

from shared.infra import ensure_paths  # noqa: E402

ensure_paths()
