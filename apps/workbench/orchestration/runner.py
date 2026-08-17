"""workbench.orchestration.runner —— RunExecutor：全链路编排（FR-5.1 落地）。

串行编排：flash → monitor → stimulus → compare → feedback → report。
每步独立可跳过（skip_*），支持"仅监控"/"仅激励"等局部闭环（AI 可按需组合）。
所有子能力调用现有模块 API，不重实现（FR-6.4 第三条）。

- flash    → module_log 烧录能力（XMODEM）或标记"已烧录"
- monitor  → loghooks 离线扫描（engine + rules），产出事件流
- stimulus → sim_concentrator runner.execute_task（场景绑定 task）
- compare  → 期望流程 vs 实际事件流
- feedback → 按归因规则生成反馈
- report   → 聚合 Report 落盘 + 更新 runs 表
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .compare import compare_flow
from .evidence import (
    ResourceConflictError,
    ResourceLeaseManager,
    acquire_serial_lease,
    collect_three_source_evidence,
    evidence_index,
    load_listener_frames_from_index,
)
from .feedback import build_feedback
from .models import (
    Assertion,
    FlowCompare,
    Report,
    Run,
    RunInput,
    RunStep,
    SourcesSummary,
)
from .scenarios import load_scenario
from .store import RunStore


def new_run_id() -> str:
    return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


# ---------------------------------------------------------------------------
# 子能力适配：复用现有模块（延迟 import，保持解耦）
# ---------------------------------------------------------------------------


def _scan_logs(log_dir: Path, rules: List[str]) -> Dict[str, Any]:
    """调用 loghooks 引擎离线扫描，返回 {files, events, summary, drift}。"""
    from loghooks.engine import run_scan
    from loghooks.output import build_drift_list, build_summary
    from loghooks.rules import RuleLoader
    from loghooks.sources import iter_lines  # 复用行解析

    loader = RuleLoader()
    try:
        loader.load_all()
    except Exception:
        pass
    rule_objs: List[Any] = loader.rules
    # 场景规则的规则集引用（如 ["common","provinces/anhui"]）→ 按 scope / province 过滤
    if rules:
        wanted_scope = {r for r in rules if "/" not in r}
        wanted_prov = {r.split("/")[-1] for r in rules if "/" in r}
        rule_objs = [
            r for r in rule_objs
            if r.scope in wanted_scope or r.province in wanted_prov
        ]

    parsed_all: List[Any] = []
    files: List[str] = []
    for f in sorted(log_dir.glob("*")):
        if f.is_file() and f.suffix.lower() in (".txt", ".log", ".jsonl", ".dat"):
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = f.read_text(encoding="gbk", errors="ignore")
            for line in text.splitlines():
                parsed_all.extend(iter_lines("module_log", [line]))
            files.append(str(f))

    result = run_scan(parsed_all, rule_objs, source="module_log")
    events = [
        {
            "type": e.type,
            "label": e.label,
            "message": e.message,
            "time": e.time,
            "rule_id": e.rule_id,
            "category": e.category,
            "source": e.source,
        }
        for e in result.events
    ]
    return {
        "files": files,
        "events": events,
        "summary": build_summary(result),
        "drift": bool(result.drifts),
        "drift_list": build_drift_list(result),
        "total_lines": result.total_lines,
        "unmatched": result.unmatched,
    }


def _run_stimulus(task_file: Optional[Path], task: Optional[dict]) -> Optional[dict]:
    """调用 sim_concentrator runner 执行验证任务（无串口时返回 None）。

    task_file 相对路径优先相对当前工作目录解析；若不存在，则相对
    scenarios 目录（SCENARIOS_DIR/tasks/）解析，支持随场景模板分发。
    """
    from sim_concentrator.runner import execute_task

    if task is not None:
        return execute_task(task)
    if task_file:
        # 1) 相对 CWD
        if Path(task_file).exists():
            from sim_concentrator.runner import load_task

            return execute_task(load_task(str(task_file)))
        # 2) 相对 scenarios 目录（SCENARIOS_DIR = <repo>/apps/workbench/scenarios）
        from .scenarios import SCENARIOS_DIR

        cand = SCENARIOS_DIR / task_file
        if cand.exists():
            from sim_concentrator.runner import load_task

            return execute_task(load_task(str(cand)))
    return None


# ---------------------------------------------------------------------------
# RunExecutor
# ---------------------------------------------------------------------------


class RunExecutor:
    """串行执行一个 Run 的全链路编排器。"""

    def __init__(self, store: Optional[RunStore] = None):
        self.store = store or RunStore()

    def execute(self, run_input: RunInput, scenarios_dir: Optional[Path] = None) -> Run:
        scenario = load_scenario(run_input.scenario_id, scenarios_dir)
        if scenario is None:
            raise ValueError(f"场景模板不存在：{run_input.scenario_id}")

        run = Run(
            run_id=new_run_id(),
            scenario_id=run_input.scenario_id,
            firmware=run_input.firmware,
        )
        self.store.create_run(run)
        self.store.update_status(run.run_id, "running")

        try:
            report = self._run_steps(run, run_input, scenario)
            run.status = "passed" if report.verdict == "pass" else "failed"
        except Exception as exc:
            run.status = "failed"
            report = Report(run_id=run.run_id, verdict="fail")
            report.assertions.append(
                Assertion(id="run.execute", actual=str(exc), result="fail")
            )
        self.store.update_status(run.run_id, run.status)
        run.report_path = str(self.store.save_report(run.run_id, report.model_dump()))
        return run

    def _run_steps(self, run: Run, run_input: RunInput, scenario: dict) -> Report:
        steps_result: Dict[str, Any] = {}
        seq = 0

        # 任务3：Run 级三源 Evidence 收集 + 串口资源租约
        lease_manager = ResourceLeaseManager()
        collected: Dict[str, List[Any]] = {
            "events": [],
            "steps": [],
            "frames": [],
        }
        # 任务4：listener 帧来源——优先显式注入（extras.listener_frames），
        # 否则从 listener 索引库（COM4 侦听台采集落库）按需读取；库不存在/无帧
        # 时优雅降级为空（listener source 为空，不阻断 Run）。
        listener_frames = run_input.extras.get("listener_frames") or []
        if not listener_frames:
            listener_frames = load_listener_frames_from_index(
                index_path=run_input.extras.get("listener_index")
            )
            if listener_frames:
                run_input.extras["listener_frames"] = listener_frames

        # 1. flash
        seq += 1
        if run_input.skip_flash:
            step = RunStep(seq=seq, kind="flash", detail="skipped", result="skipped")
        else:
            step = RunStep(seq=seq, kind="flash", detail="XMODEM 烧录（或标记已烧录）", result="pass")
        self.store.add_step(run.run_id, step)
        run.steps.append(step)

        # 2. monitor
        seq += 1
        log_dir = Path(run_input.log_dir) if run_input.log_dir else _default_log_dir()
        rules = run_input.rules or scenario.get("monitor", {}).get("rules", [])
        if run_input.skip_monitor:
            scan: Dict[str, Any] = {"files": [], "events": [], "summary": {},
                                    "drift": False, "drift_list": []}
            step = RunStep(seq=seq, kind="monitor", detail="skipped", result="skipped")
        else:
            scan = _scan_logs(log_dir, rules)
            step = RunStep(
                seq=seq,
                kind="monitor",
                detail=f"events={len(scan['events'])} files={len(scan['files'])}",
                result="pass",
            )
        self.store.add_step(run.run_id, step)
        run.steps.append(step)
        steps_result["monitor"] = scan
        collected["events"].extend(scan["events"])

        # 3. stimulus
        seq += 1
        task_file = run_input.task_file or scenario.get("stimulus", {}).get("task_file")
        if run_input.skip_stimulus:
            simcon: Optional[dict] = None
            step = RunStep(seq=seq, kind="stimulus", detail="skipped", result="skipped")
        else:
            # 任务3：串口资源独占租约（冲突可预测）
            resource_id = run_input.extras.get("resource_id") or "serial/COM24"
            lease = None
            try:
                lease = acquire_serial_lease(
                    lease_manager, holder=run.run_id, resource_id=resource_id
                )
            except ResourceConflictError as exc:
                simcon = None
                step = RunStep(
                    seq=seq, kind="stimulus",
                    detail=f"资源冲突：{exc}", result="fail",
                )
                self.store.add_step(run.run_id, step)
                run.steps.append(step)
                steps_result["stimulus"] = None
                raise
            simcon = _run_stimulus(Path(task_file) if task_file else None, None)
            if lease is not None:
                lease_manager.release("serial_port", resource_id, run.run_id)
            if simcon is None:
                step = RunStep(seq=seq, kind="stimulus", detail="无任务/无串口，跳过", result="skipped")
            else:
                step = RunStep(
                    seq=seq,
                    kind="stimulus",
                    detail=f"total={simcon['summary']['total']} pass={simcon['summary']['pass']}",
                    result="pass" if simcon["summary"]["verdict"] == "pass" else "fail",
                )
        self.store.add_step(run.run_id, step)
        run.steps.append(step)
        steps_result["stimulus"] = simcon
        if simcon and simcon.get("steps"):
            collected["steps"].extend(simcon["steps"])
        if listener_frames:
            collected["frames"].extend(listener_frames)

        # 4. compare
        seq += 1
        if run_input.skip_compare:
            compare: FlowCompare = FlowCompare()
            step = RunStep(seq=seq, kind="compare", detail="skipped", result="skipped")
        else:
            expected = scenario.get("expected_flow", [])
            compare = compare_flow(expected, scan["events"])
            step = RunStep(
                seq=seq,
                kind="compare",
                detail=f"hit={len(compare.steps)} missing={len(compare.missing)}",
                result="pass" if compare.verdict == "pass" else "fail",
            )
        self.store.add_step(run.run_id, step)
        run.steps.append(step)
        steps_result["compare"] = compare

        # 5. feedback
        seq += 1
        if run_input.skip_feedback:
            feedback: List[dict] = []
            step = RunStep(seq=seq, kind="feedback", detail="skipped", result="skipped")
        else:
            feedback = build_feedback(compare, simcon and simcon.get("summary"), scan.get("drift", False))
            step = RunStep(
                seq=seq,
                kind="feedback",
                detail=f"issues={len(feedback)}",
                result="pass" if not feedback else "fail",
            )
        self.store.add_step(run.run_id, step)
        run.steps.append(step)

        # 6. report 聚合
        verdict = "pass"
        assertions: List[Assertion] = []
        if compare.missing or compare.timeouts or compare.negated or compare.out_of_order:
            verdict = "fail"
        if simcon and simcon["summary"]["verdict"] != "pass":
            verdict = "fail"

        # 断言列表：比对差异映射为断言
        for step_cfg in scenario.get("expected_flow", []):
            name = step_cfg.get("step") or step_cfg.get("event_type", "")
            st = next((s for s in compare.steps if s["step"] == name), None)
            if st is None:
                assertions.append(Assertion(id=name, expected=name, result="fail"))
            else:
                assertions.append(
                    Assertion(
                        id=name,
                        expected=name,
                        actual=str(st.get("actual_time", "")),
                        result="pass" if st["status"] == "hit" else "fail",
                    )
                )
        if simcon and simcon["summary"]["verdict"] == "pass":
            assertions.append(Assertion(id="simcon.verdict", actual="pass", result="pass"))

        # 任务3：三源 Evidence 汇总进同一 run 级 EvidenceStore 并冻结证据窗口
        evidence_store = collect_three_source_evidence(
            run_id=run.run_id,
            events=collected["events"],
            step_results=collected["steps"],
            frame_records=collected["frames"],
            case_id=scenario.get("id", ""),
        )
        evidence_store.freeze()

        report = Report(
            run_id=run.run_id,
            firmware=run.firmware,
            scenario=run.scenario_id,
            sources=SourcesSummary(
                module_log={
                    "files": scan["files"],
                    "events": len(scan["events"]),
                    "summary": scan["summary"],
                },
                listener={
                    "frames": len(collected["frames"]),
                } if collected["frames"] else {},
                sim_concentrator=(simcon or {}).get("summary", {}),
            ),
            assertions=assertions,
            flow_compare=compare,
            feedback=feedback,
            verdict=verdict,
            artifacts=scan["files"],
            evidence_index=evidence_index(evidence_store),
            evidence_frozen=evidence_store.frozen,
        )
        return report


def _default_log_dir() -> Path:
    """默认日志目录：仓库根 LOG/模块/cco（frozen 时 exe 同目录）。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "LOG" / "模块" / "cco"
    return Path(__file__).resolve().parent.parent.parent.parent / "LOG" / "模块" / "cco"
