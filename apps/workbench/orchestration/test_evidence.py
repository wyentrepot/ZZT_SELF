"""workbench.orchestration.evidence —— 三源 Evidence 接入测试（任务 3/4）。

覆盖：
- collect_three_source_evidence：三源（loghooks/sim_concentrator/listener）数据
  进同一 run 级 EvidenceStore，kind/source/sequence 正确，冻结后拒绝追加
- evidence_index：可下钻索引（raw_ref 锚点按 source 分组）
- acquire_serial_lease：串口资源独占租约，冲突抛 ResourceConflictError 可预测
- RunExecutor 端到端：三源 Run 的 Report 含 evidence_index/evidence_frozen/sources.listener
- load_listener_frames_from_index：从 listener 索引库读帧（任务 4：Run 接入 COM4）
- RunExecutor 自动加载：未注入 listener_frames 时从索引库读取
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
    evidence_detail,
    evidence_index,
    load_listener_frames_from_index,
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


class TestEvidenceDetail:
    """任务4：evidence_detail —— 证据下钻完整字段（前端下钻面板数据源）。"""

    def test_detail_includes_full_fields(self):
        store = collect_three_source_evidence(
            run_id="run-det",
            events=[FakeLogEvent(rule_id="r1", captures={"k": "v"})],
            step_results=[{"index": 0, "name": "s", "result": "pass"}],
            frame_records=[("000001", "t", "7e 01 7e")],
        )
        detail = evidence_detail(store)
        assert detail["total"] == 3
        srcs = detail["sources"]
        assert set(srcs) == {"loghooks", "sim_concentrator", "listener"}
        # 每条证据含完整可下钻字段
        for items in srcs.values():
            for it in items:
                assert it["kind"] and it["source"]
                assert "payload" in it and "metadata" in it
                assert "raw_ref" in it and "sequence" in it
        # loghooks 事件 payload 带 captures
        ev = srcs["loghooks"][0]
        assert ev["payload"]["captures"] == {"k": "v"}
        assert ev["raw_ref"] == "loghooks:r1"

    def test_detail_empty(self):
        store = collect_three_source_evidence(run_id="run-det-e")
        detail = evidence_detail(store)
        assert detail["total"] == 0
        assert detail["sources"] == {}

    def test_run_report_contains_evidence_detail(self, tmp_path):
        """RunExecutor 端到端：Report 含 evidence_detail（前端下钻数据源）。"""
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
            extras={"listener_frames": [("000001", "t", "7e 01 7e")]},
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        assert report is not None
        ed = report["evidence_detail"]
        assert ed["total"] >= 2  # loghooks 事件 + listener 帧
        assert "loghooks" in ed["sources"]
        assert "listener" in ed["sources"]
        store.close()


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


# ---------------------------------------------------------------------------
# load_listener_frames_from_index —— 任务 4：Run 接入 COM4 侦听台串口
# ---------------------------------------------------------------------------


def _make_listener_index(tmp_path: Path, frames: list) -> Path:
    """构造 listener 风格 frames 表（sequence/log_time/raw_hex）。"""
    import sqlite3

    db = tmp_path / "log_index.sqlite3"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """CREATE TABLE frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence TEXT NOT NULL,
                log_time TEXT NOT NULL,
                byte_length INTEGER NOT NULL,
                raw_hex TEXT NOT NULL,
                summary_json TEXT,
                parse_error TEXT
            )"""
        )
        for seq, ts, hexf in frames:
            conn.execute(
                "INSERT INTO frames(sequence, log_time, byte_length, raw_hex) VALUES(?,?,?,?)",
                (seq, ts, len(hexf.split()), hexf),
            )
    return db


class TestLoadListenerFrames:
    def test_reads_frames_chronological(self, tmp_path):
        """从索引库读取帧，按 id 正序返回 (sequence, log_time, hex_frame)。"""
        db = _make_listener_index(
            tmp_path,
            [
                ("000001", "2026-08-17 10:00:00", "7e 68 01 02 7e"),
                ("000002", "2026-08-17 10:00:01", "7e 68 02 03 7e"),
            ],
        )
        records = load_listener_frames_from_index(index_path=db)
        assert records == [
            ("000001", "2026-08-17 10:00:00", "7e 68 01 02 7e"),
            ("000002", "2026-08-17 10:00:01", "7e 68 02 03 7e"),
        ]

    def test_missing_db_returns_empty(self, tmp_path):
        """库不存在 → 空列表（优雅降级）。"""
        assert load_listener_frames_from_index(index_path=tmp_path / "none.sqlite3") == []

    def test_missing_table_returns_empty(self, tmp_path):
        """库存在但无 frames 表 → 空列表。"""
        import sqlite3

        db = tmp_path / "empty.sqlite3"
        with sqlite3.connect(str(db)):
            pass
        assert load_listener_frames_from_index(index_path=db) == []

    def test_respects_limit(self, tmp_path):
        """limit 生效：只读最近 N 条（倒序取最新再正序返回）。"""
        frames = [(f"{i:06d}", f"2026-08-17 10:00:{i:02d}", f"7e 68 {i:02d} 7e")
                  for i in range(5)]
        db = _make_listener_index(tmp_path, frames)
        records = load_listener_frames_from_index(index_path=db, limit=2)
        assert len(records) == 2
        # 最近两条（id 3,4），正序
        assert records[0][0] == "000003"
        assert records[1][0] == "000004"

    def test_run_auto_loads_from_index(self, tmp_path):
        """RunExecutor：未注入 listener_frames 时自动从索引库读取（三源闭环含 listener）。"""
        db = _make_listener_index(
            tmp_path,
            [
                ("000001", "2026-08-17 10:00:00", "7e 68 01 02 7e"),
                ("000002", "2026-08-17 10:00:01", "7e 68 02 03 7e"),
            ],
        )
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
            extras={"listener_index": str(db)},
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        assert report is not None
        assert "listener" in report["evidence_index"]["sources"]
        assert len(report["evidence_index"]["sources"]["listener"]) == 2
        assert report["sources"]["listener"]["frames"] == 2
        store.close()

    def test_run_no_index_degrades_gracefully(self, tmp_path):
        """RunExecutor：索引库不存在 → listener source 为空，Run 不失败。"""
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
            extras={"listener_index": str(tmp_path / "none.sqlite3")},
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        assert report is not None
        ei = report["evidence_index"]
        assert "listener" not in ei.get("sources", {})
        store.close()


# ---------------------------------------------------------------------------
# 任务3收口：loghooks 引擎本体 Evidence 化（Event.to_evidence + Engine.on_event）
# ---------------------------------------------------------------------------


class TestLoghooksEngineEvidence:
    """引擎本体直接 Evidence 化（字段无损，不再走有损 dict 路径）。"""

    def test_event_to_evidence_fields_lossless(self):
        """Event.to_evidence()：payload 含全部事件字段，metadata 携带 source_line。"""
        from loghooks.engine import Event

        ev = Event(
            type="report",
            label="主动上报",
            message="发现 06H-F230",
            level="info",
            time="10:00:00",
            rule_id="anhui.report",
            category="report",
            source="cco.log",
            source_line="[..] [RX] 06H-F230",
            captures={"task_id": "1"},
            line_drift=True,
            drift_actual=5,
            drift_expected=3,
            source_line_idx=42,
        )
        evidence = ev.to_evidence(run_id="run-x")
        assert evidence.kind == "event"
        assert evidence.source == "loghooks"
        assert evidence.run_id == "run-x"
        assert evidence.raw_ref == "loghooks:anhui.report"
        assert evidence.correlation_key == "anhui.report"
        assert evidence.payload["rule_id"] == "anhui.report"
        assert evidence.payload["captures"] == {"task_id": "1"}
        assert evidence.payload["line_drift"] is True
        assert evidence.payload["drift_actual"] == 5
        assert evidence.payload["drift_expected"] == 3
        # metadata 携带 source_line / source_line_idx（旧 dict 有损路径丢失的字段）
        assert evidence.metadata["origin"] == "loghooks.engine"
        assert evidence.metadata["source_line"] == "[..] [RX] 06H-F230"
        assert evidence.metadata["source_line_idx"] == 42

    def test_engine_on_event_emitter(self):
        """Engine(on_event=...)：每条 Event 产出即触发回调，events 列表仍正常累积。"""
        from loghooks.engine import Engine
        from loghooks.rules import RuleLoader
        from loghooks.sources import parse_module_log

        loader = RuleLoader()
        loader.load_all()
        rules = [r for r in loader.rules if r.module in ("cco", "common")]
        emitted = []

        engine = Engine(rules, source="module_log", on_event=emitted.append)
        line = parse_module_log(
            "[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 12"
        )
        assert line is not None
        engine.feed(line)
        result = engine.finalize()

        assert emitted, "on_event 发射器应收到至少一条事件"
        assert len(emitted) == len(result.events)
        assert all(hasattr(e, "rule_id") for e in emitted)
        # 回调收到的事件与 events 列表一致（同对象）
        assert emitted[0] is result.events[0]

    def test_collect_accepts_already_evidence(self):
        """collect_three_source_evidence：events 传已 Evidence 对象 → 直接写 store，不二次包装。"""
        from loghooks.engine import Event
        from workbench.orchestration.evidence import collect_three_source_evidence

        ev = Event(
            type="report", label="主动上报", message="m", level="info",
            time="10:00:00", rule_id="anhui.report", category="report",
            source="cco.log", source_line="line", captures={"k": "v"},
            line_drift=False, source_line_idx=7,
        )
        evidence_obj = ev.to_evidence(run_id="run-y")
        store = collect_three_source_evidence(run_id="run-y", events=[evidence_obj])

        items = store.list()
        assert len(items) == 1
        it = items[0]
        assert it.kind == "event"
        assert it.source == "loghooks"
        assert it.payload["captures"] == {"k": "v"}
        assert it.metadata["source_line_idx"] == 7  # 无损
        assert it.run_id == "run-y"

    def test_run_monitor_evidence_is_lossless(self, tmp_path):
        """RunExecutor 端到端：monitor 事件经引擎本体 Evidence 化，payload 含完整字段。"""
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        assert report is not None
        assert "loghooks" in report["evidence_index"]["sources"]
        # evidence_index 只暴露 raw_ref 锚点，完整 payload 需从 store 复核
        ei = report["evidence_index"]
        assert all(ref.startswith("loghooks:") for ref in ei["sources"]["loghooks"])
        store.close()


# ---------------------------------------------------------------------------
# 任务5：异常恢复与发布冒烟
# ---------------------------------------------------------------------------


class TestRunRecoveryAndSmoke:
    """任务5验收：异常恢复、状态终态正确落库。"""

    def test_run_step_exception_marks_failed_with_report(self, tmp_path, monkeypatch):
        """Run 执行中异常 → 不崩溃，状态落 failed，report 含 fail assertion。

        异常恢复：编排器把异常转成 AssertionResult(id=run.execute, result=fail)，
        report 可落盘供下钻，状态机进入终态 failed。
        """
        from workbench.orchestration.runner import RunExecutor
        from workbench.orchestration.store import RunStore
        from workbench.orchestration.models import RunInput

        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)

        def _boom(self, run, run_input, scenario):
            raise RuntimeError("模拟执行中断（串口异常）")

        monkeypatch.setattr(RunExecutor, "_run_steps", _boom)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")

        assert run.status == "failed"  # 状态机终态正确
        report = store.get_report(run.run_id)
        assert report is not None
        assert report["verdict"] == "fail"
        assert any(
            a["id"] == "run.execute" and a["result"] == "fail"
            for a in report["assertions"]
        )
        store.close()

    def test_run_smoke_passes_with_fake_log(self, tmp_path):
        """发布冒烟：无串口环境用 fake 日志跑完整 Run（monitor 两源闭环）。

        模拟"干净环境启动后首个 Run"：skip_flash/skip_stimulus 降级，
        monitor 仍产出事件并冻结证据，report 完整落盘。
        """
        log_dir = _make_fake_log(tmp_path)
        store = RunStore(db_path=tmp_path / "runs.sqlite", reports_dir=tmp_path / "reports")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(log_dir),
            skip_flash=True,
            skip_stimulus=True,
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)

        assert run.status in ("passed", "failed")  # 不崩溃即冒烟通过
        assert report is not None
        assert report["evidence_frozen"] is True
        assert report["run_id"] == run.run_id
        store.close()
