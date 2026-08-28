from __future__ import annotations

from pathlib import Path

from .adapters import LoghooksMonitorAdapter, SimconStimulusAdapter
from .runner import RunExecutor
from .scenarios import SCENARIOS_DIR
from .store import RunStore


def build_default_executor(store: RunStore | None = None) -> RunExecutor:
    """组合默认具体适配器；应用入口不直接依赖底层实现。"""
    return RunExecutor(
        store=store,
        monitor_port=LoghooksMonitorAdapter(),
        stimulus_port=SimconStimulusAdapter(SCENARIOS_DIR),
    )