"""分钟采集周期统计引擎（移植自 H_CCO/analyze_minute_logs.py）。

场景口径：
- 主动上报（11e4）：数据区非空视为上报成功，不校验结果码。
- 被动上报（11e3 reply）：数据区非空视为上报成功，不校验结果码。
- 被动采集（11e3 request）：有请求但本周期无任何应答 → 下发采集无响应；
  有应答但数据区全空 → 被动上报无数据。
- 任务配置下发（11e2/11H_F231）：启停标志 1=配置/启动，0=删除下发，
  任务号 0xFF 且启停标志 0 为全部任务删除；仅配置帧写入任务周期映射，
  删除帧只计数（任务级删除计入 TaskStats.delete_count，全部删除单独返回）。
周期窗口从任务首次出现记录的冻结时刻对齐到周期起点，到末次记录结束，
中间所有周期全部输出（含全零周期）。仅统计 11H_F232 配置档案内的表，
未配置档案的上报不计入成功与失败。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .parsers import (
    SCENE_PASSIVE_COLLECT,
    SCENE_PASSIVE_REPORT,
    SCENE_ACTIVE_REPORT,
    classify_11e3_scene,
    dedup_key,
    has_data_region,
    parse_active_report,
    parse_f232_frame,
    parse_read_frame,
    parse_task_config_any,
)
from .timeutil import (
    cycle_window_key,
    cycle_window_label,
    fz_minutes,
    fz_text,
    period_range,
)


@dataclass(frozen=True)
class TaskCycleStats:
    index: int
    window_label: str
    active_ok: int
    passive_ok: int
    passive_collect_failed: tuple[tuple[str, str, str], ...]

    @property
    def success_count(self) -> int:
        return self.active_ok + self.passive_ok


@dataclass(frozen=True)
class TaskStats:
    task_id: int
    period: int | None
    configured_addresses: tuple[str, ...]
    cycles: tuple[TaskCycleStats, ...]
    delete_count: int = 0


def _empty_cycle_bucket() -> dict[str, object]:
    """单个采集窗口的聚合桶初始结构。"""
    return {
        "active_any": set(),
        "active_ok": set(),
        "active_events": [],
        "reply_any": set(),
        "reply_ok": set(),
        "reply_empty": set(),
        "reply_events": [],
        "requested": set(),
        "request_events": [],
    }


def _record_configured(
    task_configured: dict[int, set[str]],
    task_configured_list: dict[int, list[str]],
    task_id: int,
    addresses: Sequence[str],
) -> None:
    """记录一次 F232 档案下发：集合用于成员判断，列表保持首现顺序用于展示。"""
    task_configured.setdefault(task_id, set()).update(addresses)
    ordered = task_configured_list.setdefault(task_id, [])
    for address in addresses:
        if address not in ordered:
            ordered.append(address)


def collect_task_statistics(
    log_files: Iterable[Path],
) -> tuple[dict[int, int], list[TaskStats], int]:
    """扫描任务配置、档案下发、上报与采集帧，返回 (任务周期映射, 各任务逐周期统计, 全部删除下发次数)。"""
    task_period_map: dict[int, int] = {}
    task_configured: dict[int, set[str]] = {}
    task_configured_list: dict[int, list[str]] = {}
    task_delete_count: dict[int, int] = {}
    broadcast_delete_count = 0
    active_reports: dict[int, list[tuple[str, bytes, bool]]] = {}
    read_events: dict[int, list[tuple[str, str, bytes, bool]]] = {}
    first_event_minutes: dict[int, int] = {}
    last_event_minutes: dict[int, int] = {}

    def record_event(task_id: int, fz_bytes: bytes) -> None:
        minutes = fz_minutes(fz_bytes)
        if task_id not in first_event_minutes or minutes < first_event_minutes[task_id]:
            first_event_minutes[task_id] = minutes
        if task_id not in last_event_minutes or minutes > last_event_minutes[task_id]:
            last_event_minutes[task_id] = minutes

    for log_file in log_files:
        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as input_file:
                f232_buffer = ""
                f232_need = 0
                for raw_line in input_file:
                    content = dedup_key(raw_line)
                    if f232_buffer:
                        if re.fullmatch(r"[0-9a-fA-F]+", content):
                            f232_buffer += content
                        else:
                            f232_buffer = ""
                            f232_need = 0
                        if f232_buffer and len(f232_buffer) // 2 >= f232_need:
                            parsed = parse_f232_frame(
                                bytes.fromhex(f232_buffer[: f232_need * 2])
                            )
                            if parsed is not None:
                                _record_configured(
                                    task_configured,
                                    task_configured_list,
                                    parsed[0],
                                    parsed[1],
                                )
                            f232_buffer = ""
                            f232_need = 0
                        continue
                    if content.startswith("68") and len(content) >= 26:
                        if not re.fullmatch(r"[0-9a-fA-F]{26}", content[:26]):
                            continue
                        head = bytes.fromhex(content[:26])
                        if (
                            head[3] == 0x43
                            and head[10] == 0x11
                            and head[11] == 0x80
                            and head[12] == 0x1C
                        ):
                            f232_need = head[1] | (head[2] << 8)
                            f232_buffer = content
                            if len(f232_buffer) // 2 >= f232_need:
                                parsed = parse_f232_frame(
                                    bytes.fromhex(f232_buffer[: f232_need * 2])
                                )
                                if parsed is not None:
                                    _record_configured(
                                        task_configured,
                                        task_configured_list,
                                        parsed[0],
                                        parsed[1],
                                    )
                                f232_buffer = ""
                                f232_need = 0
                            continue
                    config = parse_task_config_any(raw_line)
                    if config is not None:
                        task_id, period, switch_flag = config
                        if task_id == 0xFF and switch_flag == 0:
                            broadcast_delete_count += 1
                        elif switch_flag == 0:
                            task_delete_count[task_id] = (
                                task_delete_count.get(task_id, 0) + 1
                            )
                        else:
                            task_period_map[task_id] = period
                        continue
                    report = parse_active_report(raw_line)
                    if report is not None:
                        address, task_id, fz_bytes, _result = report
                        has_data = has_data_region(
                            bytes.fromhex(content), SCENE_ACTIVE_REPORT
                        )
                        active_reports.setdefault(task_id, []).append(
                            (address, fz_bytes, has_data)
                        )
                        record_event(task_id, fz_bytes)
                        continue
                    read_frame = parse_read_frame(raw_line)
                    if read_frame is not None:
                        direction, task_id, address, fz_bytes, _result = read_frame
                        scene = classify_11e3_scene(read_frame)
                        has_data = has_data_region(bytes.fromhex(content), scene)
                        read_events.setdefault(task_id, []).append(
                            (scene, address, fz_bytes, has_data)
                        )
                        record_event(task_id, fz_bytes)
        except OSError:
            continue

    task_stats: list[TaskStats] = []
    for task_id in sorted(set(active_reports) | set(read_events)):
        reports = active_reports.get(task_id, [])
        reads = read_events.get(task_id, [])
        if task_id not in first_event_minutes:
            continue
        period = task_period_map.get(task_id)
        if period is None or period < 1:
            fz_minutes_list = sorted(
                fz_minutes(fz)
                for fz in (
                    [fz for _, fz, _ in reports]
                    + [fz for _, _, fz, _ in reads]
                )
            )
            gaps = [
                b - a
                for a, b in zip(fz_minutes_list, fz_minutes_list[1:])
                if 0 < b - a <= 60
            ]
            period = min(gaps) if gaps else 1
        configured = task_configured.get(task_id)
        if configured is not None and not configured:
            configured = None
        configured_list = task_configured_list.get(task_id, [])

        buckets: dict[int, dict[str, object]] = {}
        for address, fz, has_data in reports:
            bucket = buckets.setdefault(
                cycle_window_key(fz, period), _empty_cycle_bucket()
            )
            bucket["active_any"].add(address)
            if has_data:
                bucket["active_ok"].add(address)
            bucket["active_events"].append((address, fz_text(fz), has_data))
        for scene, address, fz, has_data in reads:
            bucket = buckets.setdefault(
                cycle_window_key(fz, period), _empty_cycle_bucket()
            )
            if scene == SCENE_PASSIVE_COLLECT:
                bucket["requested"].add(address)
                bucket["request_events"].append((address, fz_text(fz)))
            else:
                bucket["reply_any"].add(address)
                if has_data:
                    bucket["reply_ok"].add(address)
                else:
                    bucket["reply_empty"].add(address)
                bucket["reply_events"].append((address, fz_text(fz), has_data))

        cycles: list[TaskCycleStats] = []
        for index, window_start in enumerate(
            period_range(
                first_event_minutes[task_id],
                last_event_minutes[task_id],
                period,
            ),
            start=1,
        ):
            bucket = buckets.get(window_start, _empty_cycle_bucket())
            active_any = bucket["active_any"]
            active_ok = bucket["active_ok"]
            reply_any = bucket["reply_any"]
            reply_ok = bucket["reply_ok"]
            reply_empty = bucket["reply_empty"]
            requested = bucket["requested"]
            in_scope = (
                configured
                if configured is not None
                else set(active_any) | set(reply_any) | set(requested)
            )
            active_ok = in_scope & set(active_ok)
            passive_ok = in_scope & set(reply_ok)
            no_reply = in_scope & set(requested) - (in_scope & set(reply_any))
            reply_no_data = (in_scope & set(reply_empty)) - passive_ok
            failures: list[tuple[str, str, str]] = []
            for address in sorted(no_reply):
                time_text = next(
                    (t for a, t in bucket["request_events"] if a == address),
                    "",
                )
                failures.append((address, time_text, "下发采集无响应"))
            for address in sorted(reply_no_data):
                time_text = next(
                    (t for a, t, _ in bucket["reply_events"] if a == address),
                    "",
                )
                failures.append((address, time_text, "被动上报无数据"))
            cycles.append(
                TaskCycleStats(
                    index=index,
                    window_label=cycle_window_label(window_start, period),
                    active_ok=len(active_ok),
                    passive_ok=len(passive_ok),
                    passive_collect_failed=tuple(failures),
                )
            )
        task_stats.append(
            TaskStats(
                task_id=task_id,
                period=period,
                configured_addresses=tuple(configured_list),
                cycles=tuple(cycles),
                delete_count=task_delete_count.get(task_id, 0),
            )
        )
    return task_period_map, task_stats, broadcast_delete_count


def _wrap_failure_details(
    failures: Sequence[tuple[str, str, str]], width: int = 72
) -> list[str]:
    """将失败明细条目按宽度折行，条目间用 || 分隔，每行内部不截断单条。"""
    pieces = [
        f"表{address}-{time_text}{reason}"
        for address, time_text, reason in failures
    ]
    lines: list[str] = []
    current = ""
    for piece in pieces:
        candidate = piece if not current else f"{current}||{piece}"
        if len(candidate) > width and current:
            lines.append(current)
            current = piece
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def format_task_statistics(
    task_stats: Sequence[TaskStats],
    broadcast_delete_count: int = 0,
) -> str:
    """生成任务专项统计文本块（核心配置行 + 逐周期统计行）。"""
    if not task_stats:
        return ""
    lines = ["", "=" * 66, "                          任务专项统计", "=" * 66]
    if broadcast_delete_count > 0:
        lines.append(f"全部任务删除下发{broadcast_delete_count}次")
    for task in task_stats:
        period_text = f"{task.period}" if task.period else "未知"
        configured_text = (
            "、".join(task.configured_addresses)
            if task.configured_addresses
            else "空"
        )
        delete_text = (
            f"，删除下发{task.delete_count}次" if task.delete_count > 0 else ""
        )
        lines += [
            "",
            f"任务{task.task_id}：配置电表{len(task.configured_addresses)}只，"
            f"周期为{period_text}，关联表档案为{configured_text}{delete_text}",
        ]
        for cycle in task.cycles:
            base = (
                f"周期{cycle.index}【{cycle.window_label}】统计信息："
                f"{cycle.success_count}只表上报成功，{cycle.active_ok}只表为主动上报，"
                f"{cycle.passive_ok}只表为被动上报，"
                f"{len(cycle.passive_collect_failed)}只表被动采集失败"
            )
            if not cycle.passive_collect_failed:
                lines.append(base)
                continue
            wrapped = _wrap_failure_details(cycle.passive_collect_failed)
            lines.append(f"{base}[{wrapped[0]}")
            for extra in wrapped[1:]:
                lines.append("   " + extra)
            lines[-1] += "]"
    lines += ["", "=" * 66]
    return "\n".join(lines)
