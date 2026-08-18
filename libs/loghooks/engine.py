"""引擎：整合匹配器、状态机、来源，产出事件流 + 漂移检测。

引擎是 loghooks 的核心：接收 ParsedLine + 规则列表，
逐行匹配，产出 Event 列表和漂移/命中统计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .matchers import MatchResult, apply_capture, format_message, match_line
from .rules import Rule, RuleLoader
from .sequence import SequenceEvent, SequenceTracker
from .sources import ParsedLine


@dataclass
class Event:
    """引擎产出的一条事件。"""

    type: str
    label: str
    message: str
    level: str
    time: str
    rule_id: str
    category: str
    source: str
    source_line: str
    captures: Dict[str, Any] = field(default_factory=dict)
    line_drift: bool = False  # 行号漂移
    drift_actual: Optional[int] = None
    drift_expected: Optional[int] = None
    source_line_idx: Optional[int] = None  # 原始日志行序号（0-based，供对照绑定）

    def to_evidence(self, run_id: str = "") -> Any:
        """Event → test_automation.Evidence（kind=event, source=loghooks，任务3收口）。

        引擎本体不硬依赖 test_automation（ADR-1/10/13 解耦），此处延迟 import：
        无 test_automation 环境（独立跑 loghooks）时本方法不可用，其余引擎能力不受影响。

        证据字段：
        - payload：事件全字段（含 captures/source_line/漂移信息，比 runner 旧 dict 有损路径完整）
        - raw_ref：``loghooks:<rule_id>``（可追溯锚点）
        - correlation_key：rule_id（关联键）
        - metadata.origin：loghooks.engine（与 sources.loghooks_event_evidence 一致）
        """
        from test_automation.models import Evidence

        return Evidence(
            kind="event",
            source="loghooks",
            payload={
                "type": self.type,
                "label": self.label,
                "message": self.message,
                "level": self.level,
                "time": self.time,
                "rule_id": self.rule_id,
                "category": self.category,
                "source": self.source,
                "captures": dict(self.captures or {}),
                "line_drift": self.line_drift,
                "drift_actual": self.drift_actual,
                "drift_expected": self.drift_expected,
            },
            raw_ref=f"loghooks:{self.rule_id}",
            correlation_key=self.rule_id,
            metadata={
                "origin": "loghooks.engine",
                "source_line": self.source_line,
                "source_line_idx": self.source_line_idx,
            },
            run_id=run_id,
        )


@dataclass
class DriftInfo:
    """行号漂移记录。"""

    rule_id: str
    file: str
    expected_line: int
    actual_line: int
    tolerance: int


@dataclass
class ScanResult:
    """一次扫描的完整结果。"""

    source: str
    files: List[str]
    events: List[Event] = field(default_factory=list)
    hit_rule_ids: List[str] = field(default_factory=list)
    drifts: List[DriftInfo] = field(default_factory=list)
    unmatched: int = 0
    total_lines: int = 0


class Engine:
    """日志钩子引擎。

    用法：
        engine = Engine(rules=[...])
        for line in parsed_lines:
            engine.feed(line)
        result = engine.finalize()
    """

    def __init__(
        self,
        rules: List[Rule],
        source: str = "module_log",
        on_event: Optional[callable] = None,
    ):
        """构造引擎。

        on_event：可选可插拔发射器——每产出一条 Event 即调用 ``on_event(event)``
        （任务3收口：调用方可直接在此回调里把 Event 转 Evidence 写 EvidenceStore，
        引擎本体不依赖 test_automation，保持解耦）。为 None 时行为与旧版完全一致。
        """
        self.rules = rules
        self.source = source
        self.on_event = on_event
        self.events: List[Event] = []
        self.hit_rule_ids: Set[str] = set()
        self.drifts: List[DriftInfo] = []
        self._drift_keys: Set[tuple] = set()
        self.unmatched_lines = 0
        self.total_lines = 0
        # 按规则分组的 sequence tracker
        self._seq_trackers: Dict[str, SequenceTracker] = {}
        # 按规则的 match 规则分组
        self._match_rules: List[Rule] = []
        self._seq_rules: List[Rule] = []
        for rule in rules:
            if rule.sequence is not None:
                self._seq_rules.append(rule)
                self._seq_trackers[rule.id] = SequenceTracker(rule)
            else:
                self._match_rules.append(rule)

    def _emit(self, event: Event) -> None:
        """登记事件并触发可插拔发射器（on_event）。"""
        self.events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def feed(self, line: ParsedLine) -> None:
        """喂入一行解析后的日志行，产出事件。"""
        self.total_lines += 1
        if line.source != self.source:
            return

        any_match = False

        # 1. 匹配规则（单行/单帧）
        for rule in self._match_rules:
            if not rule.match or rule.source[0] != self.source:
                continue
            result = match_line(line, rule.match)
            if not result.matched:
                continue

            any_match = True
            self.hit_rule_ids.add(rule.id)

            # 捕获
            captures = apply_capture(line, rule.match, result, rule.capture)
            msg = format_message(rule.event.get("message", ""), captures)

            event = Event(
                type=rule.event.get("type", rule.id),
                label=rule.event.get("label", rule.id),
                message=msg,
                level=rule.level,
                time=line.time or "",
                rule_id=rule.id,
                category=rule.category,
                source=line.source,
                source_line=line.raw,
                captures=captures,
                source_line_idx=line.metadata.get("_idx"),
                line_drift=result.line_drift,
                drift_actual=result.line_actual,
                drift_expected=result.line_expected,
            )
            self._emit(event)

            # 漂移记录（按 rule_id+行号去重，避免每条事件重复记录）
            if result.line_drift:
                key = (rule.id, result.line_expected, result.line_actual)
                if key not in self._drift_keys:
                    self._drift_keys.add(key)
                    self.drifts.append(DriftInfo(
                        rule_id=rule.id,
                        file=rule.match.file or "",
                        expected_line=result.line_expected or 0,
                        actual_line=result.line_actual or 0,
                        tolerance=rule.match.line_tolerance,
                    ))

        # 2. 序列规则（跨行状态机）
        for rule in self._seq_rules:
            tracker = self._seq_trackers[rule.id]
            seq_events = tracker.feed(line)
            if seq_events:
                any_match = True
                self.hit_rule_ids.add(rule.id)
                for se in seq_events:
                    event = Event(
                        type=se.event_type,
                        label=se.label,
                        message=se.message,
                        level=se.level,
                        time=se.time,
                        rule_id=rule.id,
                        category=rule.category,
                        source=line.source,
                        source_line=line.raw,
                        captures=se.captures,
                        source_line_idx=line.metadata.get("_idx"),
                    )
                    self._emit(event)

        if not any_match:
            self.unmatched_lines += 1

    def finalize(self) -> ScanResult:
        """结束扫描，返回扫描结果。"""
        # 最后强制清理所有序列 tracker 中残留的未完成状态机（产出 on_timeout）
        for rule_id, tracker in self._seq_trackers.items():
            flush_events = tracker.flush()
            for se in flush_events:
                rule = next((r for r in self._seq_rules if r.id == rule_id), None)
                if rule:
                    event = Event(
                        type=se.event_type,
                        label=se.label,
                        message=se.message,
                        level=se.level,
                        time=se.time,
                        rule_id=rule.id,
                        category=rule.category,
                        source=self.source,
                        source_line="",
                        captures=se.captures,
                    )
                    self._emit(event)

        return ScanResult(
            source=self.source,
            files=[],
            events=self.events,
            hit_rule_ids=sorted(self.hit_rule_ids),
            drifts=self.drifts,
            unmatched=self.unmatched_lines,
            total_lines=self.total_lines,
        )


def run_scan(
    lines: List[ParsedLine],
    rules: List[Rule],
    source: str = "module_log",
) -> ScanResult:
    """便捷函数：一次扫描所有行。"""
    engine = Engine(rules, source=source)
    for line in lines:
        engine.feed(line)
    return engine.finalize()