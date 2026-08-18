"""test_automation 三源 Evidence 适配（任务3：侦听台帧/模拟集中器步骤/loghooks 事件）。

将三源原始数据统一转成 Evidence（docs/03 §3 Evidence 契约）：
- 侦听台帧 (sequence, log_time, hex_frame) → kind=frame, source=listener
- 模拟集中器步骤 result dict → kind=interaction, source=sim_concentrator
- loghooks Event → kind=event, source=loghooks
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import AdapterError, AdapterHealth, SourceAdapter
from .models import Evidence


def listener_frame_evidence(
    sequence: str | int,
    log_time: str | None,
    hex_frame: str,
    run_id: str = "",
    correlation_key: str | None = None,
) -> Evidence:
    """侦听台一帧原始数据 → Evidence（kind=frame, source=listener）。

    payload 携带 sequence/log_time/hex_frame；hex_frame 规范化（去空格、大写）
    存入 payload 并作为 raw_ref 摘要锚点。
    """
    normalized = " ".join(hex_frame.split()).upper()
    return Evidence(
        kind="frame",
        source="listener",
        payload={
            "sequence": str(sequence),
            "log_time": log_time or "",
            "hex_frame": normalized,
            "hex_length": len(normalized.replace(" ", "")) // 2,
        },
        raw_ref=f"listener:seq:{sequence}",
        correlation_key=correlation_key,
        metadata={"origin": "listener.serial_capture"},
        run_id=run_id,
    )


def simcon_step_evidence(
    step: dict[str, Any],
    run_id: str = "",
    case_id: str = "",
) -> Evidence:
    """模拟集中器一个步骤结果 → Evidence（kind=interaction, source=sim_concentrator）。

    step 为 execute_task 的 step_results 元素（index/name/sent_hex/matched/parsed/result/reason）。
    """
    return Evidence(
        kind="interaction",
        source="sim_concentrator",
        payload={
            "index": step.get("index"),
            "name": step.get("name", ""),
            "sent_hex": step.get("sent_hex", ""),
            "result": step.get("result", ""),
            "reason": step.get("reason", ""),
            "matched": step.get("matched"),
            "case_id": case_id,
        },
        raw_ref=f"simcon:step:{step.get('index')}",
        correlation_key=str(step.get("index")),
        metadata={"origin": "sim_concentrator.runner"},
        run_id=run_id,
    )


def loghooks_event_evidence(
    event: Any,
    run_id: str = "",
) -> Evidence:
    """loghooks 一条 Event → Evidence（kind=event, source=loghooks）。

    event 为 libs/loghooks Event dataclass（type/label/message/level/time/rule_id/...）。
    """
    payload = {
        "type": getattr(event, "type", ""),
        "label": getattr(event, "label", ""),
        "message": getattr(event, "message", ""),
        "level": getattr(event, "level", ""),
        "time": getattr(event, "time", ""),
        "rule_id": getattr(event, "rule_id", ""),
        "category": getattr(event, "category", ""),
        "captures": getattr(event, "captures", {}),
        "line_drift": getattr(event, "line_drift", False),
    }
    return Evidence(
        kind="event",
        source="loghooks",
        payload=payload,
        raw_ref=f"loghooks:{getattr(event, 'rule_id', '')}",
        correlation_key=getattr(event, "rule_id", None),
        metadata={"origin": "loghooks.engine", "source_line": getattr(event, "source_line", "")},
        run_id=run_id,
    )


class SourceAdapterBase(SourceAdapter):
    """SourceAdapter 便捷基类：可注入可调用数据源，便于测试。"""

    def __init__(self, name: str):
        self.name = name
        self._started = False

    def start(self, run_context: dict[str, Any] | None = None) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def health(self) -> AdapterHealth:
        return AdapterHealth(ok=self._started, message=f"{self.name} adapter")


class ListenerFrameAdapter(SourceAdapterBase):
    """侦听台帧适配器：迭代帧记录 → Evidence。"""

    def __init__(self, frame_records: list[tuple[str | int, str | None, str]]):
        super().__init__("listener_frame")
        self.frame_records = frame_records
        self.run_id = ""

    def start(self, run_context: dict[str, Any] | None = None) -> None:
        super().start(run_context)
        self.run_id = (run_context or {}).get("run_id", "")

    def collect(self, evidence_sink: Any) -> list[Any]:
        produced: list[Evidence] = []
        for seq, ts, hex_frame in self.frame_records:
            ev = listener_frame_evidence(seq, ts, hex_frame, run_id=self.run_id)
            produced.append(ev)
            if evidence_sink is not None:
                evidence_sink(ev)
        return produced


class SimConcentratorAdapter(SourceAdapterBase):
    """模拟集中器步骤适配器：步骤结果列表 → Evidence。"""

    def __init__(self, step_results: list[dict[str, Any]], case_id: str = ""):
        super().__init__("sim_concentrator")
        self.step_results = step_results
        self.case_id = case_id
        self.run_id = ""

    def start(self, run_context: dict[str, Any] | None = None) -> None:
        super().start(run_context)
        self.run_id = (run_context or {}).get("run_id", "")
        self.case_id = (run_context or {}).get("case_id", self.case_id)

    def collect(self, evidence_sink: Any) -> list[Any]:
        produced: list[Evidence] = []
        for step in self.step_results:
            ev = simcon_step_evidence(step, run_id=self.run_id, case_id=self.case_id)
            produced.append(ev)
            if evidence_sink is not None:
                evidence_sink(ev)
        return produced


class LoghooksEventAdapter(SourceAdapterBase):
    """loghooks 事件适配器：Event 列表 → Evidence。"""

    def __init__(self, events: list[Any]):
        super().__init__("loghooks")
        self.events = events
        self.run_id = ""

    def start(self, run_context: dict[str, Any] | None = None) -> None:
        super().start(run_context)
        self.run_id = (run_context or {}).get("run_id", "")

    def collect(self, evidence_sink: Any) -> list[Any]:
        produced: list[Evidence] = []
        for event in self.events:
            ev = loghooks_event_evidence(event, run_id=self.run_id)
            produced.append(ev)
            if evidence_sink is not None:
                evidence_sink(ev)
        return produced
