"""Workbench report value objects.

Execution-domain Run/Report/StepResult/Artifact models live only in
test_automation.models. These values describe report projections and comparison
results, so they do not duplicate execution state.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FlowCompare(BaseModel):
    steps: list[dict[str, Any]] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    timeouts: list[str] = Field(default_factory=list)
    out_of_order: list[str] = Field(default_factory=list)
    negated: list[str] = Field(default_factory=list)
    verdict: str = "pass"


class SourcesSummary(BaseModel):
    module_log: dict[str, Any] = Field(default_factory=dict)
    listener: dict[str, Any] = Field(default_factory=dict)
    sim_concentrator: dict[str, Any] = Field(default_factory=dict)
