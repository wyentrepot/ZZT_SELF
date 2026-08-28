"""workbench 编排层单测（FR-5 / FR-6 核心逻辑）。

覆盖：compare 流程比对（四类差异）、feedback 归因、scenarios 加载、
RunStore 持久化、RunExecutor 端到端（假日志 + 假任务）。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from workbench.orchestration.compare import compare_flow
from workbench.orchestration.feedback import build_feedback
from workbench.orchestration.composition import build_default_executor
from workbench.orchestration.models import FlowCompare, Run, RunInput, RunStep
from workbench.orchestration.scenarios import load_scenario, load_scenarios
from workbench.orchestration.store import RunStore
from workbench.orchestration.runner import RunExecutor, new_run_id


# ---------------------------------------------------------------------------
# compare.py —— 期望流程比对（FR-5.3）
# ---------------------------------------------------------------------------


def test_compare_all_hit():
    expected = [
        {"step": "onnet", "event_type": "network.onnet", "within_ms": 30000},
        {"step": "collect", "event_type": "collect.minute.e4", "within_ms": 60000},
    ]
    events = [
        {"type": "network.onnet", "time": "10:00:01"},
        {"type": "collect.minute.e4", "time": "10:01:00"},
    ]
    fc = compare_flow(expected, events)
    assert fc.verdict == "pass"
    assert not fc.missing and not fc.timeouts
    assert [s["status"] for s in fc.steps] == ["hit", "hit"]


def test_compare_missing_and_timeout():
    expected = [
        {"step": "onnet", "event_type": "network.onnet", "within_ms": 30000},
        {"step": "collect", "event_type": "collect.minute.e4", "within_ms": 60000},
    ]
    events = [{"type": "network.onnet", "time": "10:00:01"}]  # 缺 collect
    fc = compare_flow(expected, events)
    assert "collect" in fc.missing
    assert fc.verdict == "fail"


def test_compare_timeout():
    expected = [
        {"step": "onnet", "event_type": "network.onnet", "within_ms": 30000},
        {"step": "collect", "event_type": "collect.minute.e4", "within_ms": 60000},
    ]
    events = [
        {"type": "network.onnet", "time": "10:00:01"},
        {"type": "collect.minute.e4", "time": "10:02:00"},  # 超 60s
    ]
    fc = compare_flow(expected, events)
    assert "collect" in fc.timeouts
    assert fc.verdict == "fail"


def test_compare_negate_triggered():
    expected = [
        {"step": "onnet", "event_type": "network.onnet", "within_ms": 30000},
        {"step": "no_error", "negate": True, "event_type": "join.assoc.err"},
    ]
    events = [
        {"type": "network.onnet", "time": "10:00:01"},
        {"type": "join.assoc.err", "time": "10:00:05"},
    ]
    fc = compare_flow(expected, events)
    assert "no_error" in fc.negated
    assert fc.verdict == "fail"


def test_compare_out_of_order():
    expected = [
        {"step": "a", "event_type": "evt.a", "within_ms": 30000},
        {"step": "b", "event_type": "evt.b", "within_ms": 30000},
    ]
    events = [
        {"type": "evt.b", "time": "10:00:01"},
        {"type": "evt.a", "time": "10:00:02"},
    ]
    fc = compare_flow(expected, events)
    # 事件流按时间排序后 a 先出现（b 在 a 前时间戳但排在后面）
    # b 先出现 → 顺序错乱
    assert fc.verdict == "fail"


# ---------------------------------------------------------------------------
# feedback.py —— 归因反馈（FR-5.4）
# ---------------------------------------------------------------------------


def test_feedback_pass_no_feedback():
    fc = FlowCompare(verdict="pass")
    assert build_feedback(fc) == []


def test_feedback_missing_gives_suggestion():
    fc = FlowCompare(missing=["collect.minute.e4"], verdict="fail")
    fb = build_feedback(fc)
    assert fb, "缺失事件应触发归因反馈"
    assert any("采集任务" in o["suggestion"] for o in fb)


def test_feedback_negated():
    fc = FlowCompare(negated=["join.assoc.err"], verdict="fail")
    fb = build_feedback(fc)
    assert any("关联流程" in o["suggestion"] for o in fb)


# ---------------------------------------------------------------------------
# scenarios.py —— 场景模板
# ---------------------------------------------------------------------------


def test_scenarios_load():
    ss = load_scenarios()
    ids = {s["id"] for s in ss}
    assert {"minute_collect", "join_anhui", "open_close", "search_meter"} <= ids


def test_scenario_validate():
    s = load_scenario("minute_collect")
    from workbench.orchestration.scenarios import validate_scenario
    assert validate_scenario(s) == []


# ---------------------------------------------------------------------------
# store.py —— RunStore
# ---------------------------------------------------------------------------


def test_store_roundtrip(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    run = Run(run_id=new_run_id(), scenario_id="minute_collect")
    store.create_run(run)
    store.update_status(run.run_id, "running")
    store.add_step(run.run_id, RunStep(seq=1, kind="monitor", result="pass"))
    path = store.save_report(run.run_id, {"run_id": run.run_id, "verdict": "pass"})

    got = store.get_run(run.run_id)
    assert got["status"] == "running"
    assert len(got["steps"]) == 1
    rep = store.get_report(run.run_id)
    assert rep["verdict"] == "pass"
    assert path.exists()
    store.close()


# ---------------------------------------------------------------------------
# runner.py —— RunExecutor 端到端（假日志，无串口）
# ---------------------------------------------------------------------------


def _make_fake_log(tmp_path: Path) -> Path:
    """构造符合 module_log 标准行格式的日志（[ts] [DIR] 序号|模块|文件(行)|消息）。"""
    log_dir = tmp_path / "log"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "cco.log").write_text(
        "[20260814-10:00:01:000] [RX] 1 | CCO | aps_ioctrl_nwk.c(950) | nwk disc done\n"
        "[20260814-10:00:02:000] [RX] 2 | CCO | aps_ioctrl_nwk.c(950) | onnet cnt = 1\n",
        encoding="utf-8",
    )
    return log_dir


def test_execute_run_join_anhui(tmp_path):
    """join_anhui 场景：日志含 onnet（common.join_onnet 命中），scan/assoc 缺失。"""
    log_dir = _make_fake_log(tmp_path)
    store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
    ex = build_default_executor(store)
    ri = RunInput(
        scenario_id="join_anhui",
        firmware={"version": "v2.3.2", "commit": "9f2e1a"},
        log_dir=str(log_dir),
        skip_flash=True,
        skip_stimulus=True,  # 无串口环境跳过激励
    )
    run = ex.execute(ri, scenarios_dir=Path(__file__).parent / "scenarios")
    assert run.run_id.startswith("run-")
    report = store.get_report(run.run_id)
    assert report is not None
    assert report["run_id"] == run.run_id
    # 步骤应有 monitor/compare/feedback
    kinds = {s.kind for s in run.steps}
    assert {"monitor", "compare", "feedback"} <= kinds
    # onnet 事件命中（module_log 来源规则）
    onnet_assert = next(a for a in report["assertions"] if a["id"] == "onnet")
    assert onnet_assert["result"] == "pass"
    store.close()
