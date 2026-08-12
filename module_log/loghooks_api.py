"""模块日志·对照解析 API：把 loghooks 事件解析与原始日志行绑定。

为前端"对照解析"页面提供结构化数据：
- 扫描日志文件/目录，返回事件列表，每条事件携带原始日志行文本、行序号、时间、模块。
- 支持实时端口（复用现有内存缓冲日志）与打开文件两种来源。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from loghooks.engine import Engine, Event
from loghooks.rules import RuleLoader
from loghooks.sources import parse_module_log, ParsedLine

# 缓存最近一次扫描结果（供前端按文件路径再次获取，避免重复扫描）
_scan_cache: dict = {}
_CACHE_MAX = 8


def _read_file_lines(path: Path) -> List[str]:
    """按编码读取日志文件行（utf-8 优先，失败回退 gbk）。

    真实串口日志行尾常带 \r\r\n（多个回车残留），splitlines() 会把 \r\r
    拆成空行导致行间出现多余换行。这里按 \n 切分并去掉行尾 \r，
    同时过滤掉这些由行尾回车符产生的假空行。
    """
    def _split(text: str) -> List[str]:
        lines = text.split("\n")
        # 去掉行尾 \r，过滤假空行（由 \r 残留产生）
        return [ln.rstrip("\r") for ln in lines if ln.rstrip("\r") != ""]

    try:
        return _split(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError):
        try:
            return _split(path.read_text(encoding="gbk", errors="ignore"))
        except OSError:
            return []


def _iter_log_files(path: Path) -> List[Path]:
    """枚举日志文件：单个文件或目录下的 *.log/.txt。"""
    if path.is_file():
        return [path] if path.suffix in (".log", ".txt", ".jsonl") else []
    if path.is_dir():
        return sorted(
            p for p in path.iterdir()
            if p.is_file() and p.suffix in (".log", ".txt")
        )
    return []


def _detect_module(path: Path) -> str:
    """从路径/文件名推断模块：cco 或 sta。"""
    name = (path.name + " " + str(path)).lower()
    if "_[sta]" in name or "/sta" in name or "\\sta" in name:
        return "sta"
    return "cco"


def _event_to_dict(ev: Event, raw_line: str, idx: int, module: str, file: str = "") -> dict:
    """把引擎事件转为前端字典，携带原始日志行、行号与来源文件。"""
    return {
        "type": ev.type,
        "label": ev.label,
        "message": ev.message,
        "level": ev.level,
        "time": ev.time,
        "rule_id": ev.rule_id,
        "category": ev.category,
        "line": idx,          # 原始日志行序号（文件内 0-based）
        "file": file,         # 来源文件路径（多文件目录区分）
        "raw": raw_line,      # 原始日志行文本
        "module": module,
        "line_drift": ev.line_drift,
    }


def scan_log_file(
    path: Path,
    module: Optional[str] = None,
    limit: int = 2000,
) -> dict:
    """扫描单个日志文件，返回事件+原始行绑定。

    返回：
      { files, module, total_lines, events: [ {...} ], detected_provinces }
    """
    path = Path(path)
    if not path.exists():
        return {"error": f"路径不存在: {path}", "events": []}

    files = _iter_log_files(path)
    if not files:
        return {"error": f"未找到日志文件: {path}", "events": []}

    module = module or _detect_module(path)

    # 加载规则（按模块过滤）
    loader = RuleLoader().load_all()
    rules = loader.filter_by_module(module)

    all_events: List[dict] = []
    all_lines: List[dict] = []
    total_lines = 0
    hit_rule_ids = []

    for fpath in files:
        lines = _read_file_lines(fpath)
        # 全量日志行（右侧展示用）：带 file/行号/原文
        all_lines.extend([
            {"file": str(fpath), "line": i, "raw": ln}
            for i, ln in enumerate(lines)
        ])
        parsed: List[ParsedLine] = []
        for raw_idx, line in enumerate(lines):
            p = parse_module_log(line)
            if p:
                p.metadata["_idx"] = raw_idx  # 原始行索引（与 lines 对齐）
                p.metadata["_file"] = str(fpath)
                parsed.append(p)

        engine = Engine(rules, source="module_log")
        for p in parsed:
            engine.feed(p)
        result = engine.finalize()
        hit_rule_ids.extend(result.hit_rule_ids)

        # 事件与原始行绑定
        for ev in result.events:
            idx = ev.source_line_idx
            raw_line = lines[idx] if idx is not None and 0 <= idx < len(lines) else ""
            all_events.append(_event_to_dict(
                ev, raw_line,
                idx if idx is not None else -1,
                module, file=str(fpath),
            ))

        total_lines += len(lines)

    # 按时间排序（稳定排序，保持文件顺序）
    all_events.sort(key=lambda e: (e["time"], e["file"], e["line"]))

    detected = loader.detect_provinces(list(dict.fromkeys(hit_rule_ids)))

    return {
        "files": [str(f) for f in files],
        "module": module,
        "total_lines": total_lines,
        "event_count": len(all_events),
        "events": all_events[:limit],
        "lines": all_lines,  # 全量日志行（右侧，虚拟滚动用，不限流）
        "detected_provinces": detected,
        "errors": loader.errors,
    }


def ev_captures_idx(ev: Event) -> Optional[int]:
    """从事件中恢复原始日志行序号。"""
    return getattr(ev, "source_line_idx", None)


def scan_realtime(
    memory_logs: List[dict],
    module: str = "cco",
    limit: int = 2000,
) -> dict:
    """扫描实时端口的内存日志缓冲，返回事件+原始行绑定。

    memory_logs: module_log 的 logs() 返回的 lines 列表（含 ts/dir/text/seq）。
    """
    loader = RuleLoader().load_all()
    rules = loader.filter_by_module(module)

    # 复用同一引擎跨行累积，保证序列规则（跨行状态机）能命中
    engine = Engine(rules, source="module_log")
    for i, line in enumerate(memory_logs):
        text = line.get("text", "")
        direction = line.get("dir", "RX")
        ts = line.get("ts", "")
        # 组装模块日志格式行供解析
        line_text = f"[{ts}] [{direction}] {text}"
        p = parse_module_log(line_text)
        if not p:
            continue
        p.metadata["_idx"] = i
        engine.feed(p)
    result = engine.finalize()

    events: List[dict] = []
    for ev in result.events:
        idx = ev.source_line_idx
        raw_line = memory_logs[idx].get("text", "") if idx is not None and 0 <= idx < len(memory_logs) else ""
        events.append({
            "type": ev.type,
            "label": ev.label,
            "message": ev.message,
            "level": ev.level,
            "time": ev.time,
            "rule_id": ev.rule_id,
            "category": ev.category,
            "line": idx if idx is not None else -1,
            "file": "",
            "raw": raw_line,
            "module": module,
            "line_drift": ev.line_drift,
        })

    # 全量日志行（右侧展示用）
    all_lines = [
        {"file": "", "line": i, "raw": ml.get("text", "")}
        for i, ml in enumerate(memory_logs)
    ]

    return {
        "files": [],
        "module": module,
        "total_lines": len(memory_logs),
        "event_count": len(events),
        "events": events[:limit],
        "lines": all_lines,  # 全量日志行（右侧，虚拟滚动用，不限流）
        "detected_provinces": [],
        "errors": [],
    }