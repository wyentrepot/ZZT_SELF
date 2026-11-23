"""自愈测试（REQS-0042 §3.4 / 变更 2 D2-7 / SH-1..3，按 D7-1 用新口径写）。

口径（用户原话，写在 ``common.py`` 模块 docstring 里作为代码审查项）：

    「跑测试用例时，是主动调用跑的，这时候如果有占用，那应当以自动化测试为主，
      不看归属，直接抢占为自己所用即可。」

本文件守住的四件事：

1. 占用类（409）→ close 释放 → **重试一次**；成功即继续，失败才交上层；
2. **不自愈成死循环**：close 最多 1 次、重试最多 1 次（传更大的 retry 也不放大）；
3. 非占用类错误（422 参数错等）**一次都不自愈**（close 零调用）；
4. 留痕可见：``BlockResult`` / ``BlockRecord`` / ``CaseRunResult.to_dict()["self_healed"]``。

全部用注入的假对象，零真实 HTTP、零 sleep、零台架动作。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from case_runner.blocks import common
from case_runner.core.executor import (
    SELF_HEALED_KEY,
    BlockResult,
    RunContext,
    execute_case,
)
from case_runner.core.models import BLOCK_PASS, BLOCK_SKIPPED, CaseSpec, CaseStep
from case_runner.core.registry import clear_registries, register_block

BLOCKS_DIR = Path(__file__).resolve().parent

#: 占用类 409 的原始错误文本（真机实测：``已有模拟集中器任务在运行：actor=…``）
CONFLICT_409 = "已有模拟集中器任务在运行：actor=alice"
CONFLICT_409_WITH_ACTOR = {
    "ok": False,
    "error": "http_409",
    "detail": CONFLICT_409,
    "steps": [],
}


@pytest.fixture(autouse=True)
def _isolate():
    clear_registries()
    yield
    clear_registries()


# --------------------------------------------------------------------------- #
# 假对象（close 能力一律注入，不走 HTTP —— §3.4「可注入」要求）
# --------------------------------------------------------------------------- #


class FakeSimcon:
    """假 simcon 能力：前 ``conflict_times`` 次 verify 回 409，close 可计数。"""

    def __init__(self, conflict_times: int = 1, *, close_result: Any = None,
                 mapping_id: str = "simcon") -> None:
        self.conflict_times = int(conflict_times)
        self.close_result = close_result if close_result is not None else {
            "ok": True, "actor": "alice", "session_id": "sess-9",
            "detail": "已关闭模拟集中器会话",
        }
        self.mapping_id = mapping_id
        self.verify_calls = 0
        self.close_calls = 0

    def verify(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.verify_calls += 1
        if self.verify_calls <= self.conflict_times:
            return dict(CONFLICT_409_WITH_ACTOR)
        return {"ok": True, "job_id": "job-1", "steps": [{"name": "f1", "result": "pass"}]}

    def close(self) -> Any:
        self.close_calls += 1
        return dict(self.close_result) if isinstance(self.close_result, dict) else self.close_result


class FakeSimeter:
    """假 simeter 能力：表口未开，前 ``conflict_times`` 次 open 回 409。"""

    def __init__(self, conflict_times: int = 1, *, close_result: Optional[Dict[str, Any]] = None):
        self.conflict_times = int(conflict_times)
        self.close_result = close_result if close_result is not None else {
            "ok": True, "status": {"closed": True, "port": "/dev/ttyFAKE0"},
        }
        self.serial_open = False
        self.open_calls = 0
        self.close_calls = 0

    def status(self) -> Dict[str, Any]:
        # 与 SimeterCapability.status() 归一后同形（假对象替的是**能力**，不是 HTTP）
        return {"ok": True, "serial_open": self.serial_open,
                "port": "/dev/ttyFAKE0" if self.serial_open else None,
                "addr": "000000000001", "version": "645-2007"}

    def open_port(self, mapping_id: Optional[str] = None) -> Dict[str, Any]:
        self.open_calls += 1
        if self.open_calls <= self.conflict_times:
            return {"ok": False, "error": "http_409", "detail": "串口被其他会话持有"}
        self.serial_open = True
        return {"ok": True, "status": {"open": True}}

    def close_port(self) -> Dict[str, Any]:
        self.close_calls += 1
        self.serial_open = False
        return dict(self.close_result)


# --------------------------------------------------------------------------- #
# SH-1：simcon 409 → close 抢占 → 重试一次 → 继续（pass/fail 由判据决定）
# --------------------------------------------------------------------------- #


def test_simcon_409_is_preempted_once_then_verify_succeeds():
    cap = FakeSimcon(conflict_times=1)
    result = common.call_verify(cap, [{"name": "f1"}])

    assert cap.close_calls == 1, "占用一次就 close 一次（不看占用方是谁）"
    assert cap.verify_calls == 2, "close 之后重试一次"
    assert result["ok"] is True, "抢占成功后重试应拿到真实结果，块态由判据决定"

    healed = result[SELF_HEALED_KEY]
    assert len(healed) == 1
    item = healed[0]
    assert set(item) == {"action", "target", "result", "detail"}
    assert item["action"] == "simcon.close"
    assert item["target"] == "simcon:simcon"
    assert item["result"] == common.SELF_HEAL_HEALED
    assert "抢占释放" in item["detail"]
    assert "actor=alice" in item["detail"], "detail 要写清「抢占了谁」以便事后追溯"
    assert "不还原占用方会话" in item["detail"]


def test_simcon_409_twice_stays_skipped_with_original_error():
    cap = FakeSimcon(conflict_times=2)
    result = common.call_verify(cap, [{"name": "f1"}])
    state, why = common.verify_failure_state(result)

    assert cap.close_calls == 1
    assert cap.verify_calls == 2
    assert state == BLOCK_SKIPPED, "三态语义不变：仍被占用仍是既有 skipped（S3 才映射 blocked）"
    assert "409" in why
    assert CONFLICT_409 in why, "原始 409 文本必须留在 detail 里"
    assert "已抢占一次仍失败" in why
    assert result[SELF_HEALED_KEY][0]["result"] == common.SELF_HEAL_FAILED


# --------------------------------------------------------------------------- #
# SH-1：不自愈死循环（retry 上限 1，传更大值也不放大；传 0 则不动手）
# --------------------------------------------------------------------------- #


def test_self_heal_never_loops_even_if_retry_is_large():
    cap = FakeSimcon(conflict_times=5)
    result = common.call_verify(cap, [{"name": "f1"}], retry=99)

    assert cap.close_calls == 1, "close 只调 1 次"
    assert cap.verify_calls == 2, "重试只 1 次（首次 + 重试）"
    assert result["error"] == "http_409"
    assert common.SELF_HEAL_MAX_RETRY == 1


def test_retry_zero_does_not_touch_close():
    cap = FakeSimcon(conflict_times=1)
    result = common.call_verify(cap, [{"name": "f1"}], retry=0)

    assert cap.close_calls == 0
    assert cap.verify_calls == 1
    assert result["error"] == "http_409"


def test_missing_close_capability_is_traced_not_guessed():
    class NoClose:
        def __init__(self) -> None:
            self.verify_calls = 0

        def verify(self, steps):
            self.verify_calls += 1
            return dict(CONFLICT_409_WITH_ACTOR)

    cap = NoClose()
    result = common.call_verify(cap, [{"name": "f1"}])

    assert cap.verify_calls == 1, "没有 close 能力就不许瞎试（不猜、不硬编码 HTTP）"
    item = result[SELF_HEALED_KEY][0]
    assert item["result"] == common.SELF_HEAL_UNAVAILABLE
    assert "没有可用的" in item["detail"]


# --------------------------------------------------------------------------- #
# SH-1：非占用类错误一律不自愈
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("error", ["http_422", "http_400", "poll_timeout",
                                   "unreachable", "bad_response"])
def test_non_conflict_error_never_heals(error: str):
    class Failing:
        def __init__(self) -> None:
            self.verify_calls = 0
            self.close_calls = 0

        def verify(self, steps):
            self.verify_calls += 1
            return {"ok": False, "error": error, "detail": "参数不对/服务不可用", "steps": []}

        def close(self):
            self.close_calls += 1
            return {"ok": True}

    cap = Failing()
    result = common.call_verify(cap, [{"name": "f1"}])

    assert cap.close_calls == 0, "非占用类错误不触发自愈"
    assert cap.verify_calls == 1, "非占用类错误一次都不重试"
    assert SELF_HEALED_KEY not in result, "没有自愈就不该有留痕"


def test_is_conflict_error_only_matches_conflict():
    assert common.is_conflict_error({"ok": False, "error": "http_409"})
    assert common.is_conflict_error({"ok": False, "outcome": common.SIMETER_SESSION_CONFLICT})
    assert not common.is_conflict_error({"ok": True})
    assert not common.is_conflict_error({"ok": False, "error": "http_422"})
    assert not common.is_conflict_error(None)


# --------------------------------------------------------------------------- #
# SH-1：simeter 端口占用（SIMETER_SESSION_CONFLICT）→ close_port → 重试一次
# --------------------------------------------------------------------------- #


def test_simeter_conflict_closes_port_then_reopens():
    cap = FakeSimeter(conflict_times=1)
    gate = common.ensure_simeter_session(cap, mapping_id="simeter")

    assert cap.close_calls == 1
    assert cap.open_calls == 2
    assert gate["outcome"] == common.SIMETER_SESSION_OPENED
    assert gate["opened"] is True
    assert "抢占" in gate["detail"]

    healed = gate["self_healed"]
    assert len(healed) == 1
    assert healed[0]["action"] == "simeter.close_port"
    assert healed[0]["target"] == "simeter:simeter"
    assert healed[0]["result"] == common.SELF_HEAL_HEALED


def test_simeter_conflict_twice_stays_conflict_and_names_preemption():
    cap = FakeSimeter(conflict_times=5)
    gate = common.ensure_simeter_session(cap, mapping_id="simeter")

    assert cap.close_calls == 1
    assert cap.open_calls == 2, "只重试一次"
    assert gate["outcome"] == common.SIMETER_SESSION_CONFLICT
    assert "端口被占用" in gate["detail"]
    assert "已抢占一次仍失败" in gate["detail"], "detail 要说明「已抢占一次仍失败」"
    assert "串口被其他会话持有" in gate["detail"], "原始 409 文本要留档"
    assert gate["self_healed"][0]["result"] == common.SELF_HEAL_FAILED


# --------------------------------------------------------------------------- #
# 可注入性：close 能力从能力实例解析，绝不硬编码 HTTP（SH-3）
# --------------------------------------------------------------------------- #


def test_capability_closer_resolves_per_kind():
    class WithClose:
        def close(self):
            return {"ok": True}

    class WithClosePort:
        def close_port(self):
            return {"ok": True}

    assert common.capability_closer(WithClose()) is not None
    assert common.capability_closer(WithClose(), common.SIMCON_CLOSER_NAMES) is not None
    assert common.capability_closer(WithClose(), common.SIMETER_CLOSER_NAMES) is None
    assert common.capability_closer(WithClosePort(), common.SIMETER_CLOSER_NAMES) is not None
    assert common.capability_closer(object()) is None


def test_close_exception_does_not_break_the_block():
    class BoomClose(FakeSimcon):
        def close(self):
            self.close_calls += 1
            raise OSError("close 通道断了")

    cap = BoomClose(conflict_times=1)
    result = common.call_verify(cap, [{"name": "f1"}])

    assert cap.close_calls == 1
    assert cap.verify_calls == 2, "close 失败也照既定策略重试一次，不再放大"
    assert result["ok"] is True
    assert "close 通道断了" in result[SELF_HEALED_KEY][0]["detail"]


# --------------------------------------------------------------------------- #
# SH-2：留痕聚合到 CaseRunResult.to_dict()（块一行不改也能留痕）
# --------------------------------------------------------------------------- #


def _register_healing_block(cap: Any, block_id: str = "b.heal") -> str:
    """注册一个「走 simcon 提交」的块：正常路径会把 call_verify 的留痕带进结果。"""

    def _block(params: Dict[str, Any], ctx: RunContext) -> BlockResult:
        verified = common.call_verify(cap, [{"name": "f1"}])
        if verified.get("ok"):
            return BlockResult(state=BLOCK_PASS, detail="verify 通过（自愈后重试成功）")
        state, why = common.verify_failure_state(verified)
        return BlockResult(state=state, detail=why)

    register_block(block_id, _block)
    return block_id


def test_case_run_result_to_dict_exposes_self_healed():
    cap = FakeSimcon(conflict_times=1)
    block_id = _register_healing_block(cap)
    case = CaseSpec(
        id="nwk_join",
        title="自愈留痕",
        steps=[CaseStep(no=1, do="下发", expect="回帧")],
        blocks=[block_id],
    )
    result = execute_case(case, RunContext(run_id="run-heal"))

    assert result.verdict == "pass"
    record = result.blocks[0]
    assert len(record.self_healed) == 1, "块没写 BlockResult.self_healed 也要留痕（执行器收集器）"

    data = result.to_dict()
    assert SELF_HEALED_KEY in data
    entries = data[SELF_HEALED_KEY]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "simcon.close"
    assert entry["result"] == common.SELF_HEAL_HEALED
    # 聚合口径补上回指字段，便于「哪一步抢占了谁」
    assert entry["step_id"] == block_id
    assert entry["block_id"] == block_id
    assert data["blocks"][0][SELF_HEALED_KEY] == record.self_healed


def test_case_run_result_aggregates_self_healed_flat_across_steps():
    cap = FakeSimcon(conflict_times=1)
    block_id = _register_healing_block(cap, "b.heal")
    case = CaseSpec(
        id="nwk_join",
        title="两步各自自愈",
        steps=[CaseStep(no=1, do="下发", expect="回帧")],
        # 同一个块跑两次：第二次 verify 已成功，不会再自愈 → 扁平列表仍是 1 条
        blocks=[block_id, block_id],
    )
    result = execute_case(case, RunContext(run_id="run-heal-2"))

    data = result.to_dict()
    assert len(data["blocks"]) == 2
    assert len(data[SELF_HEALED_KEY]) == 1
    assert data[SELF_HEALED_KEY][0]["block_id"] == block_id


def test_block_result_carries_declared_self_healed_into_record():
    """块自己填 ``BlockResult.self_healed`` 也算数（与收集器合并且去重）。"""
    entry = {"action": "simcon.close", "target": "simcon:x",
             "result": common.SELF_HEAL_HEALED, "detail": "块自己声明的"}

    def _block(params: Dict[str, Any], ctx: RunContext) -> BlockResult:
        return BlockResult(state=BLOCK_PASS, detail="ok", self_healed=[dict(entry), dict(entry)])

    register_block("b.declared", _block)
    case = CaseSpec(
        id="c1", title="自带留痕",
        steps=[CaseStep(no=1, do="x", expect="y")],
        blocks=["b.declared"],
    )
    result = execute_case(case, RunContext(run_id="run-declared"))

    assert result.blocks[0].self_healed == [entry], "同一条留痕不许重复计数"
    assert result.to_dict()[SELF_HEALED_KEY][0]["detail"] == "块自己声明的"


# --------------------------------------------------------------------------- #
# SH-3：静态守卫 —— 自愈不许做进程级操作
# --------------------------------------------------------------------------- #

#: 三项禁令按运行时拼接构造（不用字面量）：既保证本文件自身不成为 grep 命中项，
#: 也避免 CPython 的常量折叠把片段折成完整字面量后留在 ``.pyc`` 里污染静态守卫。
FORBIDDEN_SNIPPETS = ("".join(("sub", "process")),
                      "".join(("p", "kill")),
                      "".join(("os", ".", "system")))


def test_no_process_level_operations_in_blocks():
    """``blocks/`` 下任何 ``.py`` 都不得出现进程级操作调用（SH-3 代码审查项）。"""
    offenders: List[str] = []
    for path in sorted(BLOCKS_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for snippet in FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append(f"{path.name}: {snippet}")
    assert offenders == [], "自愈只能做接口级释放与重试，禁止进程级操作：%s" % offenders
