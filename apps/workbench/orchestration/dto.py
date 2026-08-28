from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    scenario_id: str
    firmware: dict[str, Any] = Field(default_factory=dict)
    skip_flash: bool = False
    skip_monitor: bool = False
    skip_stimulus: bool = False
    skip_compare: bool = False
    skip_feedback: bool = False
    log_dir: str | None = None
    task_file: str | None = None
    rules: list[str] | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RunStepView(BaseModel):
    seq: int = 0
    kind: str = ""
    detail: str | None = None
    result: str = "pending"


class RunView(BaseModel):
    id: str
    run_id: str
    case_id: str
    scenario_id: str
    status: str
    case_version: str | None = None
    case_fingerprint: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    resource_leases: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    firmware: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    steps: list[RunStepView] = Field(default_factory=list)
    report_path: str | None = None


class AssertionView(BaseModel):
    id: str
    assertion_id: str | None = None
    result: str
    outcome: str
    expected: Any = None
    actual: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    message: str = ""


class ArtifactView(BaseModel):
    id: str = ""
    run_id: str
    type: str
    name: str
    sha256: str
    path: str | None = None
    size: int = 0
    created_at: datetime | str | None = None


class ReportView(BaseModel):
    run_id: str
    firmware: dict[str, Any] = Field(default_factory=dict)
    scenario: str = ""
    sources: dict[str, Any] = Field(default_factory=dict)
    flow_compare: dict[str, Any] = Field(default_factory=dict)
    feedback: list[dict[str, Any]] = Field(default_factory=list)
    ts: str | None = None
    evidence_detail: dict[str, Any] = Field(default_factory=dict)
    evidence_frozen: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    assertions: list[AssertionView] = Field(default_factory=list)
    evidence_index: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactView] = Field(default_factory=list)
    verdict: str | None = None