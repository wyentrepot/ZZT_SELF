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

import hashlib
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .compare import compare_flow
from test_automation.ports import MonitorRequest, PortError, StimulusRequest
from test_automation.models import (CasePackage, Run as CanonicalRun, Report as CanonicalReport,
                                   StepResult, AssertionResult, Artifact as CanonicalArtifact)
from .evidence import (
    ResourceConflictError,
    ResourceLeaseManager,
    acquire_serial_lease,
    collect_three_source_evidence,
    evidence_detail,
    evidence_index,
    load_listener_frames_from_index,
)
from .feedback import build_feedback
from .models import (
    ArtifactInfo,
    Assertion,
    FlowCompare,
    Report,
    Run,
    RunInput,
    RunStatus,
    RunStep,
    SourcesSummary,
)
from .scenarios import load_scenario
from .store import RunStore


def new_run_id() -> str:
    return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def _canonical_run(run: Run, run_input: RunInput, scenario: dict) -> CanonicalRun:
    """Freeze request/scenario identity for the execution/audit Store source."""
    version = str(scenario.get("version") or "1.0.0")
    firmware = run_input.firmware.model_dump() if hasattr(run_input.firmware, "model_dump") else dict(run_input.firmware or {})
    parameters = {
        "firmware": firmware,
        "skip_flash": run_input.skip_flash, "skip_monitor": run_input.skip_monitor,
        "skip_stimulus": run_input.skip_stimulus, "skip_compare": run_input.skip_compare,
        "skip_feedback": run_input.skip_feedback, "log_dir": run_input.log_dir,
        "task_file": run_input.task_file, "rules": list(run_input.rules or []),
        "extras": dict(run_input.extras),
        "scenario": {k: v for k, v in scenario.items() if not k.endswith("_file")},
    }
    case = CasePackage(case_id=str(scenario.get("id") or run_input.scenario_id), version=version,
                       parameters=parameters)
    return CanonicalRun(id=run.run_id, case_id=case.case_id, case_version=case.version,
                        case_fingerprint=case.fingerprint(), parameters=parameters)


def _sha256_of_file(path: Path) -> str:
    """计算文件 SHA-256（D-03 Artifact 审计链：内容摘要）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_artifacts(run_id: str, files: List[str], report_path: Optional[Path] = None) -> List[ArtifactInfo]:
    """为 Run 的产物生成结构化 Artifact 清单（D-03 审计链）。

    每个产物：计算 SHA-256、逻辑 Artifact ID（<run_id>-art-<N>）、类型与真实路径。
    文件缺失/不可读时仍登记（sha256 为空串），保持 manifest 完整可审计。
    """
    artifacts: List[ArtifactInfo] = []
    seen: set = set()

    def _add(path: Path, type_: str) -> None:
        real = path.resolve()
        key = str(real)
        if key in seen or not path.exists():
            return
        seen.add(key)
        try:
            sha = _sha256_of_file(path)
            size = path.stat().st_size
        except OSError:
            sha, size = "", 0
        artifacts.append(
            ArtifactInfo(
                id=f"{run_id}-art-{len(artifacts) + 1}",
                run_id=run_id,
                type=type_,
                name=path.name,
                sha256=sha,
                path=str(real),
                size=size,
            )
        )

    for f in files:
        _add(Path(f), "log")
    if report_path is not None:
        _add(Path(report_path), "report")
    return artifacts


def _verdict_inconclusive_reasons(run_input: RunInput, scan: Dict[str, Any], simcon: Optional[dict],
                                  listener_frames: List[Any]) -> List[str]:
    """D-02 必要来源缺失判定：执行后证据缺失（非用户主动跳过）→ inconclusive。

    返回缺失原因列表；空列表表示无缺失（可正常 pass/fail）。
    - monitor：未跳过但扫描无事件（核心日志证据缺失）
    - stimulus：未跳过但无执行结果（无任务/无串口导致，必要刺激证据缺失）
    - listener：显式期望 listener（传了 listener_index）但无帧（侦听台未采集到）
    用户主动 skip_* 的源不算缺失（降级运行模式，现有兼容行为保持不变）。
    """
    reasons: List[str] = []
    if not run_input.skip_monitor and not scan.get("events"):
        reasons.append("monitor 事件缺失")
    if not run_input.skip_stimulus and not (simcon and simcon.get("summary")):
        reasons.append("stimulus 执行结果缺失")
    if run_input.extras.get("listener_index") and not listener_frames:
        reasons.append("listener 帧缺失")
    return reasons


class RunCancelled(Exception):
    """Run 被用户取消（协作式取消：步骤间检查取消标志后抛出）。"""


# ---------------------------------------------------------------------------
# RunExecutor
# ---------------------------------------------------------------------------


def _to_canonical_report(report: Report, run: Run) -> CanonicalReport:
    summary = {
        "firmware": run.firmware.model_dump(), "scenario": report.scenario,
        "sources": report.sources.model_dump(), "flow_compare": report.flow_compare.model_dump(),
        "feedback": report.feedback, "verdict": report.verdict, "ts": report.ts,
        "evidence_detail": report.evidence_detail, "evidence_frozen": report.evidence_frozen,
    }
    return CanonicalReport(
        run_id=run.run_id, summary=summary,
        steps=[StepResult(stage=step.kind, adapter="workbench", status=step.result, error=step.detail) for step in run.steps],
        assertions=[AssertionResult(run_id=run.run_id, assertion_id=item.id, outcome=item.result,
                                    expected=item.expected, actual=item.actual) for item in report.assertions],
        evidence_index=dict(report.evidence_index),
        artifacts=[CanonicalArtifact(run_id=item.run_id, type=item.type, name=item.name,
                                     sha256=item.sha256, path=item.path, size=item.size, id=item.id)
                   for item in report.artifacts],
    )


class RunExecutor:
    """串行执行一个 Run 的全链路编排器。

    支持同步（execute，CLI/测试）与异步（submit + cancel，REST/UI）两种执行：
    - submit() 在后台线程执行，前端轮询状态；
    - cancel() 协作式取消：置取消标志，执行线程在步骤间检查，落 CANCELLED 终态。
    """

    def __init__(self, store: Optional[RunStore] = None, *, monitor_port=None, stimulus_port=None):
        if monitor_port is None or stimulus_port is None:
            raise ValueError("monitor_port and stimulus_port are required")
        self.store = store or RunStore()
        self.monitor_port = monitor_port
        self.stimulus_port = stimulus_port
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ---------------- 同步执行（CLI / 测试复用） ----------------

    def execute(self, run_input: RunInput, scenarios_dir: Optional[Path] = None) -> Run:
        """同步执行一个 Run（阻塞到结束）。取消标志可经 cancel() 注入。"""
        scenario = load_scenario(run_input.scenario_id, scenarios_dir)
        if scenario is None:
            raise ValueError(f"场景模板不存在：{run_input.scenario_id}")

        run = Run(
            run_id=new_run_id(),
            scenario_id=run_input.scenario_id,
            firmware=run_input.firmware,
        )
        canonical_run = _canonical_run(run, run_input, scenario)
        canonical_run.started_at = canonical_run.created_at
        canonical_run.status = RunStatus.RUNNING
        self.store.create_run(canonical_run)
        self.store.update_status(run.run_id, "running")

        try:
            report = self._run_steps(run, run_input, scenario)
            if report.verdict == "pass":
                run.status = RunStatus.PASSED
            elif report.verdict == "inconclusive":
                run.status = RunStatus.INCONCLUSIVE
            else:
                run.status = RunStatus.FAILED
        except RunCancelled:
            run.status = RunStatus.CANCELLED
            report = Report(run_id=run.run_id, verdict="fail")
            report.assertions.append(
                Assertion(id="run.cancelled", expected="", actual="用户取消", result="fail")
            )
        except PortError as exc:
            run.status = RunStatus.FAILED
            report = Report(run_id=run.run_id, verdict="fail")
            report.assertions.append(
                Assertion(id="run.execute", actual=exc.message, result="fail")
            )
        except Exception as exc:
            run.status = RunStatus.FAILED
            report = Report(run_id=run.run_id, verdict="fail")
            report.assertions.append(
                Assertion(id="run.execute", actual=str(exc), result="fail")
            )
        canonical_run.status = run.status
        canonical_run.finished_at = canonical_run.created_at
        if run.status == RunStatus.FAILED and report.assertions:
            canonical_run.error = report.assertions[-1].actual
        self.store.update_status(run.run_id, run.status)
        run.report_path = str(self.store.save_report(run.run_id, _to_canonical_report(report, run)))
        return run

    # ---------------- 异步执行 + 取消（REST / UI） ----------------

    def submit(self, run_input: RunInput, scenarios_dir: Optional[Path] = None) -> Run:
        """异步启动一个 Run：创建后立刻返回（状态 running），后台线程执行。

        调用方轮询 GET /api/run/{run_id} 获取进度；可经 cancel(run_id) 取消。
        """
        scenario = load_scenario(run_input.scenario_id, scenarios_dir)
        if scenario is None:
            raise ValueError(f"场景模板不存在：{run_input.scenario_id}")

        run = Run(
            run_id=new_run_id(),
            scenario_id=run_input.scenario_id,
            firmware=run_input.firmware,
        )
        canonical_run = _canonical_run(run, run_input, scenario)
        canonical_run.started_at = canonical_run.created_at
        canonical_run.status = RunStatus.RUNNING
        self.store.create_run(canonical_run)
        self.store.update_status(run.run_id, "running")
        run.status = RunStatus.RUNNING

        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[run.run_id] = cancel_event

        def _target() -> None:
            try:
                report = self._run_steps(run, run_input, scenario, cancel_event=cancel_event)
                if report.verdict == "pass":
                    status = "passed"
                elif report.verdict == "inconclusive":
                    status = "inconclusive"
                else:
                    status = "failed"
                if cancel_event.is_set():
                    status = "cancelled"
            except RunCancelled:
                status = "cancelled"
                report = Report(run_id=run.run_id, verdict="fail")
                report.assertions.append(
                    Assertion(id="run.cancelled", expected="", actual="用户取消", result="fail")
                )
            except PortError as exc:
                status = "failed"
                report = Report(run_id=run.run_id, verdict="fail")
                report.assertions.append(
                    Assertion(id="run.execute", actual=exc.message, result="fail")
                )
            except Exception as exc:
                status = "failed"
                report = Report(run_id=run.run_id, verdict="fail")
                report.assertions.append(
                    Assertion(id="run.execute", actual=str(exc), result="fail")
                )
            canonical_run.status = RunStatus(status)
            canonical_run.finished_at = canonical_run.created_at
            if canonical_run.status == RunStatus.FAILED and report.assertions:
                canonical_run.error = report.assertions[-1].actual
            self.store.update_status(run.run_id, status)
            run.report_path = str(self.store.save_report(run.run_id, _to_canonical_report(report, run)))
            with self._lock:
                self._cancel_events.pop(run.run_id, None)

        t = threading.Thread(target=_target, name=f"run-{run.run_id}", daemon=True)
        t.start()
        return run

    def cancel(self, run_id: str) -> bool:
        """请求取消一个正在执行的 Run：置取消标志 + 状态转 CANCELLING。

        返回 True 表示已发起取消（该 run 正在执行）；已终态/不存在返回 False。
        执行线程会在步骤间检查标志，最终落 CANCELLED 终态。
        """
        with self._lock:
            ev = self._cancel_events.get(run_id)
        if ev is None:
            return False
        ev.set()
        self.store.update_status(run_id, "cancelling")
        return True

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            ev = self._cancel_events.get(run_id)
        return ev is not None and ev.is_set()

    def _run_steps(
        self,
        run: Run,
        run_input: RunInput,
        scenario: dict,
        cancel_event: Optional[threading.Event] = None,
    ) -> Report:
        steps_result: Dict[str, Any] = {}
        seq = 0

        # 任务3：Run 级三源 Evidence 收集 + 串口资源租约
        lease_manager = ResourceLeaseManager()
        collected: Dict[str, List[Any]] = {
            "events": [],
            "steps": [],
            "frames": [],
        }

        # 任务4：协作式取消检查点——每个步骤前检查取消标志，已取消则抛
        # RunCancelled（外层捕获后落 CANCELLED 终态，跳过剩余步骤）。
        def _check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise RunCancelled(run.run_id)

        _check_cancel()
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
        _check_cancel()
        if run_input.skip_flash:
            step = RunStep(seq=seq, kind="flash", detail="skipped", result="skipped")
        else:
            step = RunStep(seq=seq, kind="flash", detail="XMODEM 烧录（或标记已烧录）", result="pass")
        self.store.add_step(run.run_id, step)
        run.steps.append(step)

        # 2. monitor
        seq += 1
        _check_cancel()
        log_dir = Path(run_input.log_dir) if run_input.log_dir else _default_log_dir()
        rules = run_input.rules or scenario.get("monitor", {}).get("rules", [])
        if run_input.skip_monitor:
            scan: Dict[str, Any] = {"files": [], "events": [], "evidence": [], "summary": {},
                                    "drift": False, "drift_list": []}
            step = RunStep(seq=seq, kind="monitor", detail="skipped", result="skipped")
        else:
            scan_result = self.monitor_port.scan(MonitorRequest(log_dir=log_dir, rules=rules, run_id=run.run_id))
            scan = {"files": scan_result.files, "events": scan_result.events,
                    "evidence": scan_result.evidence, "summary": scan_result.summary,
                    "drift": scan_result.drift, "drift_list": scan_result.drift_list,
                    "total_lines": scan_result.total_lines, "unmatched": scan_result.unmatched}
            step = RunStep(
                seq=seq,
                kind="monitor",
                detail=f"events={len(scan['events'])} files={len(scan['files'])}",
                result="pass",
            )
        self.store.add_step(run.run_id, step)
        run.steps.append(step)
        steps_result["monitor"] = scan
        # 任务3收口：monitor 事件用引擎本体直接 Evidence 化的完整对象（字段无损）
        collected["events"].extend(scan.get("evidence") or scan["events"])

        # 3. stimulus
        seq += 1
        _check_cancel()
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
            simcon_result = self.stimulus_port.execute(StimulusRequest(
                task=None, task_file=Path(task_file) if task_file else None, resource_id=resource_id
            ))
            simcon = simcon_result.payload
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
        _check_cancel()
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
        _check_cancel()
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
        # D-02 verdict 三态判定：
        #   明确失败证据（simcon 失败）→ fail（真实失败，优先于证据缺失）
        #   否则必要来源证据缺失（monitor 无事件等）→ inconclusive
        #   否则期望流程未满足（compare missing，有证据但缺期望）→ fail
        #   否则 → pass
        verdict = "pass"
        inconclusive_reasons = _verdict_inconclusive_reasons(
            run_input, scan, simcon, listener_frames
        )
        simcon_fail = bool(simcon and simcon["summary"]["verdict"] != "pass")
        if simcon_fail:
            verdict = "fail"
        elif inconclusive_reasons:
            verdict = "inconclusive"
        elif compare.missing or compare.timeouts or compare.negated or compare.out_of_order:
            verdict = "fail"

        assertions: List[Assertion] = []

        # D-02：必要来源缺失登记为 inconclusive 断言（证据链可追溯）
        if verdict == "inconclusive":
            for reason in _verdict_inconclusive_reasons(run_input, scan, simcon, listener_frames):
                assertions.append(
                    Assertion(
                        id="source.missing",
                        expected="必要来源证据齐全",
                        actual=reason,
                        result="inconclusive",
                    )
                )

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
            artifacts=_build_artifacts(run.run_id, scan["files"]),
            evidence_index=evidence_index(evidence_store),
            evidence_detail=evidence_detail(evidence_store),
            evidence_frozen=evidence_store.frozen,
        )
        return report


def _default_log_dir() -> Path:
    """默认日志目录：仓库根 LOG/模块/cco（frozen 时 exe 同目录）。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "LOG" / "模块" / "cco"
    return Path(__file__).resolve().parent.parent.parent.parent / "LOG" / "模块" / "cco"
