# -*- coding: utf-8 -*-
"""REQS-0037：wait_mclt_stored / read_mclt_11e3 语义步骤 单元测试。

不依赖真机/串口：FakeIO 回放帧，ListenerStore 用临时库直接落真实台架
F230 帧（test_f230_mclt_report 同源证据）。覆盖：
- 构帧字节与 task1/0/15 台架 down 证据帧逐字节一致（mclt_e3 层）；
- 正常路径：F230 已在库 → auto 冻结时刻 → 11E3 下行字节正确 + 上行
  task_id 回显校验（FakeIO 回放真实 up 帧）；
- 超时路径：库中无该 task_id 记录 → 确定性失败，报错含「等到 Ns 仍未
  出现 task_id=X 的 F230」；
- 显式 freeze_time 路径（不依赖上下文）；
- task0/15 up 失败响应（result=2）按失败判定、task_id 回显 0/15 校验；
- auto 冻结时刻但上下文缺失（未先等存数）→ 构帧失败（防发早）。
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "libs"))

from sim_concentrator.frame_codec import decode_frame  # noqa: E402
from sim_concentrator.mclt_e3 import (  # noqa: E402
    build_11e3_down,
    parse_11e3_up,
)
from sim_concentrator.runner import execute_task  # noqa: E402
from sim_concentrator.scenario_codec import (  # noqa: E402
    build_mclt_read_step,
    build_mclt_stored_step,
)
from sim_concentrator.sink import ingest_entry  # noqa: E402
from sim_concentrator.store import ListenerStore  # noqa: E402

# 真实台架帧（BENCH-v5-taskid-RECOVERED.md，只读证据，勿改）
F230_698_TASK1 = (
    "685500c300004000002006201c01000000000001030010170211263600683400c305"
    "00000000000101cda990002285020103200002010111012001020101110220000200"
    "01ffff00000100046ae8c3e84f77168616"
)
E3_UP_TASK1 = bytes.fromhex(
    "11e30000c115240000000300000000000101001017021126033600"
    "683400c30500000000000101cda990002285020103200002010111012001020101"
    "11022000020001ffff00000100046ae8c3e84f7716"
)
E3_UP_TASK0 = bytes.fromhex(
    "11e30000c115280000004300000000000100001017021126000000"
)
E3_UP_TASK15 = bytes.fromhex(
    "11e30000c11525000000430000000000010f001017021126000000"
)


class ReplayIO:
    """最小假通道：send 记录帧并回放预置响应；journal/store 缺省 None。"""

    def __init__(self, responses=(), store=None):
        self.responses = list(responses)
        self.sent = []
        self.journal = None
        self._store = store

    def send_frame(self, raw):
        self.sent.append(raw)

    def recv_frame(self, timeout=None):
        if self.responses:
            return self.responses.pop(0)
        # 无预置响应：按 timeout 等价立即超时（测试里把 expect_timeout 调小）
        time.sleep(min(timeout or 0.05, 0.05))
        return None


def _store_with_task1(td: str) -> ListenerStore:
    store = ListenerStore(Path(td) / "t.sqlite")
    parsed = decode_frame(bytes.fromhex(F230_698_TASK1))
    entry = {
        "seq": 1, "ts": "2026-11-02T18:14:07.169", "ts_epoch": 0.0,
        "dir": "rx", "kind": None, "run_id": None,
        "frame_hex": F230_698_TASK1.lower(), "afn": "06", "fn": "F230",
        "updown": "up", "parsed": parsed,
    }
    assert ingest_entry(store, entry) is not None
    return store


# ---------------------------------------------------------------------------
# mclt_e3 构帧：与证据帧逐字节一致
# ---------------------------------------------------------------------------
class TestBuild11e3Down:
    def test_task1_down_matches_evidence(self):
        raw = build_11e3_down(task_id=1, freeze_time="2026-11-02 17:10",
                              protocol=3, ssn=0x24)
        assert raw.hex() == "11e300000105240000000300000000000101001017021126"

    def test_task0_and_task15_down_match_evidence(self):
        assert build_11e3_down(task_id=0, freeze_time="2026-11-02 17:10",
                               protocol=3, ssn=0x28).hex() == \
            "11e300000105280000000300000000000100001017021126"
        assert build_11e3_down(task_id=15, freeze_time="2026-11-02 17:10",
                               protocol=3, ssn=0x25).hex() == \
            "11e30000010525000000030000000000010f001017021126"

    def test_645_task1_down_matches_evidence(self):
        raw = build_11e3_down(task_id=1, freeze_time="2026-11-02 18:05:00",
                              protocol=2, dst="000000000001", ssn=0x71)
        assert raw.hex() == "11e300000105710000000200000000000101000518021126"

    def test_freeze_time_bcd_layout(self):
        """fz_time = 秒 分 时 日 月 年（低位在前）：17:10:00 → 00 10 17 02 11 26。"""
        raw = build_11e3_down(task_id=1, freeze_time="2026-11-02 17:10:00",
                              protocol=3, ssn=0x24)
        assert raw[18:24].hex() == "001017021126"

    def test_parse_up_task1_full_chain(self):
        up = parse_11e3_up(E3_UP_TASK1)
        assert up is not None
        assert (up.task_id, up.protocol, up.result, up.di_cnt, up.bufflen) == \
            (1, 3, 0, 3, 54)
        assert up.fz_time_text == "2026-11-02 17:10:00"
        assert up.ok and len(up.payload) == 54

    def test_parse_up_failure_responses(self):
        up0 = parse_11e3_up(E3_UP_TASK0)
        up15 = parse_11e3_up(E3_UP_TASK15)
        assert (up0.task_id, up0.result, up0.di_cnt, up0.bufflen) == (0, 2, 0, 0)
        assert (up15.task_id, up15.result, up15.di_cnt, up15.bufflen) == (15, 2, 0, 0)
        assert not up0.ok and not up15.ok

    def test_parse_rejects_non_11e3(self):
        assert parse_11e3_up(b"\x68\x00") is None
        down = build_11e3_down(task_id=1, freeze_time="2026-11-02 17:10")
        assert parse_11e3_up(down) is None      # 下行 dir=0 不是上行


# ---------------------------------------------------------------------------
# 步骤构建器（scenario_codec）
# ---------------------------------------------------------------------------
class TestStepBuilders:
    def test_build_stored_step_defaults(self):
        step = build_mclt_stored_step({"task_id": 15})
        assert step["wait_mclt_stored"] == {
            "task_id": 15, "proto": None, "timeout_s": 900, "poll_s": 5}

    def test_build_read_step_auto(self):
        step = build_mclt_read_step({"task_id": 0})
        assert step["read_mclt_11e3"]["freeze_time"] == "auto"
        assert step["read_mclt_11e3"]["task_id"] == 0
        assert step["expect"] == {"afn": "11E3", "task_id": 0}
        assert step["expect_timeout"] == 60

    def test_build_read_step_explicit_time_validated(self):
        step = build_mclt_read_step({"task_id": 1, "freeze_time": "2026-11-02 18:05"})
        assert step["read_mclt_11e3"]["freeze_time"] == "2026-11-02 18:05"

    def test_build_read_step_bad_time_raises(self):
        import pytest
        from sim_concentrator.scenario_codec import ScenarioCodecError
        with pytest.raises(ScenarioCodecError):
            build_mclt_read_step({"task_id": 1, "freeze_time": "not-a-time"})

    def test_build_read_step_bad_task_id_raises(self):
        import pytest
        from sim_concentrator.scenario_codec import ScenarioParamError
        with pytest.raises(ScenarioParamError):
            build_mclt_read_step({"task_id": "XX"})


# ---------------------------------------------------------------------------
# execute_task 闭环：正常/超时/显式时刻/失败响应
# ---------------------------------------------------------------------------
def _task(steps, store=None):
    task = {
        "id": "0037-recheck", "port": "COM_TEST",
        "enable_responder": False, "steps": steps,
    }
    if store is not None:
        task["store"] = store
    return task


class TestExecuteMcltSteps:
    def test_normal_path_auto_freeze_and_task_id_echo(self, tmp_path):
        """F230 已在库 → auto 冻结时刻 17:10 → 下行字节=task1 证据帧；上行回显 1。"""
        store = _store_with_task1(str(tmp_path))
        io = ReplayIO(responses=[E3_UP_TASK1], store=store)
        out = execute_task(_task([
            build_mclt_stored_step({"task_id": 1, "timeout_s": 5, "poll_s": 1}),
            build_mclt_read_step({"task_id": 1, "expect_timeout": 5}),
        ], store=store), io=io)
        assert out["summary"]["verdict"] == "pass", out["steps"]
        s0, s1 = out["steps"]
        assert s0["result"] == "pass" and "17:10:00" in s0["reason"]
        assert s0["mclt"]["collect_time"] == "2026-11-02 17:10:00"
        # 构帧字节与 task1 down 证据帧一致（ssn 缺省 1，task_id/fz 对齐证据）
        sent = bytes.fromhex(s1["sent_hex"])
        assert sent[0:4].hex() == "11e30000"
        assert sent[17] == 1 and sent[18:24].hex() == "001017021126"
        assert sent[10] & 0x07 == 3
        assert s1["result"] == "pass" and s1["parsed"]["task_id"] == 1
        assert s1["parsed"]["bufflen"] == 54
        store.close()

    def test_timeout_path_deterministic_fail(self):
        """库中无 task_id=2 记录 → 确定性失败，报错含指定文案。"""
        with tempfile.TemporaryDirectory() as td:
            store = _store_with_task1(td)   # 只有 task 1
            io = ReplayIO(store=store)
            out = execute_task(_task([
                build_mclt_stored_step({"task_id": 2, "timeout_s": 2, "poll_s": 1}),
            ], store=store), io=io)
            assert out["summary"]["verdict"] == "fail"
            s0 = out["steps"][0]
            assert s0["result"] == "fail"
            assert "等到 2s 仍未出现 task_id=2 的 F230" in s0["reason"]
            store.close()

    def test_explicit_freeze_time_path(self, tmp_path):
        """显式 freeze_time：不依赖上下文，直接按给定时刻构帧。"""
        store = _store_with_task1(str(tmp_path))
        io = ReplayIO(responses=[E3_UP_TASK1], store=store)
        out = execute_task(_task([
            build_mclt_read_step({"task_id": 1,
                                  "freeze_time": "2026-11-02 17:10:00",
                                  "expect_timeout": 5}),
        ], store=store), io=io)
        assert out["summary"]["verdict"] == "pass", out["steps"]
        s0 = out["steps"][0]
        sent = bytes.fromhex(s0["sent_hex"])
        # 与 task1 证据帧逐字节一致（ssn 缺省 1 之外完全同证据：18..24 fz）
        assert sent[17] == 1 and sent[18:24].hex() == "001017021126"
        assert s0["result"] == "pass"
        store.close()

    def test_task0_failure_response_result2_fail_with_echo(self):
        """task0 失败响应（result=2）：task_id 回显 0 校验通过但整体判 fail。"""
        io = ReplayIO(responses=[E3_UP_TASK0])
        out = execute_task(_task([
            build_mclt_read_step({"task_id": 0,
                                  "freeze_time": "2026-11-02 17:10",
                                  "expect_timeout": 5}),
        ]), io=io)
        s0 = out["steps"][0]
        assert s0["result"] == "fail"
        assert "result=2" in s0["reason"]
        # 下行字节与 task0 证据帧一致（除 ssn）
        sent = bytes.fromhex(s0["sent_hex"])
        assert sent[17] == 0 and sent[10] & 0x07 == 3

    def test_task15_failure_response_echo_15(self):
        io = ReplayIO(responses=[E3_UP_TASK15])
        out = execute_task(_task([
            build_mclt_read_step({"task_id": 15,
                                  "freeze_time": "2026-11-02 17:10",
                                  "expect_timeout": 5}),
        ]), io=io)
        s0 = out["steps"][0]
        assert s0["result"] == "fail"
        sent = bytes.fromhex(s0["sent_hex"])
        assert sent[17] == 15
        assert s0["parsed"]["task_id"] == 15
        assert s0["parsed"]["result"] == 2

    def test_auto_without_wait_step_fails_fast(self):
        """freeze_time=auto 但上下文无冻结时刻 → 构帧失败（防 11E3 发早）。"""
        io = ReplayIO(store=None)
        out = execute_task(_task([
            build_mclt_read_step({"task_id": 1, "expect_timeout": 5}),
        ]), io=io)
        s0 = out["steps"][0]
        assert s0["result"] == "fail"
        assert "构帧失败" in s0["reason"] and "wait_mclt_stored" in s0["reason"]
        assert io.sent == []      # 未发任何帧

    def test_task_id_echo_mismatch_fails(self):
        """期望 task 0 却收到 task 1 回显 → task_id 回显校验失败。"""
        io = ReplayIO(responses=[E3_UP_TASK1])
        out = execute_task(_task([
            build_mclt_read_step({"task_id": 0,
                                  "freeze_time": "2026-11-02 17:10",
                                  "expect_timeout": 5}),
        ]), io=io)
        s0 = out["steps"][0]
        assert s0["result"] == "fail"
        assert "task_id 回显不匹配" in s0["reason"]
        assert "实际1" in s0["reason"]

    def test_composed_steps_json_shape(self, tmp_path):
        """配任务 → wait_mclt_stored → read_mclt_11e3 组合（fail_fast 中断验证）。"""
        store = _store_with_task1(str(tmp_path))
        io = ReplayIO(store=store)      # 无回放：11E3 等待超时
        out = execute_task(_task([
            {"name": "配置任务(11H-F231)",
             "send": {"afn": "11", "fn": "F231",
                      "params": {"task_no": 1, "action": "enable", "protocol": 3,
                                 "cycle_min": 5,
                                 "items": [{"meter_type": 0, "item": "20000201",
                                            "reply_len": 2}]}},
             "expect": {"afn": 17, "fn": 231}, "expect_timeout": 0.3},
            build_mclt_stored_step({"task_id": 1, "timeout_s": 5, "poll_s": 1}),
            build_mclt_read_step({"task_id": 1, "expect_timeout": 0.5}),
        ], store=store), io=io)
        # 第一步等待确认超时 fail → fail_fast 中断
        assert out["steps"][0]["result"] == "fail"
        assert len(out["steps"]) == 1
        store.close()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
