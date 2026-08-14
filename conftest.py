"""仓库级 pytest 配置：把仓库根、apps/、libs/ 加入 sys.path。

目录分层后包结构为 apps/（listener、module_log）与 libs/（shared、parser_lib、
loghooks、sim_concentrator），测试文件分布在各项目内。此 conftest 确保从任意
目录运行 pytest 都能 import 这些顶层包（导入名保持 shared.* / listener.* 不变）。
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
for _sub in ("", "apps", "libs"):
    _p = os.path.join(_REPO_ROOT, _sub) if _sub else _REPO_ROOT
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.infra import ensure_paths  # noqa: E402

ensure_paths()
