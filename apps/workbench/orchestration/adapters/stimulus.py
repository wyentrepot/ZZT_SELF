from __future__ import annotations

from pathlib import Path
from typing import Any

from test_automation.ports import PortError, StimulusPort, StimulusRequest, StimulusResult


class SimconStimulusAdapter:
    """唯一负责把模拟集中器能力接入 StimulusPort。"""

    def __init__(self, scenarios_dir: Path | None = None):
        self.scenarios_dir = scenarios_dir

    def execute(self, request: StimulusRequest) -> StimulusResult:
        try:
            from sim_concentrator.runner import execute_task, load_task

            task: dict[str, Any] | None = request.task
            if task is None and request.task_file:
                path = request.task_file
                if not path.exists() and self.scenarios_dir is not None:
                    path = self.scenarios_dir / path
                if path.exists():
                    task = load_task(str(path))
            if task is None:
                return StimulusResult(payload=None)
            return StimulusResult(payload=execute_task(task))
        except Exception as exc:
            raise PortError("stimulus_failed", "stimulus port failed", {"type": type(exc).__name__}) from exc