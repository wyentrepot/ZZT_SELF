"""workbench.orchestration —— 编排层（无 UI 依赖，CLI/REST/AI 三端复用）。

对应 FR-5（AI 闭环验证）：Run 管理 / 统一报告 / 期望流程比对 / 归因反馈。
"""
from .compare import compare_flow  # noqa: F401
from .feedback import build_feedback  # noqa: F401
from .runner import RunExecutor  # noqa: F401
from .store import RunStore  # noqa: F401
