"""仓库级 pytest 配置：把仓库根加入 sys.path。

拆分后包结构为 listener/、module_log/、shared/ 三个平级包，测试文件分布在各
项目内。此 conftest 确保从任意目录运行 pytest 都能 import 这些顶层包。
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
