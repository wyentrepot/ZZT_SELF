from __future__ import annotations

from typing import Any

from test_automation.models import Artifact, AssertionResult, Report, Run

from .dto import AssertionView, ArtifactView, ReportView, RunRequest, RunStepView, RunView


def request_to_execution_context(request: RunRequest) -> dict[str, Any]:
    """Map API input to execution context without manufacturing a domain Run."""
    return {
        "case_id": request.scenario_id,
        "parameters": dict(request.parameters),
        "firmware": dict(request.firmware),
        "skip_flash": request.skip_flash,
        "skip_monitor": request.skip_monitor,
        "skip_stimulus": request.skip_stimulus,
        "skip_compare": request.skip_compare,
        "skip_feedback": request.skip_feedback,
        "log_dir": request.log_dir,
        "task_file": request.task_file,
        "rules": list(request.rules or []),
        "extras": dict(request.extras),
    }


def canonical_run_to_view(run: Run) -> RunView:
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    return RunView(
        id=run.id, run_id=run.id, case_id=run.case_id, scenario_id=run.case_id,
        status=status, case_version=run.case_version, case_fingerprint=run.case_fingerprint,
        parameters=dict(run.parameters), resource_leases=[lease.to_dict() for lease in run.resource_leases],
        error=run.error, created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
    )


def assertion_result_to_view(result: AssertionResult) -> AssertionView:
    return AssertionView(id=result.assertion_id or result.id, assertion_id=result.assertion_id,
                         result=result.outcome, outcome=result.outcome, expected=result.expected,
                         actual=result.actual, evidence_ids=list(result.evidence_ids), message=result.message)


def artifact_to_view(artifact: Artifact) -> ArtifactView:
    return ArtifactView(id=artifact.id, run_id=artifact.run_id, type=artifact.type,
                        name=artifact.name, sha256=artifact.sha256, path=artifact.path, size=artifact.size,
                        created_at=artifact.created_at)


def canonical_report_to_view(report: Report) -> ReportView:
    summary = dict(report.summary)
    verdict = summary.get("verdict")
    return ReportView(
        run_id=report.run_id,
        firmware=dict(summary.get("firmware") or {}), scenario=str(summary.get("scenario") or ""),
        sources=dict(summary.get("sources") or {}), flow_compare=dict(summary.get("flow_compare") or {}),
        feedback=list(summary.get("feedback") or []), ts=summary.get("ts"),
        evidence_detail=dict(summary.get("evidence_detail") or {}), evidence_frozen=bool(summary.get("evidence_frozen", False)),
        summary=summary,
        steps=[step.to_dict() for step in report.steps],
        assertions=[assertion_result_to_view(item) for item in report.assertions],
        evidence_index=dict(report.evidence_index),
        artifacts=[artifact_to_view(item) for item in report.artifacts],
        verdict=verdict,
    )