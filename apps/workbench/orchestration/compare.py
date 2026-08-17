"""workbench.orchestration.compare —— 期望流程比对器（FR-5.3 落地）。

输入：期望流程（场景模板 expected_flow）+ 实际事件流（loghooks scan 结果）。
输出四类差异（可机器消费 + 前端渲染）：

- ✅ hit             命中步骤（含实际时间）
- ❌ missing         缺失步骤（期望出现但未出现）
- ⚠️ timeout         超时步骤（超出 within_ms 窗）
- 🔀 out_of_order    顺序错乱（出现但次序不符）
- 🚫 negate_triggered 负向断言触发（期望不出现的事件出现）

算法：事件流按时间排序，对 expected_flow 依序匹配（optional 步可跳过）；
within_ms 判定超时；negate 步在窗口内扫到即触发；命中次序与声明不符标
out_of_order。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import FlowCompare


def _ev_time(ev: Dict[str, Any]) -> float:
    """事件时间 → 秒（浮点）。事件 time 形如 HH:MM:SS 或 YYYYMMDD-HH:MM:SS:mmm。"""
    t = str(ev.get("time", "")).strip()
    if not t:
        return 0.0
    try:
        if "T" in t:
            return _parse_iso(t)
        # HH:MM:SS[:mmm]
        parts = t.split(":")
        sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2].split(".")[0])
        if len(parts) > 3:
            sec += int(parts[3]) / 1000.0
        return sec
    except (ValueError, IndexError):
        return 0.0


def _parse_iso(t: str) -> float:
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.fromisoformat(t[:19])
    return dt.timestamp()


def _matches(ev: Dict[str, Any], event_type: str) -> bool:
    return ev.get("type") == event_type or ev.get("label") == event_type


def compare_flow(expected_flow: List[dict], actual_events: List[dict]) -> FlowCompare:
    """期望流程 vs 实际事件流，返回四类差异 + verdict。"""
    flow = FlowCompare()
    if not expected_flow:
        return flow

    # 事件流按时间排序
    events = sorted(actual_events, key=_ev_time) if actual_events else []

    # 步骤间允许的最大时间间隔（毫秒）：以 step.within_ms 或默认 60s 计算，
    # 用于判断"步骤在期望时序内出现"。
    cursor = 0  # 已消费的事件索引
    last_time: Optional[float] = None

    for idx, step in enumerate(expected_flow):
        event_type = step.get("event_type", "")
        negate = bool(step.get("negate", False))
        within_ms = float(step.get("within_ms", 60000))
        optional = bool(step.get("optional", False))
        step_name = step.get("step") or event_type or f"step{idx}"

        if negate:
            # 负向断言：在窗口内扫到即触发
            triggered = False
            for ev in events:
                ev_t = _ev_time(ev)
                if last_time is not None and ev_t < last_time:
                    continue
                if _matches(ev, event_type):
                    triggered = True
                    flow.steps.append(
                        {
                            "step": step_name,
                            "status": "negate_triggered",
                            "actual_event": {"type": ev.get("type"), "time": ev.get("time")},
                        }
                    )
                    flow.negated.append(step_name)
                    flow.verdict = "fail"
                    break
            if not triggered:
                flow.steps.append({"step": step_name, "status": "negate_ok"})
            continue

        # 正步：从 cursor 起找第一个匹配事件
        found = None
        found_idx = -1
        for i in range(cursor, len(events)):
            if _matches(events[i], event_type):
                found = events[i]
                found_idx = i
                break

        if found is None:
            flow.steps.append(
                {
                    "step": step_name,
                    "status": "missing",
                    "expected_within_ms": within_ms,
                }
            )
            flow.missing.append(step_name)
            if not optional:
                flow.verdict = "fail"
            continue

        # 超时判定：相对上一命中步的时间窗
        t_now = _ev_time(found)
        if last_time is not None and (t_now - last_time) * 1000 > within_ms:
            flow.steps.append(
                {
                    "step": step_name,
                    "status": "timeout",
                    "actual_time": found.get("time"),
                    "expected_within_ms": within_ms,
                }
            )
            flow.timeouts.append(step_name)
            if not optional:
                flow.verdict = "fail"
        else:
            flow.steps.append(
                {
                    "step": step_name,
                    "status": "hit",
                    "actual_time": found.get("time"),
                }
            )
        last_time = t_now
        cursor = found_idx + 1

    # 顺序错乱检测：期望步骤依次出现但某些步骤在后续又被更低序命中前消费 ——
    # 简化：对每一步记录命中索引，若后续步命中索引 < 前一步命中索引则乱序。
    hit_indices: List[int] = []
    for st in flow.steps:
        if st["status"] == "hit":
            # 重新计算该步在 events 中的位置（按匹配顺序）
            ev_type = None
            for es in expected_flow:
                if (es.get("step") or es.get("event_type")) == st["step"]:
                    ev_type = es.get("event_type")
                    break
            for i, ev in enumerate(events):
                if ev_type and _matches(ev, ev_type):
                    hit_indices.append(i)
                    break
    for i in range(1, len(hit_indices)):
        if hit_indices[i] < hit_indices[i - 1]:
            nm = flow.steps[i]["step"] if i < len(flow.steps) else "?"
            if nm not in flow.out_of_order:
                flow.out_of_order.append(nm)
            flow.verdict = "fail"

    if flow.verdict == "fail" and not flow.missing and not flow.timeouts \
            and not flow.out_of_order and not flow.negated:
        flow.verdict = "pass"
    return flow
