"""Workbench orchestration runner backed by the canonical test_automation domain models."""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from test_automation.models import (
    Artifact,
    AssertionResult,
    CasePackage,
    Report,
    Run,
    RunStatus,
    StepResult,
    utcnow,
)
from test_automation.ports import MonitorRequest, PortError, StimulusRequest

from .compare import compare_flow
from .dto import RunRequest
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
from .reporting import SourcesSummary
from .scenarios import load_scenario
from .store import RunStore


def new_run_id() -> str:
    return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def _canonical_run(request: RunRequest, scenario: dict[str, Any], run_id: str | None = None) -> Run:
    """Freeze request and effective scenario identity in the canonical Run."""
    firmware = dict(request.firmware or {})
    parameters = {
        "firmware": firmware,
        "skip_flash": request.skip_flash,
        "skip_monitor": request.skip_monitor,
        "skip_stimulus": request.skip_stimulus,
        "skip_compare": request.skip_compare,
        "skip_feedback": request.skip_feedback,
        "log_dir": request.log_dir,
        "task_file": request.task_file,
        "rules": list(request.rules or []),
        "extras": dict(request.extras),
        "request_parameters": dict(request.parameters),
        "scenario": {key: value for key, value in scenario.items() if not key.endswith("_file")},
    }
    case = CasePackage(
        case_id=str(scenario.get("id") or request.scenario_id),
        version=str(scenario.get("version") or "1.0.0"),
        parameters=parameters,
    )
    return Run(
        id=run_id or new_run_id(),
        case_id=case.case_id,
        case_version=case.version,
        case_fingerprint=case.fingerprint(),
        parameters=parameters,
    )


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_artifacts(run_id: str, files: list[str]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for raw in files:
        path = Path(raw)
        real = path.resolve()
        if str(real) in seen or not path.exists():
            continue
        seen.add(str(real))
        try:
            sha256 = _sha256_of_file(path)
            size = path.stat().st_size
        except OSError:
            sha256, size = "", 0
        artifacts.append(
            Artifact(
                id=f"{run_id}-art-{len(artifacts) + 1}",
                run_id=run_id,
                type="log",
                name=path.name,
                sha256=sha256,
                path=str(real),
                size=size,
            )
        )
    return artifacts


def _verdict_inconclusive_reasons(
    request: RunRequest,
    scan: dict[str, Any],
    simcon: dict[str, Any] | None,
    listener_frames: list[Any],
) -> list[str]:
    reasons: list[str] = []
    if not request.skip_monitor and not scan.get("events"):
        reasons.append("monitor 事件缺失")
    if not request.skip_stimulus and not (simcon and simcon.get("summary")):
        reasons.append("stimulus 执行结果缺失")
    if request.extras.get("listener_index") and not listener_frames:
        reasons.append("listener 帧缺失")
    return reasons


class RunCancelled(Exception):
    """Run 被用户协作式取消。"""


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, PortError):
        return exc.code if exc.code.isidentifier() else "port_error"
    return "execution_error"


def _step_status(result: str) -> str:
    return {"pass": "ok", "fail": "error", "skipped": "skipped",
            "running": "ok", "pending": "ok"}.get(result, "error")


def _step_evidence_count(detail: str | None) -> int:
    import re
    match = re.search(r"events=(\d+)", detail or "")
    return int(match.group(1)) if match else 0


class RunExecutor:
    """串行执行器。所有运行态写入均经 canonical Run/Report Store 接口。"""

    def __init__(self, store: RunStore | None = None, *, monitor_port=None, stimulus_port=None):
        if monitor_port is None or stimulus_port is None:
            raise ValueError("monitor_port and stimulus_port are required")
        self.store = store or RunStore()
        self.monitor_port = monitor_port
        self.stimulus_port = stimulus_port
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _record_step(
        self,
        run: Run,
        stage: str,
        status: str,
        detail: str | None = None,
        evidence_count: int = 0,
    ) -> StepResult:
        step = StepResult(
            stage=stage,
            adapter="workbench",
            status=status,
            evidence_count=evidence_count,
            error=detail if status == "error" else None,
        )
        run.steps.append(step)
        self.store.update_canonical_run(run)
        return step

    def execute(self, request: RunRequest, scenarios_dir: Path | None = None) -> Run:
        scenario = load_scenario(request.scenario_id, scenarios_dir)
        if scenario is None:
            raise ValueError(f"场景模板不存在：{request.scenario_id}")
        run = _canonical_run(request, scenario)
        run.started_at = utcnow()
        run.status = RunStatus.RUNNING
        self.store.create_run(run)
        with self._lock:
            self._active_runs[run.id] = run
        try:
            report = self._run_steps(run, request, scenario)
            run.status = {
                "pass": RunStatus.PASSED,
                "inconclusive": RunStatus.INCONCLUSIVE,
            }.get(str(report.summary.get("verdict")), RunStatus.FAILED)
        except RunCancelled:
            run.status = RunStatus.CANCELLED
            report = self._error_report(run, "run_cancelled", "用户取消")
        except PortError as exc:
            run.status = RunStatus.FAILED
            run.error = _safe_error(exc)
            report = self._error_report(run, run.error, run.error)
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = _safe_error(exc)
            report = self._error_report(run, run.error, run.error)
        finally:
            run.finished_at = utcnow()
            self.store.update_canonical_run(run)
        path = self.store.save_report(run.id, report)
        run.report_path = str(path)
        with self._lock:
            self._active_runs.pop(run.id, None)
        return run

    def submit(self, request: RunRequest, scenarios_dir: Path | None = None) -> Run:
        scenario = load_scenario(request.scenario_id, scenarios_dir)
        if scenario is None:
            raise ValueError(f"场景模板不存在：{request.scenario_id}")
        run = _canonical_run(request, scenario)
        run.started_at = utcnow()
        run.status = RunStatus.RUNNING
        self.store.create_run(run)
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[run.id] = cancel_event
            self._active_runs[run.id] = run

        def target() -> None:
            try:
                report = self._run_steps(run, request, scenario, cancel_event)
                run.status = {
                    "pass": RunStatus.PASSED,
                    "inconclusive": RunStatus.INCONCLUSIVE,
                }.get(str(report.summary.get("verdict")), RunStatus.FAILED)
                if cancel_event.is_set():
                    run.status = RunStatus.CANCELLED
            except RunCancelled:
                run.status = RunStatus.CANCELLED
                report = self._error_report(run, "run_cancelled", "用户取消")
            except PortError as exc:
                run.status = RunStatus.FAILED
                run.error = _safe_error(exc)
                report = self._error_report(run, run.error, run.error)
            except Exception as exc:
                run.status = RunStatus.ERROR
                run.error = _safe_error(exc)
                report = self._error_report(run, run.error, run.error)
            finally:
                run.finished_at = utcnow()
                self.store.update_canonical_run(run)
                self.store.save_report(run.id, report)
                with self._lock:
                    self._cancel_events.pop(run.id, None)
                    self._active_runs.pop(run.id, None)

        threading.Thread(target=target, name=f"run-{run.id}", daemon=True).start()
        return run

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(run_id)
            run = self._active_runs.get(run_id)
        if event is None or run is None:
            return False
        event.set()
        run.status = RunStatus.CANCELLING
        self.store.update_canonical_run(run)
        return True

    def is_cancelled(self, run_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(run_id)
        return event is not None and event.is_set()

    def _error_report(self, run: Run, code: str, message: str) -> Report:
        assertion_id = "run.cancelled" if code == "run_cancelled" else "run.execute"
        return Report(
            run_id=run.id,
            summary={"verdict": "fail", "error_code": code},
            steps=list(run.steps),
            assertions=[
                AssertionResult(
                    run_id=run.id,
                    assertion_id=assertion_id,
                    outcome="fail",
                    actual=message,
                    message=message,
                )
            ],
            evidence_index={},
            artifacts=[],
        )

    def _run_steps(
        self,
        run: Run,
        request: RunRequest,
        scenario: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Report:
        def check_cancel() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise RunCancelled(run.id)

        collected: dict[str, list[Any]] = {"events": [], "steps": [], "frames": []}
        listener_frames = list(request.extras.get("listener_frames") or [])
        if not listener_frames:
            listener_frames = load_listener_frames_from_index(request.extras.get("listener_index"))
        lease_manager = ResourceLeaseManager()

        check_cancel()
        self._record_step(run, "flash", "skipped" if request.skip_flash else "ok",
                          "skipped" if request.skip_flash else None)
        check_cancel()
        log_dir = Path(request.log_dir) if request.log_dir else _default_log_dir()
        rules = request.rules or scenario.get("monitor", {}).get("rules", [])
        if request.skip_monitor:
            scan = {"files": [], "events": [], "evidence": [], "summary": {},
                    "drift": False, "drift_list": []}
            self._record_step(run, "monitor", "skipped", "skipped")
        else:
            result = self.monitor_port.scan(MonitorRequest(log_dir=log_dir, rules=rules, run_id=run.id))
            scan = {
                "files": result.files, "events": result.events, "evidence": result.evidence,
                "summary": result.summary, "drift": result.drift, "drift_list": result.drift_list,
                "total_lines": result.total_lines, "unmatched": result.unmatched,
            }
            self._record_step(run, "monitor", "ok",
                              f"events={len(result.events)} files={len(result.files)}",
                              len(result.events))
        collected["events"].extend(scan.get("evidence") or scan["events"])

        check_cancel()
        simcon = None
        if request.skip_stimulus:
            self._record_step(run, "stimulus", "skipped", "skipped")
        else:
            resource_id = request.extras.get("resource_id") or "serial/COM19"
            lease = acquire_serial_lease(lease_manager, holder=run.id, resource_id=resource_id)
            run.resource_leases.append(lease)
            self.store.update_canonical_run(run)
            try:
                simcon = self.stimulus_port.execute(
                    StimulusRequest(task=None, task_file=Path(request.task_file) if request.task_file else None,
                                    resource_id=resource_id)
                ).payload
            finally:
                lease_manager.release("serial_port", resource_id, run.id)
            if simcon is None:
                self._record_step(run, "stimulus", "skipped", "skipped")
            else:
                summary = simcon.get("summary") or {}
                detail = f"total={summary.get('total', 0)} pass={summary.get('pass', 0)}"
                self._record_step(run, "stimulus",
                                  "ok" if summary.get("verdict") == "pass" else "error",
                                  detail if summary.get("verdict") != "pass" else None)
                collected["steps"].extend(simcon.get("steps") or [])
        if listener_frames:
            collected["frames"].extend(listener_frames)

        check_cancel()
        if request.skip_compare:
            compare = None
            self._record_step(run, "compare", "skipped", "skipped")
        else:
            compare = compare_flow(scenario.get("expected_flow", []), scan["events"])
            detail = f"hit={len(compare.steps)} missing={len(compare.missing)}"
            self._record_step(run, "compare", "ok" if compare.verdict == "pass" else "error",
                              detail if compare.verdict != "pass" else None)
        check_cancel()
        feedback = [] if compare is None else build_feedback(
            compare, simcon and simcon.get("summary"), bool(scan.get("drift"))
        )
        self._record_step(run, "feedback", "ok" if not feedback else "error",
                          f"issues={len(feedback)}" if feedback else None)

        compare_data = compare.model_dump() if compare is not None else {}
        verdict = "pass"
        reasons = _verdict_inconclusive_reasons(request, scan, simcon, listener_frames)
        if simcon and (simcon.get("summary") or {}).get("verdict") != "pass":
            verdict = "fail"
        elif reasons:
            verdict = "inconclusive"
        elif any(compare_data.get(key) for key in ("missing", "timeouts", "negated", "out_of_order")):
            verdict = "fail"

        assertions: list[AssertionResult] = []
        for expected in scenario.get("expected_flow", []):
            name = expected.get("step") or expected.get("event_type", "")
            hit = next((item for item in compare_data.get("steps", []) if item.get("step") == name), None)
            assertions.append(
                AssertionResult(
                    run_id=run.id,
                    assertion_id=name,
                    outcome="pass" if hit and hit.get("status") == "hit" else "fail",
                    expected=name,
                    actual=str((hit or {}).get("actual_time", "")),
                )
            )
        if simcon and (simcon.get("summary") or {}).get("verdict") == "pass":
            assertions.append(AssertionResult(run_id=run.id, assertion_id="simcon.verdict",
                                               outcome="pass", actual="pass"))
        if verdict == "inconclusive":
            assertions.extend(
                AssertionResult(run_id=run.id, assertion_id="source.missing",
                                 outcome="inconclusive", expected="必要来源证据齐全", actual=reason)
                for reason in reasons
            )

        evidence_store = collect_three_source_evidence(
            run_id=run.id, events=collected["events"], step_results=collected["steps"],
            frame_records=collected["frames"], case_id=run.case_id
        )
        evidence_store.freeze()
        summary = {
            "firmware": run.firmware,
            "scenario": run.case_id,
            "sources": SourcesSummary(
                module_log={"files": scan["files"], "events": len(scan["events"]),
                            "summary": scan["summary"]},
                listener={"frames": len(collected["frames"])} if collected["frames"] else {},
                sim_concentrator=(simcon or {}).get("summary", {}),
            ).model_dump(),
            "flow_compare": compare_data,
            "feedback": feedback,
            "verdict": verdict,
            "evidence_detail": evidence_detail(evidence_store),
            "evidence_frozen": evidence_store.frozen,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        return Report(
            run_id=run.id,
            summary=summary,
            steps=list(run.steps),
            assertions=assertions,
            evidence_index=evidence_index(evidence_store),
            artifacts=_build_artifacts(run.id, scan["files"]),
        )


def _default_log_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "LOG" / "模块" / "cco"
    return Path(__file__).resolve().parent.parent.parent.parent / "LOG" / "模块" / "cco"
