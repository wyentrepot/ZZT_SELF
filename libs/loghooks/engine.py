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

    def __init__(self, rules: List[Rule], source: str = "module_log"):
        self.rules = rules
        self.source = source
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
            self.events.append(event)

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
                    self.events.append(event)

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
                    self.events.append(event)

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