"""loghooks 运行时接入：异步队列消费者，供 module_log 实时产事件。

设计约束：
- 异步 + 队列消费，不阻塞串口写盘、不拖慢日志采集。
- 事件实时落盘到 LOG/模块/事件/<channel>/*.jsonl（每行一条事件）。
- 可开关：环境变量 LOG_HOOKS_ENABLED，默认开但失败静默降级。
- 规则从 loghooks/rules/ 自动加载。
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import List, Optional

from .engine import Engine, Event
from .rules import RuleLoader
from .sources import parse_module_log

# 队列与消费者全局单例（进程内一个即可）
_queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=2000)
_consumer: Optional[threading.Thread] = None
_lock = threading.Lock()
_rules_cache: Optional[List] = None
_rules_ts = 0.0

ENABLED = os.environ.get("LOG_HOOKS_ENABLED", "1").lower() not in ("0", "false", "no", "off")


def _load_rules():
    """加载规则（带缓存，规则文件变更后自动重载）。"""
    global _rules_cache, _rules_ts
    try:
        import time
        rules_dir = Path(__file__).parent / "rules"
        # 纳入 cco.json / sta.json / common.json + provinces/*.json（模块文件变更也要触发重载）
        rule_files = [rules_dir / "common.json", rules_dir / "cco.json", rules_dir / "sta.json"]
        rule_files += list((rules_dir / "provinces").glob("*.json"))
        mtime = max(
            (p.stat().st_mtime for p in rule_files if p.exists()),
            default=0,
        )
        if _rules_cache is None or mtime != _rules_ts:
            loader = RuleLoader().load_all()
            if not loader.errors:
                _rules_cache = loader.rules
                _rules_ts = mtime
    except Exception:
        pass
    return _rules_cache or []


def _consumer_loop() -> None:
    """后台消费者：从队列取行，跑引擎，事件落盘。"""
    while True:
        try:
            item = _queue.get()
        except Exception:
            break
        if item is None:  # 哨兵：退出
            break
        module, direction, text = item
        try:
            _process_one(module, direction, text)
        except Exception:
            pass  # 静默降级
        finally:
            _queue.task_done()


def _process_one(module: str, direction: str, text: str) -> None:
    """处理单行（在消费者线程中执行）。"""
    # 组装模块日志格式行（补时间戳前缀）
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d-%H:%M:%S") + f":{datetime.datetime.now().microsecond//1000:03d}"
    line_text = f"[{ts}] [{direction}] {text}"
    parsed = parse_module_log(line_text)
    if parsed is None:
        return

    rules = _load_rules()
    if not rules:
        return
    # 按模块过滤：只加载该 module + common 的规则
    if module in ("cco", "sta"):
        match_rules = [r for r in rules if r.match is not None and "module_log" in r.source
                       and (r.module == module or r.module == "common")]
    else:
        match_rules = [r for r in rules if r.match is not None and "module_log" in r.source]
    if not match_rules:
        return

    engine = Engine(match_rules, source="module_log")
    engine.feed(parsed)
    result = engine.finalize()

    if not result.events:
        return

    # 落盘到 LOG/模块/事件/<channel>/*.jsonl
    _write_events(result.events)


_event_path_cache: dict = {}


def _event_file_path() -> Path:
    """计算事件 JSONL 文件路径（按天轮转）。"""
    import datetime
    today = datetime.date.today().isoformat()
    if today in _event_path_cache:
        return _event_path_cache[today]
    base = Path("LOG") / "模块" / "事件"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{today}_loghooks.jsonl"
    _event_path_cache[today] = path
    return path


def _write_events(events: List[Event]) -> None:
    path = _event_file_path()
    with open(path, "a", encoding="utf-8", buffering=1) as f:
        for ev in events:
            f.write(json.dumps({
                "type": ev.type,
                "label": ev.label,
                "message": ev.message,
                "level": ev.level,
                "time": ev.time,
                "rule_id": ev.rule_id,
                "category": ev.category,
            }, ensure_ascii=False) + "\n")


def run_loghooks(module: str, direction: str, text: str) -> None:
    """module_log 调用点：入队一行，异步消费。失败静默降级。

    module: "cco" / "sta"（用于过滤规则）。
    返回 None（不阻塞调用方）。串口写盘主链路不受影响。
    """
    if not ENABLED:
        return
    try:
        _ensure_consumer()
        _queue.put_nowait((module, direction, text))
    except Exception:
        pass  # 队列满或异常，静默丢弃（绝不阻塞主链路）


def _ensure_consumer() -> None:
    global _consumer
    if _consumer is not None and _consumer.is_alive():
        return
    with _lock:
        if _consumer is None or not _consumer.is_alive():
            _consumer = threading.Thread(
                target=_consumer_loop, name="loghooks-consumer", daemon=True
            )
            _consumer.start()


def stop_consumer() -> None:
    """停止消费者（测试/退出时调用）。"""
    global _consumer
    with _lock:
        if _consumer is not None:
            _queue.put(None)
            _consumer = None