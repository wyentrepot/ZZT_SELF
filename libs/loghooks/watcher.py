"""loghooks watcher —— 持续监听日志文件，实时产出事件。

区别于 scan（离线扫一遍），watch 是「tail + 引擎」：
- 打开一个或多个日志文件，从末尾 seek，循环读新增行；
- 每行经 parse_module_log → Engine.feed 实时匹配；
- 支持运行时注入自定义 pattern（不必写进 rules/ 文件）：
  构造一条临时 Rule，命中即产事件；
- 结束条件：--timeout 到达，或 --pattern 命中后提前返回。

用法：
    from loghooks.watcher import watch
    result = watch(["LOG/模块/cco/x.log"], pattern="\\[MYLOG\\] foo", timeout=60)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, List, Optional

from loghooks.engine import Engine, Event
from loghooks.rules import Rule
from loghooks.sources import parse_module_log

logger = logging.getLogger(__name__)

# 事件命中记录（结构化输出用）
WatchEvent = dict


def _make_pattern_rule(pattern: str, rule_id: str = "watch.pattern") -> Rule:
    """由 --pattern 运行时构造一条临时文本匹配规则（不写 rules/ 文件）。

    match 对齐 rules.py 的 MatchDef text 模式（正则，忽略大小写）；
    event 类型用 rule_id，便于结果区分。
    """
    from loghooks.rules import MatchDef

    return Rule(
        id=rule_id,
        category="watch",
        level="info",
        scope="common",
        module="common",
        province=None,
        source=["module_log"],
        match=MatchDef.from_dict(
            {"mode": "text", "pattern": pattern, "flags": ["i"]}
        ),
        capture={},
        event={
            "type": "watch.hit",
            "label": f"自定义断言命中: {pattern}",
            "message": f"匹配到: {pattern}",
        },
    )


def watch(
    log_paths: List[str],
    pattern: Optional[str] = None,
    timeout: float = 60.0,
    on_event: Optional[Callable[[Event], None]] = None,
    poll_interval: float = 0.1,
) -> dict:
    """持续监听日志文件，返回事件结果 dict。

    参数：
        log_paths: 日志文件路径列表（不存在/无法打开则跳过该文件）。
        pattern:   可选自定义断言正则；命中后立即返回（提前结束）。
        timeout:   监听时长上限（秒）。默认 60。
        on_event:  可选实时回调（每命中一条事件调用）。
        poll_interval: 文件轮询间隔（秒）。

    返回：
        {
          "watch": True,
          "timeout": float,           # 实际监听时长
          "stopped_by": "pattern" | "timeout",
          "files": [ ... ],            # 实际监听的文件
          "total_lines": int,          # 各文件新增行总数
          "event_count": int,
          "events": [ {type,label,message,time,rule_id,source_line}, ... ],
          "pattern_hit": bool,         # pattern 是否命中
          "hit_count": int,            # pattern 命中次数
          "hit_times": [ ... ],        # pattern 命中时间列表
        }
    """
    from loghooks.rules import RuleLoader

    # 规则集：现有 rules/ + 可选自定义 pattern 规则
    loader = RuleLoader().load_all()
    rules = list(loader.rules)
    pattern_rule = None
    if pattern:
        pattern_rule = _make_pattern_rule(pattern)
        rules.append(pattern_rule)

    engine = Engine(rules, source="module_log", on_event=on_event)

    # 命中跟踪
    pattern_hits: List[str] = []  # 时间戳列表
    hit_lock = _threading_lock()

    def _default_on_event(ev: Event) -> None:
        if pattern_rule and ev.rule_id == pattern_rule.id:
            with hit_lock:
                pattern_hits.append(ev.time)

    engine.on_event = _default_on_event if on_event is None else on_event

    # 打开日志文件，seek 到末尾（只监听新增行）
    handles: List[tuple] = []  # (path, fileobj)
    for p in log_paths:
        path = Path(p)
        if not path.exists():
            logger.warning("watch: 日志文件不存在，跳过: %s", p)
            continue
        try:
            f = open(path, "r", encoding="utf-8", errors="replace")
            f.seek(0, 2)  # 到末尾
            handles.append((str(path), f))
        except OSError as exc:
            logger.warning("watch: 无法打开日志文件 %s: %s", p, exc)

    if not handles:
        return {
            "watch": True,
            "timeout": timeout,
            "stopped_by": "timeout",
            "files": [],
            "total_lines": 0,
            "event_count": 0,
            "events": [],
            "pattern_hit": False,
            "hit_count": 0,
            "hit_times": [],
            "error": "没有可监听的日志文件（路径不存在或无法打开）",
        }

    start = time.time()
    total_lines = 0
    try:
        while time.time() - start < timeout:
            progressed = False
            for path, f in handles:
                for raw in f:
                    line = raw.rstrip("\r\n")
                    if not line:
                        continue
                    total_lines += 1
                    parsed = parse_module_log(line)
                    if parsed is not None:
                        engine.feed(parsed)
                        progressed = True

            # pattern 命中即提前返回
            if pattern and pattern_hits:
                break
            time.sleep(poll_interval)

    finally:
        for _, f in handles:
            try:
                f.close()
            except OSError:
                pass

    stopped_by = "pattern" if (pattern and pattern_hits) else "timeout"
    events = [
        {
            "type": e.type,
            "label": e.label,
            "message": e.message,
            "level": e.level,
            "time": e.time,
            "rule_id": e.rule_id,
            "category": e.category,
            "source_line": e.source_line,
        }
        for e in engine.events
    ]

    return {
        "watch": True,
        "timeout": round(time.time() - start, 2),
        "stopped_by": stopped_by,
        "files": [p for p, _ in handles],
        "total_lines": total_lines,
        "event_count": len(events),
        "events": events,
        "pattern_hit": bool(pattern_hits),
        "hit_count": len(pattern_hits),
        "hit_times": pattern_hits,
    }


def _threading_lock():
    import threading

    return threading.Lock()
