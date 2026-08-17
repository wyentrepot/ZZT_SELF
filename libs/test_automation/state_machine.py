"""Run 状态机（docs/03 §3、§8）。

规则：
- 合法迁移由 RUN_TRANSITIONS 定义；``from == to`` 允许（幂等停留/刷新）。
- 终态（TERMINAL = cancelled/passed/failed/inconclusive/error）不可回退，
  任何指向终态之外的迁移、或从终态出发的迁移都抛 ValueError。
"""
from __future__ import annotations

from .models import RunStatus, TERMINAL_STATUSES

#: 合法迁移表：from -> set(to)
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.CREATED, RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({RunStatus.QUEUED, RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.PASSED,
            RunStatus.FAILED,
            RunStatus.INCONCLUSIVE,
            RunStatus.CANCELLING,
            RunStatus.ERROR,
        }
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLING, RunStatus.CANCELLED, RunStatus.ERROR}),
    # 终态：只允许原地停留（幂等），不可回退到任何其他状态
    RunStatus.CANCELLED: frozenset({RunStatus.CANCELLED}),
    RunStatus.PASSED: frozenset({RunStatus.PASSED}),
    RunStatus.FAILED: frozenset({RunStatus.FAILED}),
    RunStatus.INCONCLUSIVE: frozenset({RunStatus.INCONCLUSIVE}),
    RunStatus.ERROR: frozenset({RunStatus.ERROR}),
}

#: 终态集合（复用 models.TERMINAL_STATUSES 保证单一事实来源）
TERMINAL = frozenset(TERMINAL_STATUSES)


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    """校验并执行状态迁移，返回目标状态；非法迁移抛 ValueError。"""
    if current not in RUN_TRANSITIONS:
        raise ValueError(f"未知状态 {current!r}")
    allowed = RUN_TRANSITIONS[current]
    if target not in allowed:
        raise ValueError(f"非法迁移 {current.value} -> {target.value}（允许：{sorted(s.value for s in allowed)}）")
    return target
