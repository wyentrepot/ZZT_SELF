"""REQS-0036：v2 read_job 的 wait_seconds 阻塞等待 + verification_run 信封 summary。

此前 read_job 静默忽略 wait_seconds（接口声明 0-30 却立即返回 running 快照），
且 verification_run 终态 summary 停留在任务状态（"succeeded"），AI 只读信封
会把 succeeded 误当验证通过。用 stub store 直接对 AICapabilityService 断言。
"""
import time

from workbench.ai_capability_service import AICapabilityService


class _StubStore:
    def __init__(self, op):
        self._op = dict(op)
        self.calls = 0

    def get(self, op_id):
        self.calls += 1
        return dict(self._op)


class _StubControl:
    def __init__(self, op):
        self.store = _StubStore(op)


def _verification_op(state="running", summary=None):
    result = {"underlying_operation_id": "op-x"}
    if summary is not None:
        result["result"] = {"summary": summary}
    return {
        "operation_id": "op-x",
        "kind": "verification_run",
        "state": state,
        "result": result,
    }


def test_read_job_waits_for_terminal_when_wait_seconds_positive():
    svc = AICapabilityService(_StubControl(_verification_op(state="running")))
    t0 = time.monotonic()
    env = svc.read_job("job-x", wait_seconds=1)
    assert time.monotonic() - t0 >= 0.9
    assert env.job_state.value == "running"


def test_read_job_terminal_returns_immediately():
    op = _verification_op(
        state="succeeded",
        summary={"total": 1, "pass": 0, "fail": 1, "verdict": "fail"},
    )
    svc = AICapabilityService(_StubControl(op))
    t0 = time.monotonic()
    env = svc.read_job("job-x", wait_seconds=5)
    assert time.monotonic() - t0 < 1.0
    assert env.job_state.value == "succeeded"


def test_read_job_summary_carries_verification_verdict():
    """succeeded 只代表任务跑完；summary 必须透出步骤判定 fail/pass N/M。"""
    op = _verification_op(
        state="succeeded",
        summary={"total": 1, "pass": 0, "fail": 1, "verdict": "fail"},
    )
    env = AICapabilityService(_StubControl(op)).read_job("job-x")
    assert "fail (0 pass/1 fail)" in (env.summary or "")


def test_read_job_wakes_early_when_store_turns_terminal():
    class _FlipStore:
        def __init__(self):
            self.n = 0

        def get(self, op_id):
            self.n += 1
            if self.n <= 2:
                return _verification_op(state="running")
            return _verification_op(
                state="succeeded",
                summary={"total": 1, "pass": 1, "fail": 0, "verdict": "pass"},
            )

    control = type("C", (), {"store": _FlipStore()})()
    t0 = time.monotonic()
    env = AICapabilityService(control).read_job("job-x", wait_seconds=5)
    assert time.monotonic() - t0 < 1.0
    assert "pass (1 pass/0 fail)" in (env.summary or "")
