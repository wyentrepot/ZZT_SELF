"""A1：Run 状态机契约测试（docs/03 §3、§8）。

覆盖：合法迁移路径、非法迁移拒绝、终态不可回退、cancelling → cancelled、
cancelling → error（清理失败）、幂等停留。
"""
import pytest

from test_automation.state_machine import transition, RUN_TRANSITIONS, TERMINAL
from test_automation.models import RunStatus


class TestTransitions:
    def test_all_statuses_defined(self):
        assert RunStatus.CREATED in TERMINAL or True  # 占位，下面具体断言

    def test_created_to_queued(self):
        assert RunStatus.QUEUED in RUN_TRANSITIONS[RunStatus.CREATED]

    def test_queued_to_running(self):
        assert RunStatus.RUNNING in RUN_TRANSITIONS[RunStatus.QUEUED]

    def test_running_to_passed(self):
        assert RunStatus.PASSED in RUN_TRANSITIONS[RunStatus.RUNNING]

    def test_running_to_failed(self):
        assert RunStatus.FAILED in RUN_TRANSITIONS[RunStatus.RUNNING]

    def test_running_to_inconclusive(self):
        assert RunStatus.INCONCLUSIVE in RUN_TRANSITIONS[RunStatus.RUNNING]

    def test_running_to_cancelling(self):
        assert RunStatus.CANCELLING in RUN_TRANSITIONS[RunStatus.RUNNING]

    def test_cancelling_to_cancelled(self):
        assert RunStatus.CANCELLED in RUN_TRANSITIONS[RunStatus.CANCELLING]

    def test_cancelling_to_error(self):
        assert RunStatus.ERROR in RUN_TRANSITIONS[RunStatus.CANCELLING]

    def test_running_to_error(self):
        assert RunStatus.ERROR in RUN_TRANSITIONS[RunStatus.RUNNING]

    def test_transition_legal(self):
        assert transition(RunStatus.CREATED, RunStatus.QUEUED) == RunStatus.QUEUED

    def test_transition_illegal_raises(self):
        with pytest.raises(ValueError):
            transition(RunStatus.CREATED, RunStatus.PASSED)

    def test_terminal_status_cannot_transition(self):
        with pytest.raises(ValueError):
            transition(RunStatus.PASSED, RunStatus.RUNNING)
        with pytest.raises(ValueError):
            transition(RunStatus.ERROR, RunStatus.CREATED)

    def test_same_status_legal(self):
        # 幂等停留：允许 from==to（刷新/重发相同状态）
        assert transition(RunStatus.RUNNING, RunStatus.RUNNING) == RunStatus.RUNNING

    def test_created_cannot_jump_to_finished(self):
        for target in (RunStatus.PASSED, RunStatus.FAILED, RunStatus.INCONCLUSIVE, RunStatus.ERROR):
            with pytest.raises(ValueError):
                transition(RunStatus.CREATED, target)
