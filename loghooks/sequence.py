"""跨行状态机：把分散多行聚合为一条事件（sequence 原语）。

状态机按 bucket 键（默认取各步捕获的第一个 id 类字段，如 NID/MAC）分桶，
支持多节点并行入网/上报不串台。依序推进 step，超时触发 on_timeout。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .rules import Rule, SequenceDef, SequenceStep
from .sources import ParsedLine


@dataclass
class SequenceEvent:
    """状态机产出的事件。"""

    rule_id: str
    event_type: str
    label: str
    message: str
    level: str
    time: str
    bucket: str
    captures: Dict[str, Any]
    source_line: str


class SequenceMachine:
    """单个状态机实例（绑定一个 bucket）。"""

    def __init__(self, rule: Rule, bucket: str, start_time: str, start_ms: Optional[float] = None):
        self.rule = rule
        self.bucket = bucket
        self.start_time = start_time
        self.start_ms = start_ms  # 绝对毫秒（来自日志时间戳）
        self.current_step = 0  # 下一个待命中的 step 索引
        self.step_times: List[Optional[str]] = [None] * len(
            rule.sequence.steps if rule.sequence else []
        )
        self.captures: Dict[str, Any] = {}
        self.completed = False
        self.timed_out = False

    def advance(self, step_index: int, line: ParsedLine, step_captures: Dict[str, Any]) -> None:
        self.step_times[step_index] = line.time
        self.captures.update(step_captures)
        self.current_step = step_index + 1

    def is_expired(self, now_ms: Optional[float]) -> bool:
        """按 window_ms 判断是否超时（用日志绝对时间戳比较）。

        当时间戳无法解析（now_ms/start_ms 为 None）时，不做超时判定。
        """
        if not self.rule.sequence:
            return False
        window = self.rule.sequence.window_ms
        if window <= 0:
            return False
        if now_ms is None or self.start_ms is None:
            return False
        return now_ms > self.start_ms + window


def _parse_ts_to_ms(ts: str) -> Optional[float]:
    """把模块日志时间戳 YYYYMMDD-HH:MM:SS:mmm 解析为绝对毫秒。

    返回自某纪元起毫秒（仅用于同文件内相对比较），解析失败返回 None。
    """
    if not ts:
        return None
    m = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2}):(\d{2}):(\d{2}):(\d{3})", ts)
    if not m:
        return None
    y, mo, d, h, mi, s, ms = (int(g) for g in m.groups())
    # 用 datetime 转绝对毫秒（基准 1970）
    try:
        import datetime
        dt = datetime.datetime(y, mo, d, h, mi, s, ms * 1000)
        return dt.timestamp() * 1000
    except (ValueError, OverflowError):
        return None


class SequenceTracker:
    """跨行状态机跟踪器：管理所有进行中的状态机。"""

    def __init__(self, rule: Rule, now_ms: int = 0):
        self.rule = rule
        self.window_ms = rule.sequence.window_ms if rule.sequence else 30000
        self._machines: Dict[str, SequenceMachine] = {}
        self._events: List[SequenceEvent] = []

    def feed(self, line: ParsedLine) -> List[SequenceEvent]:
        """喂入一行，推进状态机，返回新产出的事件。"""
        seq = self.rule.sequence
        if not seq:
            return []

        # 当前行的绝对毫秒（用于超时判定）
        now_ms = _parse_ts_to_ms(line.time or "")

        # 1. 清理超时机器（可能产出 on_timeout 事件）
        self._cleanup(now_ms)

        # 2. 尝试匹配当前 step
        bucket = self._bucket_for(line, seq)
        matched = False
        for step_index, step in enumerate(seq.steps):
            if step_index < self._current_step_for(bucket):
                continue
            step_captures = self._try_match_step(step, line)
            if step_captures is not None:
                if step_index > self._current_step_for(bucket):
                    self._reset_to(bucket, step_index)
                machine = self._get_or_create(bucket, line)
                machine.advance(step_index, line, step_captures)
                matched = True
                break

        if not matched:
            # 没有匹配，但可能已有 cleanup 事件
            if self._events:
                result = list(self._events)
                self._events.clear()
                return result
            return []

        # 3. 检查是否完成
        machine = self._machines.get(bucket)
        if machine and machine.current_step >= len(seq.steps):
            self._emit_complete(machine, bucket)
            del self._machines[bucket]

        # 4. 返回所有累积事件
        if self._events:
            result = list(self._events)
            self._events.clear()
            return result
        return []

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _step_regex(self, step: SequenceStep) -> re.Pattern:
        flag_bits = 0
        for f in step.flags:
            if f.lower() == "i":
                flag_bits |= re.IGNORECASE
        return re.compile(step.pattern, flag_bits)

    def _bucket_for(self, line: ParsedLine, seq: SequenceDef) -> str:
        """计算当前行的 bucket 键。"""
        # 显式 bucket_field
        if seq.bucket_field:
            val = self._get_field(line, seq.bucket_field)
            if val is not None:
                return str(val)

        # 隐式：取首个带捕获组 step 的第一个捕获组
        for step in seq.steps:
            if step.capture_group is not None:
                m = self._step_regex(step).search(line.text or "")
                if m:
                    try:
                        return m.group(step.capture_group)
                    except (IndexError, KeyError):
                        pass
        return "_default"

    def _get_field(self, line: ParsedLine, path: str) -> Optional[Any]:
        if line.fields is None:
            return None
        cur: Any = line.fields
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    def _try_match_step(self, step: SequenceStep, line: ParsedLine) -> Optional[Dict[str, Any]]:
        """尝试匹配一个 step，成功返回捕获字段。

        捕获字段：step{i} 为各捕获组；若规则 capture 引用此 step 序号（int），
        则按 capture 字段名提取第一个捕获组。
        """
        if line.text is None:
            return None
        m = self._step_regex(step).search(line.text)
        if not m:
            return None
        captures = {}
        if m.groups():
            for i, g in enumerate(m.groups(), start=1):
                if g is not None:
                    captures[f"step{i}"] = g
        # 规则 capture: {字段名: step序号(1-based)}
        for name, spec in self.rule.capture.items():
            try:
                step_no = int(spec)
            except (ValueError, TypeError):
                continue
            if step_no - 1 == self.rule.sequence.steps.index(step):
                if m.groups() and m.group(1) is not None:
                    captures[name] = m.group(1)
        return captures

    def _current_step_for(self, bucket: str) -> int:
        machine = self._machines.get(bucket)
        return machine.current_step if machine else 0

    def _get_or_create(self, bucket: str, line: ParsedLine) -> SequenceMachine:
        machine = self._machines.get(bucket)
        if not machine:
            start_ms = _parse_ts_to_ms(line.time or "")
            machine = SequenceMachine(self.rule, bucket, line.time or "", start_ms=start_ms)
            self._machines[bucket] = machine
        return machine

    def _reset_to(self, bucket: str, step_index: int) -> None:
        # 安全删除该 bucket 的机器（若存在）
        self._machines.pop(bucket, None)

    def _cleanup(self, now_ms: Optional[float]) -> None:
        """清理超时未完成的机器，产出 on_timeout 事件。"""
        expired = [b for b, m in self._machines.items() if m.is_expired(now_ms)]
        for b in expired:
            machine = self._machines.pop(b)
            self._emit_timeout(machine, b)

    def flush(self) -> List[SequenceEvent]:
        """结束扫描时强制清理所有未完成的机器，为其产出 on_timeout 事件。

        用于日志在某一步后截断（如 STA 入网流程未走完）时，让残留状态机
        以超时事件收尾，而不是静默丢弃。
        """
        for b in list(self._machines.keys()):
            machine = self._machines.pop(b)
            if machine.current_step < len(self.rule.sequence.steps):
                self._emit_timeout(machine, b)
        # 返回累积事件并清空
        if self._events:
            result = list(self._events)
            self._events.clear()
            return result
        return []

    def _emit_complete(self, machine: SequenceMachine, bucket: str) -> None:
        seq = self.rule.sequence
        on_complete = seq.on_complete if seq else None
        if not on_complete:
            return
        event_type = on_complete.get("type", f"{self.rule.id}.complete")
        message = on_complete.get("message", "")
        # 消息插值
        try:
            message = message.format(**machine.captures)
        except KeyError:
            pass
        event = SequenceEvent(
            rule_id=self.rule.id,
            event_type=event_type,
            label=on_complete.get("label", event_type),
            message=message or event_type,
            level=on_complete.get("level", self.rule.level),
            time=next((t for t in reversed(machine.step_times) if t), ""),
            bucket=bucket,
            captures=dict(machine.captures),
            source_line="",
        )
        self._events.append(event)

    def _emit_timeout(self, machine: SequenceMachine, bucket: str) -> None:
        seq = self.rule.sequence
        on_timeout = seq.on_timeout if seq else None
        if not on_timeout:
            return
        event_type = on_timeout.get("type", f"{self.rule.id}.timeout")
        event = SequenceEvent(
            rule_id=self.rule.id,
            event_type=event_type,
            label=on_timeout.get("label", event_type),
            message=on_timeout.get("message", f"{self.rule.id} 超时"),
            level=on_timeout.get("level", "warn"),
            time=machine.start_time,
            bucket=bucket,
            captures=dict(machine.captures),
            source_line="",
        )
        self._events.append(event)

    @property
    def events(self) -> List[SequenceEvent]:
        return self._events