"""test_automation 领域模型：用例包（版本化 RunSpec）与 Run/Evidence/AssertionResult/
Artifact/Report 统一数据模型。

契约来源：docs/03-骨架设计.md §3（核心数据模型）、§4（用例与执行流程）、§9（全局约定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """带时区当前时间（ISO 8601 契约，docs/03 §9）。"""
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class RunStatus(str, Enum):
    """Run 状态机取值（docs/03 §3）。终态不可回退（docs/03 §8）。"""

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.CANCELLED,
        RunStatus.PASSED,
        RunStatus.FAILED,
        RunStatus.INCONCLUSIVE,
        RunStatus.ERROR,
    }
)

#: Evidence 种类（docs/03 §3）：frame=串口输出/帧、event=事件、interaction=交互、
#: measurement=指标/测量、log=日志（WYT-4 统一数据模型：事件、日志、指标、串口输出）。
EVIDENCE_KINDS: frozenset[str] = frozenset({"frame", "event", "interaction", "measurement", "log"})

ASSERTION_OUTCOMES: frozenset[str] = frozenset({"pass", "fail", "inconclusive"})


# ---------------------------------------------------------------------------
# 用例包（版本化 RunSpec）
# ---------------------------------------------------------------------------


class CommandSpec(BaseModel):
    """构建/工具命令：编排核心只引用适配器与用例包，不写死工具命令（WYT-4 约束）。"""

    program: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)


class SourceSpec(BaseModel):
    """源码/构建：版本化源码引用与构建命令。"""

    repo: str | None = None
    revision: str | None = None
    build_command: CommandSpec | None = None


class FirmwareSpec(BaseModel):
    """固件：路径/名称 + 期望 sha256（证据链：固件哈希贯穿 build→flash）。"""

    path: str = Field(min_length=1)
    expected_sha256: str | None = None


class DeviceSpec(BaseModel):
    """设备选择：资源类型 + 稳定标识；端口类默认独占，离线文件可 shared 只读。"""

    resource_type: str = "serial_port"
    resource_id: str = Field(min_length=1)
    shared: bool = False


class FlashSpec(BaseModel):
    """烧录：适配器名 + 固件引用 + 附加选项。"""

    adapter: str = Field(default="mock_flasher", min_length=1)
    firmware: FirmwareSpec
    options: dict[str, Any] = Field(default_factory=dict)


class BootSpec(BaseModel):
    """启动：适配器名 + 启动超时。"""

    adapter: str = Field(default="mock_device", min_length=1)
    boot_timeout_s: float = Field(default=10.0, gt=0)


class ObserveSpec(BaseModel):
    """观测：适配器名 + 采集时长/超时。"""

    adapter: str = Field(default="mock_observer", min_length=1)
    duration_s: float = Field(default=1.0, ge=0)
    timeout_s: float = Field(default=5.0, gt=0)


class AssertionSpec(BaseModel):
    """流程级断言：present/absent/contains/equals/count_gte，可限定 source 与 kind。"""

    id: str = Field(min_length=1)
    kind: Literal["present", "absent", "contains", "equals", "count_gte"]
    source: str | None = None
    kind_filter: str | None = None
    field: str | None = None
    expected: Any = None
    count: int = Field(default=1, ge=1)
    message: str | None = None


class CleanupSpec(BaseModel):
    """清理：适配器名（stop 幂等，失败写 error 但保留证据）。"""

    adapter: str = Field(default="mock_device", min_length=1)


class ReportSpec(BaseModel):
    """报告：输出格式。"""

    formats: list[str] = Field(default_factory=lambda: ["json", "jsonl"])


class CasePackage(BaseModel):
    """版本化 RunSpec：源码/构建、固件、设备选择、烧录、启动、观测、断言、清理、报告。

    加载时由 fingerprint() 计算不可变指纹（docs/03 §4）；未知字段拒绝（extra=forbid），
    保证 schema 校验可测（WYT-4 验收：有效/无效样例）。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    version: str = Field(default="1.0.0", min_length=1)
    name: str | None = None
    description: str | None = None
    timeout_s: float = Field(default=30.0, gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: SourceSpec | None = None
    firmware: FirmwareSpec | None = None
    device: DeviceSpec | None = None
    flash: FlashSpec | None = None
    boot: BootSpec | None = None
    observe: ObserveSpec | None = None
    assertions: list[AssertionSpec] = Field(default_factory=list)
    cleanup: CleanupSpec | None = None
    report: ReportSpec = Field(default_factory=ReportSpec)

    def fingerprint(self) -> str:
        """规范化 JSON 的 sha256（与键顺序无关，docs/03 §4 不可变指纹）。"""
        from test_automation.fingerprint import case_fingerprint

        return case_fingerprint(self)


# ---------------------------------------------------------------------------
# 运行时领域模型
# ---------------------------------------------------------------------------


@dataclass
class ResourceLease:
    """资源租约（docs/03 §5）：硬件端口独占、离线文件共享只读。"""

    resource_type: str
    resource_id: str
    holder: str
    shared: bool
    acquired_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "holder": self.holder,
            "shared": self.shared,
            "acquired_at": _iso(self.acquired_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceLease":
        return cls(
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            holder=data["holder"],
            shared=bool(data.get("shared", False)),
            acquired_at=_from_iso(data.get("acquired_at")),
        )


@dataclass
class Run:
    """Run（docs/03 §3）：case 的一次执行，状态机见 test_automation.state_machine。"""

    id: str
    case_id: str
    case_version: str
    case_fingerprint: str
    status: RunStatus = RunStatus.CREATED
    parameters: dict[str, Any] = field(default_factory=dict)
    resource_leases: list[ResourceLease] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "case_version": self.case_version,
            "case_fingerprint": self.case_fingerprint,
            "status": self.status.value,
            "parameters": self.parameters,
            "resource_leases": [lease.to_dict() for lease in self.resource_leases],
            "error": self.error,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        return cls(
            id=data["id"],
            case_id=data["case_id"],
            case_version=data["case_version"],
            case_fingerprint=data["case_fingerprint"],
            status=RunStatus(data["status"]),
            parameters=dict(data.get("parameters") or {}),
            resource_leases=[ResourceLease.from_dict(item) for item in data.get("resource_leases") or []],
            error=data.get("error"),
            created_at=_from_iso(data.get("created_at")),
            started_at=_from_iso(data.get("started_at")),
            finished_at=_from_iso(data.get("finished_at")),
        )


@dataclass
class Evidence:
    """Evidence（docs/03 §3）：追加写入，sequence 单调递增，冻结后拒绝追加。

    id/run_id/sequence 由 EvidenceStore.append 统一分配（stable id：``<run_id>-ev-<seq>``）。
    """

    kind: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=utcnow)
    raw_ref: str | None = None
    correlation_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    run_id: str = ""
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"未知 Evidence.kind={self.kind!r}，允许：{sorted(EVIDENCE_KINDS)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "kind": self.kind,
            "source": self.source,
            "observed_at": _iso(self.observed_at),
            "sequence": self.sequence,
            "payload": self.payload,
            "raw_ref": self.raw_ref,
            "correlation_key": self.correlation_key,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(
            kind=data["kind"],
            source=data["source"],
            payload=dict(data.get("payload") or {}),
            observed_at=_from_iso(data.get("observed_at")),
            raw_ref=data.get("raw_ref"),
            correlation_key=data.get("correlation_key"),
            metadata=dict(data.get("metadata") or {}),
            id=data.get("id", ""),
            run_id=data.get("run_id", ""),
            sequence=int(data.get("sequence", 0)),
        )


@dataclass
class AssertionResult:
    """AssertionResult（docs/03 §3）：outcome=pass|fail|inconclusive。"""

    run_id: str
    assertion_id: str
    outcome: str
    expected: Any = None
    actual: Any = None
    evidence_ids: list[str] = field(default_factory=list)
    message: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in ASSERTION_OUTCOMES:
            raise ValueError(f"未知 outcome={self.outcome!r}，允许：{sorted(ASSERTION_OUTCOMES)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "assertion_id": self.assertion_id,
            "outcome": self.outcome,
            "expected": self.expected,
            "actual": self.actual,
            "evidence_ids": list(self.evidence_ids),
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssertionResult":
        return cls(
            run_id=data["run_id"],
            assertion_id=data["assertion_id"],
            outcome=data["outcome"],
            expected=data.get("expected"),
            actual=data.get("actual"),
            evidence_ids=list(data.get("evidence_ids") or []),
            message=data.get("message", ""),
            id=data.get("id", ""),
        )


@dataclass
class StepResult:
    """单个执行阶段的结果（进入 Report.steps）。"""

    stage: str
    adapter: str
    status: str = "ok"  # ok|skipped|error|cancelled
    evidence_count: int = 0
    error: str | None = None
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "adapter": self.adapter,
            "status": self.status,
            "evidence_count": self.evidence_count,
            "error": self.error,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
        }


@dataclass
class Artifact:
    """Artifact（docs/03 §3）：附件/产物，sha256 审计。"""

    run_id: str
    type: str
    name: str
    sha256: str
    id: str = ""
    path: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "type": self.type,
            "name": self.name,
            "path": self.path,
            "sha256": self.sha256,
            "created_at": _iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            run_id=data["run_id"],
            type=data["type"],
            name=data["name"],
            sha256=data["sha256"],
            id=data.get("id", ""),
            path=data.get("path"),
            created_at=_from_iso(data.get("created_at")),
        )


@dataclass
class Report:
    """Report（docs/03 §3）：派生结果，Evidence 与 Artifact 清单是审计基础。"""

    run_id: str
    summary: dict[str, Any]
    steps: list[StepResult]
    assertions: list[AssertionResult]
    evidence_index: dict[str, Any]
    artifacts: list[Artifact]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "assertions": [item.to_dict() for item in self.assertions],
            "evidence_index": self.evidence_index,
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Report":
        return cls(
            run_id=data["run_id"],
            summary=dict(data.get("summary") or {}),
            steps=[StepResult(**{**item, "started_at": _from_iso(item.get("started_at")), "finished_at": _from_iso(item.get("finished_at"))}) for item in data.get("steps") or []],
            assertions=[AssertionResult.from_dict(item) for item in data.get("assertions") or []],
            evidence_index=dict(data.get("evidence_index") or {}),
            artifacts=[Artifact.from_dict(item) for item in data.get("artifacts") or []],
        )
