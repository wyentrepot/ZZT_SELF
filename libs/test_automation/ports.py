from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class MonitorRequest:
    log_dir: Path
    rules: list[str] = field(default_factory=list)
    run_id: str = ""


@dataclass
class MonitorResult:
    files: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Any] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    drift: bool = False
    drift_list: list[Any] = field(default_factory=list)
    total_lines: int = 0
    unmatched: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class StimulusRequest:
    task: dict[str, Any] | None = None
    task_file: Path | None = None
    resource_id: str = ""


@dataclass
class StimulusResult:
    payload: dict[str, Any] | None = None


class PortError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class MonitorPort(Protocol):
    def scan(self, request: MonitorRequest) -> MonitorResult:
        ...


class StimulusPort(Protocol):
    def execute(self, request: StimulusRequest) -> StimulusResult:
        ...