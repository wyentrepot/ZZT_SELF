"""Report value-object compatibility module.

Execution-domain Run/Report/StepResult/Artifact types are defined only in
libs/test_automation.models. Comparison/report summaries live in reporting.py.
"""
from .reporting import FlowCompare, SourcesSummary

__all__ = ["FlowCompare", "SourcesSummary"]
