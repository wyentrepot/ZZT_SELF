"""Named Pydantic contracts for the versioned AI task facade."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccessZone(str, Enum):
    LOCAL_FULL = "local_full"
    LAN_SCOPED = "lan_scoped"


class AccessContext(_Contract):
    zone: AccessZone
    actor: str = Field(min_length=1, max_length=128)


class Capability(_Contract):
    name: str = Field(min_length=1, max_length=80)
    allowed: bool
    resources: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=240)


class ResourceAlias(_Contract):
    alias: str = Field(min_length=1, max_length=128)
    source: "SourceKind"


class SourceHealth(_Contract):
    available: bool
    reason: str | None = Field(default=None, max_length=240)


class SourceKind(str, Enum):
    MODULE_LOG = "module_log"
    LISTENER = "listener"
    SIMCON = "simcon"


class CapabilitySnapshot(_Contract):
    capability_revision: Literal["ai-v2-p1"]
    access: AccessContext
    capabilities: list[Capability]
    resource_aliases: list[ResourceAlias]
    source_health: dict[SourceKind, SourceHealth] = Field(default_factory=dict)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class CorrelationKeys(_Contract):
    context_id: str | None = None
    run_id: str | None = None
    operation_id: str | None = None


class EvidenceRef(_Contract):
    source: SourceKind
    evidence_id: str | None = None
    raw_ref: str | None = None
    index_id: str | None = None
    frame_id: int | None = Field(default=None, ge=0)
    correlation: CorrelationKeys = Field(default_factory=CorrelationKeys)


class EvidenceLevel(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class ObservationTarget(_Contract):
    session_id: str | None = Field(default=None, max_length=128)
    mapping_id: str | None = Field(default=None, max_length=128)
    index_id: str | None = Field(default=None, max_length=256)
    capture: str | None = Field(default=None, max_length=256)


class ObservationWindow(_Contract):
    type: str | None = Field(default=None, max_length=32)
    mode: str = Field(default="live", max_length=32)
    start: str | None = Field(default=None, max_length=128)
    end: str | None = Field(default=None, max_length=128)
    index_id: str | None = Field(default=None, max_length=256)
    start_seq: int | None = Field(default=None, ge=0)
    end_seq: int | None = Field(default=None, ge=0)
    start_frame_id: int | None = Field(default=None, ge=0)
    end_frame_id: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class ObservationRequest(_Contract):
    source: SourceKind
    target: ObservationTarget = Field(default_factory=ObservationTarget)
    window: ObservationWindow = Field(default_factory=ObservationWindow)
    # Match fields are source-specific and are validated again by the existing
    # AIControlService boundary.  Values remain JSON primitives/objects; this
    # avoids exposing an unconstrained Any response while retaining v1 matcher
    # compatibility inside the v2 request envelope.
    match: dict[str, object] = Field(default_factory=dict)
    completion: dict[str, object] = Field(default_factory=dict)
    context: dict[str, object] = Field(default_factory=dict)


class InvestigationRequest(_Contract):
    observations: list[ObservationRequest] = Field(min_length=1, max_length=3)
    context_id: str | None = Field(default=None, max_length=128)
    client_request_id: str | None = Field(default=None, max_length=128)
    cleanup: Literal["owned_only"] = "owned_only"


class ModuleActionRequest(_Contract):
    """The deliberately small allow-list for module write actions."""

    action: Literal["ensure", "send", "stop"]
    session_id: str | None = Field(default=None, max_length=128)
    mapping_id: str | None = Field(default=None, max_length=128)
    module: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=128)
    port: str | None = Field(default=None, max_length=128)
    serial: dict[str, object] = Field(default_factory=dict)
    text: str | None = Field(default=None, max_length=4096)
    data_hex: str | None = Field(default=None, max_length=8192)
    append_newline: bool = True
    force: bool = False
    client_request_id: str | None = Field(default=None, max_length=128)
    cleanup: Literal["owned_only"] = "owned_only"


class VerificationRunRequest(_Contract):
    task: dict[str, object] = Field(default_factory=dict)
    client_request_id: str | None = Field(default=None, max_length=128)


class FlashJobRequest(_Contract):
    session_id: str = Field(min_length=1, max_length=128)
    bin_path: str = Field(min_length=1, max_length=1024)
    slot: int = Field(default=0, ge=0, le=255)
    baud_plan: list[object] | dict[str, object] | None = None
    no_reboot_after: bool = False
    client_request_id: str | None = Field(default=None, max_length=128)


class SourceHealthView(_Contract):
    source: SourceKind
    health: SourceHealth
    observation_state: str | None = Field(default=None, max_length=32)


class EvidenceItem(_Contract):
    source: SourceKind
    evidence_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


class EvidenceView(_Contract):
    job_id: str
    level: EvidenceLevel
    summary: str = Field(default="", max_length=3072)
    items: list[EvidenceItem] = Field(default_factory=list, max_length=50)
    refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)


class JobEnvelope(_Contract):
    job_id: str = Field(min_length=1, max_length=128)
    job_state: JobState
    verdict: Verdict | None = None
    summary: str | None = Field(default=None, max_length=3072)
    source_health: dict[SourceKind, SourceHealth] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    underlying_refs: list[str] = Field(default_factory=list)
    result: dict[str, object] | None = None
