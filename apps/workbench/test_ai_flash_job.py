"""P0-4 / P0-2：flash_job 的幂等受理与异步烧录。

真机两次事故：
- P0-4：CCO 21:44 起烧成功，客户端 `curl -m 60` 超时后用同一 client_request_id
  重提 → 又烧一次 → `Bootloader prompt was not detected`。根因是幂等键被 pop 掉
  丢了、且父 job 在整个阻塞期一直是 created，重提命中不了「已有任务」。
- P0-2：flash_module 同步阻塞（实测 121s），HTTP 响应等烧完才回，客户端拿不到
  job_id，只能盲重提。

这里用真实 OperationStore + AIControlService + 假 module service 端到端断言：
重提只烧一次、返回同一 job_id、同键不同参数仍 409、受理立即返回、不可取消、
不可并发、底层失败/后台异常都能让父 job 落可读的 error。
"""
from __future__ import annotations

import threading
import time

import pytest

from workbench.ai_capability_service import AICapabilityService
from workbench.ai_contracts import AccessContext, AccessZone, JobState
from workbench.ai_operations import AIControlService, SessionBusy
from workbench.ai_store import TERMINAL_STATES, IdempotencyConflict


class _FakeFlashModule:
    """只实现 flash_module 需要的方法；flash_session 可阻塞（模拟 121s）可失败。"""

    def __init__(self, *, gate: threading.Event | None = None, error: str = ""):
        self.sessions = {
            "ms-cco": {
                "session_id": "ms-cco", "title": "CCO 主模块", "module": "cco",
                "state": "running", "port": "COM8",
                "port_identity": {"mapping_id": "cco-main"},
                "log_file": "/tmp/cco.log",
                "flash": {"flashing": False, "phase": "idle", "message": ""},
            },
        }
        self.flash_calls: list[tuple] = []
        self.gate = gate
        self.error = error

    def list_sessions(self):
        return list(self.sessions.values())

    def get_session(self, session_id):
        return self.sessions[session_id]

    def status(self):
        return {"sessions": self.list_sessions()}

    def flash_session(self, session_id, bin_path, slot=0, baud_plan=None, no_reboot_after=False):
        self.flash_calls.append((session_id, bin_path, slot))
        if self.gate is not None:
            assert self.gate.wait(timeout=10), "测试没有及时放行烧录闸门"
        if self.error:
            self.sessions[session_id]["flash"] = {
                "flashing": False, "phase": "error", "message": self.error,
            }
            raise RuntimeError(self.error)
        self.sessions[session_id]["flash"] = {
            "flashing": False, "phase": "done", "message": "success",
        }
        return {"session_id": session_id,
                "flash": dict(self.sessions[session_id]["flash"])}


def _context() -> AccessContext:
    return AccessContext(zone=AccessZone.LOCAL_FULL, actor="ai:flash-test")


def _service(**kwargs):
    module = _FakeFlashModule(**kwargs)
    control = AIControlService(module_service=module)
    return AICapabilityService(control), module, control


def _op_id(job_id: str) -> str:
    assert job_id.startswith("job-")
    return "op-" + job_id[4:]


def _wait_until(predicate, timeout: float = 5.0, what: str = "条件") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"{what} 未在 {timeout}s 内成立")


def _wait_terminal(control, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        operation = control.store.get(_op_id(job_id))
        if operation["state"] in TERMINAL_STATES:
            return operation
        time.sleep(0.01)
    raise AssertionError(f"{job_id} 未在 {timeout}s 内进入终态："
                         f"{control.store.get(_op_id(job_id))['state']}")


def _flash_body(bin_path: str = "/fw/cco.bin", key: str = "flash-1", **extra) -> dict:
    return {"session_id": "ms-cco", "bin_path": bin_path,
            "slot": 0, "client_request_id": key, **extra}


def test_replay_returns_same_job_and_burns_only_once():
    """同键重提：同一 job_id，且底层 flash_session 全程只被调用一次。"""
    gate = threading.Event()
    service, module, control = _service(gate=gate)
    try:
        first = service.start_flash_job(_flash_body(), context=_context())
        _wait_until(lambda: len(module.flash_calls) == 1, what="第一次烧录启动")
        # 第一次还在烧（闸门未放行）时立刻重提同一个键
        replay = service.start_flash_job(_flash_body(), context=_context())
        assert replay.job_id == first.job_id
        assert replay.job_state is JobState.RUNNING
        gate.set()
        _wait_terminal(control, first.job_id)
    finally:
        gate.set()
    assert len(module.flash_calls) == 1
    assert control.store.get(_op_id(first.job_id))["state"] == "succeeded"
    # 幂等键确实透传到了内层 module_flash（不是 pop 完就丢）
    inner = control.store.by_client_request_id("flash-1:module_flash")
    assert inner is not None and inner["kind"] == "module_flash"


def test_replay_with_different_params_conflicts():
    """同键不同参数仍按既有 IdempotencyConflict 报冲突，且不触发第二次烧录。"""
    gate = threading.Event()
    service, module, control = _service(gate=gate)
    try:
        first = service.start_flash_job(_flash_body(bin_path="/fw/a.bin"), context=_context())
        _wait_until(lambda: len(module.flash_calls) == 1, what="第一次烧录启动")
        with pytest.raises(IdempotencyConflict, match="幂等键与既有请求不一致"):
            service.start_flash_job(_flash_body(bin_path="/fw/b.bin"), context=_context())
        assert len(module.flash_calls) == 1
        gate.set()
        _wait_terminal(control, first.job_id)
    finally:
        gate.set()


def test_submit_returns_before_the_burn_finishes():
    """受理立即返回 202 级信封（running），烧录在后台跑；终态由监视线程推进。"""
    gate = threading.Event()
    service, module, control = _service(gate=gate)
    try:
        started = time.monotonic()
        envelope = service.start_flash_job(_flash_body(key="flash-async"), context=_context())
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"受理被阻塞了 {elapsed:.2f}s"
        assert envelope.job_state is JobState.RUNNING
        assert control.store.get(_op_id(envelope.job_id))["state"] == "waiting"
        _wait_until(lambda: len(module.flash_calls) == 1, what="后台烧录启动")
        assert control.store.get(_op_id(envelope.job_id))["state"] == "waiting"
        gate.set()
        operation = _wait_terminal(control, envelope.job_id)
    finally:
        gate.set()
    assert operation["state"] == "succeeded"
    assert operation["result"]["underlying_state"] == "succeeded"


def test_second_flash_on_same_session_is_rejected():
    """烧录不可并发：同会话第二次提交（换幂等键）在受理阶段就被拒。"""
    gate = threading.Event()
    service, module, control = _service(gate=gate)
    try:
        first = service.start_flash_job(_flash_body(key="flash-a"), context=_context())
        _wait_until(lambda: len(module.flash_calls) == 1, what="第一次烧录启动")
        with pytest.raises(SessionBusy, match="烧录不可并发"):
            service.start_flash_job(_flash_body(key="flash-b"), context=_context())
        assert len(module.flash_calls) == 1
        gate.set()
        _wait_terminal(control, first.job_id)
    finally:
        gate.set()


def test_flash_job_cancel_is_explicitly_rejected():
    """烧录不可取消：cancel_job 对 flash_job 仍然显式拒绝，且不改变父 job 状态。"""
    gate = threading.Event()
    service, module, control = _service(gate=gate)
    try:
        envelope = service.start_flash_job(_flash_body(key="flash-cancel"), context=_context())
        with pytest.raises(SessionBusy, match="烧录或模拟集中器验证任务不可取消"):
            service.cancel_job(envelope.job_id, actor="ai:flash-test")
        assert control.store.get(_op_id(envelope.job_id))["state"] == "waiting"
        gate.set()
        assert _wait_terminal(control, envelope.job_id)["state"] == "succeeded"
    finally:
        gate.set()


def test_backend_failure_lands_on_parent_with_readable_error():
    """底层失败（bootloader 未响应）必须让父 job 落 error，原因可读不吞异常。"""
    service, module, control = _service(error="Bootloader prompt was not detected")
    envelope = service.start_flash_job(_flash_body(key="flash-err"), context=_context())
    operation = _wait_terminal(control, envelope.job_id)

    assert operation["state"] == "error"
    assert "Bootloader prompt was not detected" in (operation["error"] or "")
    read_back = service.read_job(envelope.job_id)
    assert read_back.job_state is JobState.FAILED
    assert "Bootloader prompt was not detected" in (read_back.summary or "")
    assert len(module.flash_calls) == 1


def test_backend_exception_does_not_leave_parent_waiting():
    """后台线程里 flash_module 直接抛（未落 operation）时，父 job 不能停在 waiting。"""
    control = AIControlService(module_service=None)  # flash_module 立即抛 SourceUnavailable
    service = AICapabilityService(control)

    envelope = service.start_flash_job(_flash_body(key="flash-no-service"), context=_context())
    operation = _wait_terminal(control, envelope.job_id)

    assert operation["state"] == "error"
    assert "模块日志服务不可用" in (operation["error"] or "")
    assert operation["result"]["error"] == "模块日志服务不可用"
