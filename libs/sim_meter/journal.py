"""模拟电表会话帧日志：内存镜像 + JSONL 追加 + 过滤查询。

一个会话 = 串口从打开到关闭。每收发一帧记录一条：
    {"seq": 3, "ts": "...", "dir": "tx"|"rx", "frame_hex": "68...",
     "note": {...}}            # note 为 MeterBrain.handle_frame 的说明

与 sim_concentrator.journal 分开实现：645 帧无需 1376.2 的 AFN/FN 摘要，
保持轻量；JSONL 落盘 data/logs/simeter/。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_MEMORY_LIMIT = 5000
_PORT_SANITIZE = re.compile(r"[^A-Za-z0-9_-]+")


def default_log_dir() -> Path:
    """帧日志根目录：frozen 下为 exe 同目录 data/logs/simeter，否则项目根。"""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent.parent
    return root / "data" / "logs" / "simeter"


def _safe_copy(note: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """深拷贝说明字典，隔离调用方后续变更（如波特率热切换回填）。"""
    if not note:
        return {}
    try:
        return json.loads(json.dumps(note, ensure_ascii=False, default=str))
    except Exception:
        return {k: str(v) for k, v in note.items()}


class MeterJournal:
    """单会话帧日志。"""

    def __init__(self, *, port: str, log_dir: Optional[Path] = None,
                 memory_limit: int = _MEMORY_LIMIT, session_id: Optional[str] = None):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_port = _PORT_SANITIZE.sub("-", str(port or "COM"))
        self.session_id = session_id or f"sm-{stamp}-{safe_port}"
        self.port = str(port or "")
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self._entries: deque = deque(maxlen=max(1, int(memory_limit)))
        self._seq = 0
        self._lock = threading.RLock()
        self._fh = None
        if log_dir is not None:
            try:
                log_dir = Path(log_dir)
                log_dir.mkdir(parents=True, exist_ok=True)
                self._fh = (log_dir / f"{self.session_id}.jsonl").open(
                    "a", encoding="utf-8", buffering=1)
            except Exception:
                self._fh = None

    def append(self, direction: str, raw: bytes, note: Optional[Dict[str, Any]] = None) -> Optional[dict]:
        raw = bytes(raw or b"")
        if not raw:
            return None
        try:
            with self._lock:
                self._seq += 1
                entry = {
                    "seq": self._seq,
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "ts_epoch": time.time(),
                    "dir": "tx" if direction == "tx" else "rx",
                    "frame_hex": raw.hex().upper(),
                    "note": _safe_copy(note),
                }
                self._entries.append(entry)
                if self._fh is not None:
                    self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                return dict(entry)
        except Exception:
            return None

    def event(self, note: Dict[str, Any]) -> Optional[dict]:
        """记录一条非帧事件（如波特率热切换），dir="event"。"""
        try:
            with self._lock:
                self._seq += 1
                entry = {
                    "seq": self._seq,
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "ts_epoch": time.time(),
                    "dir": "event",
                    "frame_hex": "",
                    "note": _safe_copy(note),
                }
                self._entries.append(entry)
                if self._fh is not None:
                    self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                return dict(entry)
        except Exception:
            return None

    def entries(self, *, direction: Optional[str] = None, after_seq: int = 0,
                limit: int = 200) -> List[dict]:
        with self._lock:
            items = [dict(e) for e in self._entries
                     if (direction is None or e["dir"] == direction)
                     and e["seq"] > int(after_seq)]
        return items[-max(1, int(limit)):]

    def info(self) -> dict:
        with self._lock:
            return {
                "session_id": self.session_id,
                "port": self.port,
                "started_at": self.started_at,
                "frames": len(self._entries),
            }

    def close_file(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass
        self._fh = None
