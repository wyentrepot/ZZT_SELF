"""workbench.orchestration.evidence —— 三源 Evidence 接入测试（任务 3）。

覆盖：
- collect_three_source_evidence：三源（loghooks/sim_concentrator/listener）数据
  进同一 run 级 EvidenceStore，kind/source/sequence 正确，冻结后拒绝追加
- evidence_index：可下钻索引（raw_ref 锚点按 source 分组）
- acquire_serial_lease：串口资源独占租约，冲突抛 ResourceConflictError 可预测
- RunExecutor 端到端：三源 Run 的 Report 含 evidence_index/evidence_frozen/sources.listener
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from test_automation.resource_lease import ResourceConflictError, ResourceLeaseManager

from workbench.orchestration.evidence import (
    acquire_serial_lease,
    collect_three_source_evidence,
    evidence_index,
)
from workbench.orchestration.models import RunInput
from workbench.orchestration.runner import RunExecutor
from workbench.orchestration.store import RunStore


@dataclass
class FakeLogEvent:
    """与 libs/loghooks/engine.Event 字段兼容的假事件。"""

    type: str = "report"
    label: str = "主动上报"
    message: str = "发现 06H-F230"
    level: str = "info"
    time: str = "10:00:00"
    rule_id: str = "anhui.report"
    category: str = "report"
    source: str = "cco.log"
    source_line: str = "line"
    captures: dict = field(default_factory=dict)
    line_drift: bool = False
    drift_actual: Any = None
    drift_expected: Any = None
    source_line_idx: Any = None


# ---------------------------------------------------------------------------
# collect_three_source_evidence —— 三源同时消费
# ---------------------------------------------------------------------------


class TestCollectThreeSourceEvidence:
    def test_three_sources_into_one_store(self):
        events = [FakeLogEvent(rule_id="anhui.report")]
        steps = [{"index": 0, "name": "查询路由", "result": "pass"}]
        frames = [("000001", "2026-08-17 10:00:00", "7e 68 01 02 7e")]

        store = collect_three_source_evidence(
            run_id="run-3src",
            events=events,
            step_results=steps,
            frame_records=frames,
            case_id="minute_collect",
        )

        items = store.list()
        # 三类证据进同一 store，sequence 单调递增
        assert len(items) == 3
        assert [ev.sequence for ev in items] == [1, 2, 3]
        assert all(ev.run_id == "run-3src" for ev in items)
        kinds = {(ev.kind, ev.source) for ev in items}
        assert ("event", "loghooks") in kinds
        assert ("interaction", "sim_concentrator") in kinds
        assert ("frame", "listener") in kinds

    def test_dict_events_supported(self):
        """_scan_logs 产出的 dict 事件也能被消费（无需 loghooks Event dataclass）。"""
        events = [{"type": "network.onnet", "label": "入网", "message": "onnet",
                   "time": "10:00:01", "rule_id": "common.join_onnet",
                   "category": "join", "source": "cco.log"}]
        store = collect_three_source_evidence(run_id="run-d", events=events)
        items = store.list()
        assert len(items) == 1
        assert items[0].kind == "event"
        assert items[0].source == "loghooks"
        assert items[0].payload["type"] == "network.onnet"
        assert items[0].correlation_key == "common.join_onnet"

    def test_empty_inputs_produce_empty_store(self):
        store = collect_three_source_evidence(run_id="run-empty")
        assert store.list() == []

    def test_freeze_rejects_append(self):
        store = collect_three_source_evidence(
            run_id="run-f",
            events=[FakeLogEvent()],
            step_results=[{"index": 0, "name": "s", "result": "pass"}],
        )
        store.freeze()
        assert store.frozen is True
        with pytest.raises(RuntimeError):
            store.append(kind="event", source="loghooks", payload={})


# ---------------------------------------------------------------------------
# evidence_index —— 可下钻索引
# ---------------------------------------------------------------------------


class TestEvidenceIndex:
    def test_index_groups_by_source(self):
        store = collect_three_source_evidence(
            run_id="run-idx",
            events=[FakeLogEvent(rule_id="r1")],
            step_results=[{"index": 0, "name": "s", "result": "pass"}],
            frame_records=[("000001", "t", "7e 01 7e")],
        )
        idx = evidence_index(store)
        assert idx["total"] == 3
        srcs = idx["sources"]
        assert srcs["loghooks"] == ["loghooks:r1"]
        assert srcs["sim_concentrator"] == ["simcon:step:0"]
        assert srcs["listener"] == ["listener:seq:000001"]

    def test_index_empty(self):
        store = collect_three_source_evidence(run_id="run-idx-e")
        idx = evidence_index(store)
        assert idx["total"] == 0
        assert idx["sources"] == {}


# ---------------------------------------------------------------------------
# acquire_serial_lease —— 资源冲突可预测
# ---------------------------------------------------------------------------


class TestAcquireSerialLease:
    def test_exclusive_conflict_raises(self):
        mgr = ResourceLeaseManager()
        lease1 = acquire_serial_lease(mgr, holder="run-A", resource_id="serial/COM24")
        assert lease1.holder == "run-A"
        # 同串口第二次独占 → 冲突
        with pytest.raises(ResourceConflictError):
            acquire_serial_lease(mgr, holder="run-B", resource_id="serial/COM24")

    def test_release_then_reacquire_ok(self):
        mgr = ResourceLeaseManager()
        acquire_serial_lease(mgr, holder="run-A", resource_id="serial/COM23")
        mgr.release("serial_port", "serial/COM23", "run-A")
        lease2 = acquire_serial_lease(mgr, holder="run-B", resource_id="serial/COM23")
        assert lease2.holder == "run-B"

    def test_shared_readonly_offline_file_coexist(self):
        """离线文件 shared 只读：多个持有者可共存（任务 3 出口：资源冲突可预测）。"""
        from test_automation.models import DeviceSpec

        mgr = ResourceLeaseManager()
        spec = DeviceSpec(resource_type="file", resource_id="log/cco.log", shared=True)
        mgr.acquire(spec, "run-A")
        spec2 = DeviceSpec(resource_type="file", resource_id="log/cco.log", shared=True)
        mgr.acquire(spec2, "run-B")
        assert mgr.is_held("file", "log/cco.log")


# ---------------------------------------------------------------------------
# RunExecutor 端到端 —— 三源 Run 的 Report 含 evidence 字段
# ---------------------------------------------------------------------------


def _make_fake_log(tmp_path: Path) -> Path:
    log_dir = tmp_path / "log"
    log_dir.mkdir(exist_ok=True)
    (log_dir / "cco.log").write_text(
        "[20260814-10:00:01:000] [RX] 1 | CCO | aps_ioctrl_nwk.c(950) | nwk disc done\n"
        "[20260814-10:00:02:000] [RX] 2 | CCO | aps_ioctrl_nwk.c(950) | onnet cnt = 1\n",
        encoding="utf-8",
    )
    return log_dir


class TestRunExecutorThreeSource:
    def test_report_has_evidence_fields(self, tmp_path):
        """三源 Run：monitor 事件 + listener 帧 → Report 含 evidence_index/frozen/sources.listener。

        stimulus 跳过（无串口环境），listener 帧经 RunInput.extras 注入。
        """
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
            extras={
                "listener_frames": [
                    ("000001", "2026-08-17 10:00:00", "7e 68 01 02 7e"),
                    ("000002", "2026-08-17 10:00:01", "7e 68 02 03 7e"),
                ]
            },
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        assert report is not None
        # evidence_index：loghooks 事件 + listener 帧 两源
        ei = report["evidence_index"]
        assert ei["total"] >= 3
        assert "loghooks" in ei["sources"]
        assert "listener" in ei["sources"]
        assert len(ei["sources"]["listener"]) == 2
        assert report["evidence_frozen"] is True
        # sources.listener 已填充
        assert report["sources"]["listener"]["frames"] == 2
        store.close()

    def test_stimulus_steps_become_evidence(self, tmp_path, monkeypatch):
        """stimulus 步骤结果 → sim_concentrator Evidence（monkeypatch 掉真实串口执行）。"""
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)

        def _fake_stimulus(*args, **kwargs):
            return {
                "task_id": "verify.task",
                "port": "COM24",
                "baudrate": 9600,
                "steps": [
                    {"index": 0, "name": "查询路由", "sent_hex": "68 01",
                     "matched": {"afn": 16}, "result": "pass", "reason": ""},
                ],
                "summary": {"total": 1, "pass": 1, "fail": 0, "verdict": "pass"},
            }

        monkeypatch.setattr(
            "workbench.orchestration.runner._run_stimulus", _fake_stimulus
        )
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=False,
            extras={"resource_id": "serial/COM24"},
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        assert report is not None
        assert "sim_concentrator" in report["evidence_index"]["sources"]
        assert report["evidence_index"]["sources"]["sim_concentrator"] == ["simcon:step:0"]
        assert report["sources"]["sim_concentrator"]["verdict"] == "pass"
        store.close()
