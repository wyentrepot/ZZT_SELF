"""会话帧日志（FrameJournal）：模拟集中器 tx/rx 帧的记录、JSONL 持久化与查询。

一个会话（session）= 串口从打开到关闭（或一次 verify 自建临时串口的完整
生命周期）。会话内每收发一帧记录一条：

    {"seq": 3, "ts": "...", "ts_epoch": ..., "dir": "tx"|"rx",
     "kind": "step_send"|"manual_send"|"auto_reply"|null,   # 仅 tx 有语义
     "run_id": "run-...",                                   # 本次运行的归属标记
     "frame_hex": "68...", "afn": "06", "fn": "F230",
     "updown": "up"|"down",                                  # C 位 DIR：up=CCO 上行（主动上报）
     "parsed": {...}|null}                                   # 安全解码结果（可缺）

- 持久化：每会话一个 JSONL（data/logs/simcon/sc-*.jsonl），逐行追加。
- 查询：内存镜像（有界 deque）支撑 /frames 过滤（方向/afn/fn/kind/run_id/游标）。
- run 归属：journal.scope(run_id, kind) 上下文给期间产生的帧打标；verify 任务
  用它圈定"本次运行"，帧 seq 区间随任务结果返回。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional


_MEMORY_LIMIT = 5000          # 单会话内存镜像上限（条）
_SESSION_RETAIN = 10          # SessionManager 保留的最近会话数（含临时会话）
_PORT_SANITIZE = re.compile(r"[^A-Za-z0-9_-]+")


def default_log_dir() -> Path:
    """帧日志根目录：frozen 下为 exe 同目录 data/logs/simcon，否则项目根 data/logs/simcon。

    只计算路径不建目录；目录在真正写会话文件时（FrameJournal）按需创建，
    避免应用启动/测试就污染真实运行目录。
    """
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent.parent
    return root / "data" / "logs" / "simcon"


def normalize_afn(value: Any) -> Optional[str]:
    """afn 归一为两位大写十六进制串（6/"6"/"0x06"/"06"/int 0x06 → "06"）。

    注意：int 直接按数值转十六进制（0x10 → "10"），不当作 hex 字符串解析，
    避免把已解码的 int 16 误读为字符串 "16" → 0x16。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        if not 0 <= value <= 0xFF:
            return None
        return f"{value:02X}"
    try:
        text = str(value).strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        n = int(text, 16)
    except (TypeError, ValueError):
        return None
    if not 0 <= n <= 0xFF:
        return None
    return f"{n:02X}"


def normalize_fn(value: Any) -> Optional[str]:
    """fn 归一为 "F" + 十进制编号（230/"230"/"F230" → "F230"，对齐 AFN/Fn 语义）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if text.upper().startswith("F") and text[1:].isdigit():
        text = text[1:]
    try:
        n = int(text, 10)
    except (TypeError, ValueError):
        return None
    if not 0 <= n <= 0xFFFF:
        return None
    return f"F{n}"


def summarize_frame(raw: bytes) -> dict:
    """安全解码一帧，提炼 afn/fn/updown 与全量 parsed（失败不抛）。"""
    summary: dict[str, Any] = {"afn": None, "fn": None, "updown": None, "parsed": None}
    try:
        from sim_concentrator.frame_codec import decode_frame
        parsed = decode_frame(raw)
    except Exception:
        try:
            from sim_concentrator.frame_codec import decode_local_13762_frame
            parsed = decode_local_13762_frame(raw)
        except Exception:
            return summary
    summary["parsed"] = parsed
    fields = parsed.get("fields") or {}
    afn = fields.get("AFN", {}).get("raw")
    if not isinstance(afn, int):
        afn = parsed.get("afn")
    fn = fields.get("FN", {}).get("raw")
    if not isinstance(fn, int):
        fn = parsed.get("fn")
    ctl = fields.get("控制域C", {}).get("raw")
    if not isinstance(ctl, int):
        ctl = parsed.get("ctrl")
    summary["afn"] = normalize_afn(afn)
    summary["fn"] = normalize_fn(fn)
    summary["updown"] = ("up" if ((ctl >> 7) & 1) else "down") if isinstance(ctl, int) else None
    return summary


class FrameJournal:
    """单会话帧日志：内存镜像 + JSONL 追加 + 过滤查询。"""

    def __init__(self, *, port: str, log_dir: Path | None = None,
                 memory_limit: int = _MEMORY_LIMIT, session_id: str | None = None,
                 store: Any | None = None):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_port = _PORT_SANITIZE.sub("-", str(port or "COM"))
        self.session_id = session_id or f"sc-{stamp}-{safe_port}"
        self.port = str(port or "")
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self._started_epoch = time.time()
        self._entries: deque = deque(maxlen=max(1, int(memory_limit)))
        self._seq = 0
        self._lock = threading.RLock()
        self._scopes: list[tuple[Optional[str], Optional[str]]] = []
        self._path: Path | None = None
        self._rel_path: str | None = None
        self._fh = None
        # REQS-0013：可选 1376.2 收发库（ListenerStore），append 时同步落库
        self.store = store
        if log_dir is not None:
            try:
                log_dir = Path(log_dir)
                log_dir.mkdir(parents=True, exist_ok=True)
                self._path = log_dir / f"{self.session_id}.jsonl"
                try:
                    self._rel_path = str(self._path.relative_to(
                        Path(__file__).resolve().parent.parent.parent))
                except ValueError:
                    self._rel_path = self._path.name
                self._fh = self._path.open("a", encoding="utf-8", buffering=1)
            except Exception:
                self._path, self._rel_path, self._fh = None, None, None

    # -- 记录 -------------------------------------------------------------
    @contextmanager
    def scope(self, run_id: Optional[str] = None,
              kind: Optional[str] = None) -> Iterator[None]:
        """给期间产生的帧打 run_id/kind 标（内层 kind 覆盖外层，run_id 就近）。"""
        with self._lock:
            self._scopes.append((run_id, kind))
        try:
            yield
        finally:
            with self._lock:
                self._scopes.pop()

    def _current_scope(self) -> tuple[Optional[str], Optional[str]]:
        run_id = None
        kind = None
        for sid, kind_ in self._scopes:
            if run_id is None and sid:
                run_id = sid
            kind = kind_
        return run_id, kind

    def append(self, direction: str, raw: bytes, *, kind: Optional[str] = None,
               run_id: Optional[str] = None, parsed: Optional[dict] = None,
               afn: Optional[str] = None, fn: Optional[str] = None,
               updown: Optional[str] = None) -> Optional[dict]:
        """记录一帧（tx/rx），返回条目；raw 为空或异常时不中断调用方。"""
        raw = bytes(raw or b"")
        if not raw:
            return None
        try:
            with self._lock:
                self._seq += 1
                seq = self._seq
                scope_run_id, scope_kind = self._current_scope()
                if direction == "tx" and kind is None:
                    kind = scope_kind
                if run_id is None:
                    run_id = scope_run_id
                summary = summarize_frame(raw)
                entry = {
                    "seq": seq,
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "ts_epoch": time.time(),
                    "dir": "tx" if direction == "tx" else "rx",
                    "kind": kind,
                    "run_id": run_id,
                    "frame_hex": raw.hex(),
                    "afn": afn or summary["afn"],
                    "fn": fn or summary["fn"],
                    "updown": updown or summary["updown"],
                    "parsed": parsed if parsed is not None else summary["parsed"],
                }
                self._entries.append(entry)
                if self._fh is not None:
                    try:
                        self._fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                    except Exception:
                        pass
                # REQS-0013：同步灌入 1376.2 收发库（失败不中断帧日志）
                if self.store is not None:
                    try:
                        entry["session_id"] = self.session_id
                        from sim_concentrator.sink import ingest_entry
                        ingest_entry(self.store, entry)
                    except Exception:
                        pass
                # REQS-0013：上行响应帧附契约驱动的记录解析（前端表格直接用）
                if entry["dir"] == "rx" and entry.get("updown") == "up":
                    try:
                        from sim_concentrator.sink import enrich_response
                        entry["resp"] = enrich_response(entry)
                    except Exception:
                        pass
                return entry
        except Exception:
            return None

    # -- 查询 -------------------------------------------------------------
    def query(self, *, direction: Optional[str] = None, updown: Optional[str] = None,
              afn: Optional[str] = None, fn: Optional[str] = None,
              kind: Optional[str] = None, run_id: Optional[str] = None,
              after_seq: int = 0, limit: int = 100) -> dict:
        want_afn = normalize_afn(afn)
        want_fn = normalize_fn(fn)
        want_dir = direction if direction in ("tx", "rx") else None
        want_updown = updown if updown in ("up", "down") else None
        limit = max(1, min(int(limit or 100), 500))
        after_seq = max(0, int(after_seq or 0))
        with self._lock:
            items = list(self._entries)
        counts = {"tx": 0, "rx": 0, "uplink": 0}
        selected: list[dict] = []
        for entry in items:
            if entry["dir"] == "tx":
                counts["tx"] += 1
            else:
                counts["rx"] += 1
                if entry.get("updown") == "up":
                    counts["uplink"] += 1
            if entry["seq"] <= after_seq:
                continue
            if want_dir and entry["dir"] != want_dir:
                continue
            if want_updown and entry.get("updown") != want_updown:
                continue
            if want_afn and entry.get("afn") != want_afn:
                continue
            if want_fn and entry.get("fn") != want_fn:
                continue
            if kind and entry.get("kind") != kind:
                continue
            if run_id and entry.get("run_id") != run_id:
                continue
            if len(selected) < limit:
                selected.append(entry)
        matched_total = sum(
            1 for entry in items
            if entry["seq"] > after_seq
            and (not want_dir or entry["dir"] == want_dir)
            and (not want_updown or entry.get("updown") == want_updown)
            and (not want_afn or entry.get("afn") == want_afn)
            and (not want_fn or entry.get("fn") == want_fn)
            and (not kind or entry.get("kind") == kind)
            and (not run_id or entry.get("run_id") == run_id)
        )
        next_after = selected[-1]["seq"] if selected else after_seq
        has_more = matched_total > len(selected)
        return {
            "session_id": self.session_id,
            "entries": selected,
            "next_after_seq": next_after,
            "matched_total": matched_total,
            "has_more": has_more,
            "counts": counts,
        }

    def info(self) -> dict:
        with self._lock:
            items = list(self._entries)
        counts = {"tx": 0, "rx": 0, "uplink": 0}
        for entry in items:
            counts["tx" if entry["dir"] == "tx" else "rx"] += 1
            if entry["dir"] == "rx" and entry.get("updown") == "up":
                counts["uplink"] += 1
        return {
            "session_id": self.session_id,
            "port": self.port,
            "started_at": self.started_at,
            "last_seq": self._seq,
            "counts": counts,
            "log_file": self._rel_path,
            "open": self._fh is not None,
        }

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def close_file(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None


class SessionManager:
    """会话保留表：当前会话 + 最近 N 个历史会话（供 verify 临时串口事后查询）。"""

    def __init__(self, log_dir: Path | None = None, retain: int = _SESSION_RETAIN,
                 store: Any | None = None):
        self._log_dir = log_dir if log_dir is not None else default_log_dir()
        self._retain = max(1, int(retain))
        self._sessions: "dict[str, FrameJournal]" = {}
        self._current: FrameJournal | None = None
        self._lock = threading.RLock()
        self._store = store

    def open_session(self, port: str) -> FrameJournal:
        """新建会话并置为当前（旧当前会话仅关闭文件，保留在历史里可查）。"""
        journal = FrameJournal(port=port, log_dir=self._log_dir, store=self._store)
        with self._lock:
            if self._current is not None:
                self._current.close_file()
            self._sessions[journal.session_id] = journal
            self._current = journal
            self._trim()
        return journal

    def register_ephemeral(self, journal: FrameJournal) -> None:
        """登记一次临时串口会话（不改变当前会话指针）。"""
        with self._lock:
            self._sessions[journal.session_id] = journal
            self._trim()

    def finalize(self, journal: FrameJournal) -> None:
        journal.close_file()

    def current(self) -> Optional[FrameJournal]:
        with self._lock:
            if self._current is not None and self._current._fh is not None:
                return self._current
            return self._current

    def current_or_latest(self) -> Optional[FrameJournal]:
        with self._lock:
            if self._current is not None:
                return self._current
            if self._sessions:
                return list(self._sessions.values())[-1]
            return None

    def get(self, session_id: str) -> Optional[FrameJournal]:
        with self._lock:
            return self._sessions.get(str(session_id or ""))

    def resolve(self, session_id: str | None = None) -> Optional[FrameJournal]:
        if session_id:
            return self.get(session_id)
        return self.current_or_latest()

    def list_info(self) -> list[dict]:
        with self._lock:
            journals = list(self._sessions.values())
        current_id = self._current.session_id if self._current else None
        infos = []
        for journal in journals:
            info = journal.info()
            info["current"] = info["session_id"] == current_id
            infos.append(info)
        return infos

    def _trim(self) -> None:
        while len(self._sessions) > self._retain:
            oldest_id, oldest = next(iter(self._sessions.items()))
            if oldest is self._current:
                break
            oldest.close_file()
            self._sessions.pop(oldest_id)
