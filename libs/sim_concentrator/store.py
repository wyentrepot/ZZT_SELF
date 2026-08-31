# -*- coding: utf-8 -*-
"""1376.2 收发数据库（REQS-0013 P0-3）：单文件 sqlite。

分层（用户 2026-08-31 拍板）：
  持久层（只追加）
    report_event       06H F1/F3/F4/F5 上报事件
    report_meter_data  06H F2 抄读数据
    frame_log          所有 1376.2 收发帧（证据链，业务行经 frame_id 回溯）
  临时层（快照制，可清理）
    query_snapshot     一次查询/自动遍历 = 一个快照
    query_snapshot_item 快照明细行（记录字段按 AFN/Fn 映射）

上报数据保留策略（用户 2026-09-01 拍板）：06H 上报按天保存（day 列），
滚动保留最近 5 天——第 6 天写入时自动清掉第 1 天（最旧一天），循环往复。

所有业务行携带 frame_id → frame_log，保证"表格每一行可回溯原始帧"。
写操作线程安全（单连接 + WAL + 行锁）。异常不抛，失败记录到 last_error。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_REPORT_RETAIN_DAYS = 5  # 06H 上报滚动保留天数

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frame_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    ts_epoch REAL NOT NULL,
    dir TEXT NOT NULL,               -- tx / rx
    kind TEXT,
    run_id TEXT,
    afn TEXT,
    fn TEXT,
    updown TEXT,                     -- up / down
    frame_hex TEXT NOT NULL,
    parsed TEXT
);
CREATE INDEX IF NOT EXISTS idx_frame_log_afn ON frame_log(afn, fn);
CREATE INDEX IF NOT EXISTS idx_frame_log_session ON frame_log(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_frame_log_updown ON frame_log(updown);

CREATE TABLE IF NOT EXISTS report_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id INTEGER,
    ts TEXT NOT NULL,
    day TEXT NOT NULL DEFAULT '',      -- YYYY-MM-DD，按天保存
    afn TEXT NOT NULL,
    fn TEXT NOT NULL,
    event_type TEXT,                 -- F1从节点信息/F3工况变动/F4设备类型/F5事件
    payload_json TEXT NOT NULL,      -- 结构化明细（head + records）
    FOREIGN KEY (frame_id) REFERENCES frame_log(id)
);
CREATE INDEX IF NOT EXISTS idx_report_event_fn ON report_event(afn, fn, ts);
CREATE INDEX IF NOT EXISTS idx_report_event_day ON report_event(day);

CREATE TABLE IF NOT EXISTS report_meter_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_id INTEGER,
    ts TEXT NOT NULL,
    day TEXT NOT NULL DEFAULT '',      -- YYYY-MM-DD，按天保存
    seq_no TEXT,
    proto_type TEXT,
    uplink_sec TEXT,
    payload_hex TEXT,
    payload_json TEXT,
    FOREIGN KEY (frame_id) REFERENCES frame_log(id)
);
CREATE INDEX IF NOT EXISTS idx_report_meter_data_ts ON report_meter_data(ts);
CREATE INDEX IF NOT EXISTS idx_report_meter_data_day ON report_meter_data(day);

CREATE TABLE IF NOT EXISTS query_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    afn TEXT NOT NULL,
    fn TEXT NOT NULL,
    mode TEXT,                       -- manual / auto
    total INTEGER,
    item_count INTEGER,
    status TEXT,                     -- running / done / partial / error
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_query_snapshot_fn ON query_snapshot(afn, fn, ts DESC);

CREATE TABLE IF NOT EXISTS query_snapshot_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    seq_index INTEGER NOT NULL,
    frame_id INTEGER,
    addr TEXT,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES query_snapshot(id),
    FOREIGN KEY (frame_id) REFERENCES frame_log(id)
);
CREATE INDEX IF NOT EXISTS idx_qsi_snapshot ON query_snapshot_item(snapshot_id, seq_index);
"""


def default_db_path() -> Path:
    """data/listener_13762.sqlite（frozen 时 exe 同目录）。"""
    import sys
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent.parent
    return root / "data" / "listener_13762.sqlite"


class ListenerStore:
    """1376.2 收发库门面。"""

    def __init__(self, db_path: Path | None = None):
        self._path = Path(db_path) if db_path else default_db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.last_error: str | None = None
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._migrate_daily_columns()
            self._conn.commit()
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)

    def _migrate_daily_columns(self) -> None:
        """旧库补 day 列（按天保存滚动清理）。幂等：列已存在则跳过。"""
        for table in ("frame_log", "report_event", "report_meter_data"):
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "day" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN day TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    def _prune_reports(self) -> None:
        """滚动清理：删除早于保留窗口（最近 5 天）的旧日数据（含 frame_log 证据链）。

        保留窗口 = 今天 + 前 4 天（共 5 天）；第 6 天写入时，第 1 天（最旧一天）
        被清掉。空 day（历史遗留/异常）视为过时一并清理。三张表同按 day 窗口清理，
        业务行与证据帧同步过期。
        """
        cutoff = (date.today() - timedelta(days=_REPORT_RETAIN_DAYS - 1)).isoformat()
        for table in ("report_event", "report_meter_data", "frame_log"):
            self._conn.execute(
                f"DELETE FROM {table} WHERE day = '' OR day < ?", (cutoff,),
            )

    # ------------------------------------------------------------------ 帧
    def add_frame(self, entry: dict) -> Optional[int]:
        """写入一帧证据（journal entry 语义），返回 frame_log.id。"""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO frame_log(session_id, seq, ts, ts_epoch, dir, kind, "
                    "run_id, afn, fn, updown, frame_hex, parsed, day) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry.get("session_id", ""), entry.get("seq", 0),
                        entry.get("ts", datetime.now().isoformat(timespec="milliseconds")),
                        entry.get("ts_epoch", time.time()), entry.get("dir", ""),
                        entry.get("kind"), entry.get("run_id"), entry.get("afn"),
                        entry.get("fn"), entry.get("updown"), entry.get("frame_hex", ""),
                        json.dumps(entry.get("parsed"), ensure_ascii=False, default=str)
                        if entry.get("parsed") else None,
                        self._today(),
                    ),
                )
                self._prune_reports()  # frame_log 证据链同样滚动保留 5 天
                self._conn.commit()
                return cur.lastrowid
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return None

    # ------------------------------------------------------------ 上报事件
    def add_report_event(self, *, frame_id: int | None, afn: str, fn: str,
                         event_type: str, payload: dict) -> Optional[int]:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO report_event(frame_id, ts, day, afn, fn, event_type, payload_json) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (frame_id, datetime.now().isoformat(timespec="milliseconds"),
                     self._today(), afn, fn, event_type,
                     json.dumps(payload, ensure_ascii=False, default=str)),
                )
                self._prune_reports()  # 滚动保留最近 5 天
                self._conn.commit()
                return cur.lastrowid
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return None

    def add_report_meter_data(self, *, frame_id: int | None, payload: dict) -> Optional[int]:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO report_meter_data(frame_id, ts, day, seq_no, proto_type, "
                    "uplink_sec, payload_hex, payload_json) VALUES(?,?,?,?,?,?,?,?)",
                    (frame_id, datetime.now().isoformat(timespec="milliseconds"),
                     self._today(),
                     str(payload.get("从节点序号", "")), str(payload.get("通信协议类型", "")),
                     str(payload.get("当前报文本地通信上行时长", "")),
                     payload.get("报文内容__hex", ""),
                     json.dumps(payload, ensure_ascii=False, default=str)),
                )
                self._prune_reports()  # 滚动保留最近 5 天
                self._conn.commit()
                return cur.lastrowid
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return None

    # ------------------------------------------------------------ 快照
    def open_snapshot(self, *, afn: str, fn: str, mode: str,
                      meta: dict | None = None) -> Optional[int]:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO query_snapshot(ts, afn, fn, mode, status, meta_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="milliseconds"), afn, fn, mode,
                     "running", json.dumps(meta or {}, ensure_ascii=False, default=str)),
                )
                self._conn.commit()
                return cur.lastrowid
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return None

    def add_snapshot_item(self, snapshot_id: int, seq_index: int, addr: str,
                          payload: dict, frame_id: int | None = None) -> Optional[int]:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO query_snapshot_item(snapshot_id, seq_index, frame_id, addr, "
                    "payload_json) VALUES(?,?,?,?,?)",
                    (snapshot_id, seq_index, frame_id, addr,
                     json.dumps(payload, ensure_ascii=False, default=str)),
                )
                self._conn.commit()
                return cur.lastrowid
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return None

    def close_snapshot(self, snapshot_id: int, *, status: str = "done",
                       total: int | None = None, item_count: int | None = None) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE query_snapshot SET status=?, total=COALESCE(?,total), "
                    "item_count=COALESCE(?,item_count) WHERE id=?",
                    (status, total, item_count, snapshot_id),
                )
                self._conn.commit()
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)

    # ------------------------------------------------------------ 查询
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            return []

    def list_snapshots(self, *, afn: str | None = None, fn: str | None = None,
                       limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM query_snapshot"
        where, params = [], []
        if afn:
            where.append("afn=?")
            params.append(afn)
        if fn:
            where.append("fn=?")
            params.append(fn)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return self.query(sql, tuple(params))

    def snapshot_items(self, snapshot_id: int) -> list[dict]:
        return self.query(
            "SELECT * FROM query_snapshot_item WHERE snapshot_id=? ORDER BY seq_index",
            (snapshot_id,),
        )

    def list_report_events(self, *, limit: int = 50) -> list[dict]:
        return self.query("SELECT * FROM report_event ORDER BY id DESC LIMIT ?", (int(limit),))

    def report_days(self) -> list[dict]:
        """上报数据按天统计（供「上报历史」按天分组展示）。"""
        rows = self.query(
            "SELECT day, COUNT(*) AS cnt FROM report_event GROUP BY day ORDER BY day DESC")
        meter = {r["day"]: r["cnt"] for r in self.query(
            "SELECT day, COUNT(*) AS cnt FROM report_meter_data GROUP BY day")}
        for r in rows:
            r["meter"] = meter.get(r["day"], 0)
        return rows

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:  # pragma: no cover
            pass
