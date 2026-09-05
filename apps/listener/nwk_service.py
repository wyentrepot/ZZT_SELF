"""组网观测服务（REQS-0024）：基于 adapter_dualmac 的组网事件流/网络总览。

数据路径：
  - 帧从 frames 表按 id 增量扫描（log_time + raw_hex），经
    adapter_dualmac.decode_frame 解析为组网事件，落库 nwk_events 表
    （frame_id 主键，幂等重扫）；
  - 链路质量统计（SACK 成败/ICV 失败/截断/帧型分布）随扫描累计进
    nwk_scan_state 计数器，不落事件行（SACK 量大，聚合口径足够）；
  - 查询走 SQL 过滤（时间窗/NID/事件类型/方向），扫描未完成时返回 pending。

口径约定：
  - NID 以 24bit 小端整数的十六进制展示（如 947F69，与 frames.nid 物化列
    同源）；查询过滤同时匹配字节序反转写法（697F94）。
  - direction: down=TEIs 为 CCO(001)；up=TEId 为 CCO；mesh=中继段。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

_SYS_ROOT = str(Path(__file__).resolve().parents[2])
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from parser_lib.adapters.adapter_dualmac import decode_frame  # noqa: E402
from parser_lib.adapters.adapter_dualmac.events import EVENT_NAMES  # noqa: E402

EVENT_GROUPS = {
    "组网": ["assoc_req", "assoc_cnf", "assoc_gather", "proxy_change_req",
             "proxy_change_cnf", "leave_ind"],
    "维护": ["heartbeat", "discover_list", "success_rate"],
    "冲突": ["nid_conflict", "rf_conflict", "coord_frame"],
    "路由": ["route_request", "route_reply", "route_error", "route_ack"],
    "信标": ["beacon_central", "beacon_proxy", "beacon_discover"],
    "业务": ["app_data"],
}


@contextmanager
def _connect(service):
    """统一连接入口：LogFileService._connect 是 @contextmanager 生成器（yield 连接，
    退出时 commit/close），测试桩可能直接返回 sqlite3.Connection——两种形态都支持。"""
    obj = service._connect()
    if isinstance(obj, sqlite3.Connection):
        try:
            yield obj
        finally:
            obj.close()
    else:
        with obj as connection:
            yield connection

_BEACON_EVENTS = ("beacon_central", "beacon_proxy", "beacon_discover")


def _reverse_hex(value: str) -> str:
    raw = value.strip().upper()
    if len(raw) % 2:
        raw = "0" + raw
    try:
        return bytes.fromhex(raw)[::-1].hex().upper()
    except ValueError:
        return raw


class NwkService:
    """组网观测：增量扫描 frames → nwk_events，提供事件/总览/信标查询。"""

    MAX_SCAN_PER_CALL = 20000

    def __init__(self, log_service):
        self._log = log_service
        self._lock = threading.Lock()

    # ---------- 表结构 ----------

    def _ensure_tables(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS nwk_events (
                frame_id INTEGER PRIMARY KEY,
                log_time TEXT NOT NULL,
                nid TEXT NOT NULL,
                event TEXT NOT NULL,
                name TEXT NOT NULL,
                direction TEXT NOT NULL,
                src_tei TEXT,
                dst_tei TEXT,
                summary TEXT,
                fields_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_nwk_events_time ON nwk_events(log_time);
            CREATE INDEX IF NOT EXISTS idx_nwk_events_event ON nwk_events(event);
            CREATE TABLE IF NOT EXISTS nwk_scan_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_frame_id INTEGER NOT NULL DEFAULT 0,
                frames_total INTEGER NOT NULL DEFAULT 0,
                mgmt_total INTEGER NOT NULL DEFAULT 0,
                app_total INTEGER NOT NULL DEFAULT 0,
                beacon_total INTEGER NOT NULL DEFAULT 0,
                sack_total INTEGER NOT NULL DEFAULT 0,
                sack_fail INTEGER NOT NULL DEFAULT 0,
                coord_total INTEGER NOT NULL DEFAULT 0,
                icv_fail INTEGER NOT NULL DEFAULT 0,
                truncated INTEGER NOT NULL DEFAULT 0,
                decode_fail INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            );
            """
        )

    # ---------- 增量扫描 ----------

    def refresh(self, index_id: str = "", max_frames: Optional[int] = None) -> dict:
        """增量扫描新帧并落事件；返回进度（pending=True 表示还有存量未扫完）。"""
        service = self._log.open_index(index_id) if index_id else self._log
        budget = max_frames or self.MAX_SCAN_PER_CALL
        with self._lock:
            with _connect(service) as connection:
                self._ensure_tables(connection)
                row = connection.execute(
                    "SELECT last_frame_id FROM nwk_scan_state WHERE id = 1"
                ).fetchone()
                last_id = row[0] if row else 0
                max_id = connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM frames"
                ).fetchone()[0]
                scanned = 0
                counters = self._empty_counters()
                insert_sql = (
                    "INSERT OR REPLACE INTO nwk_events(frame_id, log_time, nid, event,"
                    " name, direction, src_tei, dst_tei, summary, fields_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)"
                )
                batch = connection.execute(
                    "SELECT id, log_time, raw_hex FROM frames"
                    " WHERE id > ? ORDER BY id LIMIT ?",
                    (last_id, budget),
                ).fetchall()
                last_frame = last_id
                for frame_id, log_time, raw_hex in batch:
                    last_frame = frame_id
                    self._consume(connection, insert_sql, frame_id,
                                  log_time or "", raw_hex or "", counters)
                    scanned += 1
                connection.execute(
                    "INSERT INTO nwk_scan_state(id, last_frame_id, frames_total,"
                    " mgmt_total, app_total, beacon_total, sack_total, sack_fail,"
                    " coord_total, icv_fail, truncated, decode_fail, updated_at)"
                    " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET last_frame_id=excluded.last_frame_id,"
                    " frames_total=frames_total+excluded.frames_total,"
                    " mgmt_total=mgmt_total+excluded.mgmt_total,"
                    " app_total=app_total+excluded.app_total,"
                    " beacon_total=beacon_total+excluded.beacon_total,"
                    " sack_total=sack_total+excluded.sack_total,"
                    " sack_fail=sack_fail+excluded.sack_fail,"
                    " coord_total=coord_total+excluded.coord_total,"
                    " icv_fail=icv_fail+excluded.icv_fail,"
                    " truncated=truncated+excluded.truncated,"
                    " decode_fail=decode_fail+excluded.decode_fail,"
                    " updated_at=excluded.updated_at",
                    (
                        last_frame, counters["frames"], counters["mgmt"],
                        counters["app"], counters["beacon"], counters["sack"],
                        counters["sack_fail"], counters["coord"], counters["icv_fail"],
                        counters["truncated"], counters["decode_fail"],
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                connection.commit()
                pending = last_frame < max_id
        return {"scanned": scanned, "last_frame_id": last_frame, "pending": pending}

    @staticmethod
    def _empty_counters() -> dict:
        return {"frames": 0, "mgmt": 0, "app": 0, "beacon": 0, "sack": 0,
                "sack_fail": 0, "coord": 0, "icv_fail": 0, "truncated": 0,
                "decode_fail": 0}

    def _consume(self, connection, insert_sql, frame_id: int, log_time: str,
                 raw_hex: str, counters: dict) -> None:
        try:
            parsed = decode_frame(raw_hex)
        except Exception:
            counters["frames"] += 1
            counters["decode_fail"] += 1
            return
        counters["frames"] += 1
        fch = parsed.fch
        nid_hex = fch.nid_hex
        if fch.delimiter == 2:  # 选择确认：只计质量统计，不落事件行
            counters["sack"] += 1
            if fch.variable.get("recv_result"):
                counters["sack_fail"] += 1
            return
        if fch.delimiter == 0 and parsed.beacon is not None:
            counters["beacon"] += 1
        elif fch.delimiter == 3:
            counters["coord"] += 1
        if parsed.mac is not None:
            if parsed.mac.msdu_type == 0:
                counters["mgmt"] += 1
            elif parsed.mac.msdu_type == 48:
                counters["app"] += 1
        if parsed.msdu is not None and parsed.msdu.icv_ok is False:
            counters["icv_fail"] += 1
        if parsed.msdu is not None and parsed.msdu.truncated:
            counters["truncated"] += 1
        for ev in parsed.events:
            connection.execute(insert_sql, (
                frame_id, log_time, nid_hex, ev["event"], ev["name"],
                ev["direction"], ev["src_tei"], ev["dst_tei"], ev["summary"],
                json.dumps(ev["fields"], ensure_ascii=False),
            ))

    # ---------- 查询 ----------

    def list_events(self, index_id: str = "", start_time: str = "", end_time: str = "",
                    nid: str = "", event: str = "", group: str = "",
                    direction: str = "", query: str = "",
                    limit: int = 200, offset: int = 0,
                    auto_refresh: bool = True) -> dict:
        service = self._log.open_index(index_id) if index_id else self._log
        refresh_info = None
        if auto_refresh:
            refresh_info = self.refresh(index_id)
        clauses, params = [], []
        start_bound = service._time_range_bound(start_time)
        end_bound = service._time_range_bound(end_time, is_end=True)
        if start_bound:
            clauses.append("log_time >= ?")
            params.append(start_bound)
        if end_bound:
            clauses.append("log_time <= ?")
            params.append(end_bound)
        if nid.strip():
            clauses.append("nid IN (?, ?)")
            params.extend([nid.strip().upper(), _reverse_hex(nid)])
        if event.strip():
            clauses.append("event = ?")
            params.append(event.strip())
        elif group.strip() and group in EVENT_GROUPS:
            marks = ",".join("?" for _ in EVENT_GROUPS[group])
            clauses.append(f"event IN ({marks})")
            params.extend(EVENT_GROUPS[group])
        if direction.strip() in ("up", "down", "mesh"):
            clauses.append("direction = ?")
            params.append(direction.strip())
        if query.strip():
            clauses.append("(summary LIKE ? OR src_tei LIKE ? OR dst_tei LIKE ?)")
            wildcard = f"%{query.strip()}%"
            params.extend([wildcard, wildcard, wildcard])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with _connect(service) as connection:
            self._ensure_tables(connection)
            total = connection.execute(
                f"SELECT COUNT(*) FROM nwk_events{where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT frame_id, log_time, nid, event, name, direction, src_tei,"
                f" dst_tei, summary, fields_json FROM nwk_events{where}"
                f" ORDER BY frame_id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        events = [
            {
                "frame_id": r[0], "log_time": r[1], "nid": r[2], "event": r[3],
                "name": r[4], "direction": r[5], "src_tei": r[6], "dst_tei": r[7],
                "summary": r[8], "fields": json.loads(r[9] or "{}"),
            }
            for r in rows
        ]
        return {"total": total, "events": events, "limit": limit, "offset": offset,
                "groups": EVENT_GROUPS, "refresh": refresh_info}

    def overview(self, index_id: str = "", start_time: str = "", end_time: str = "",
                 nid: str = "", auto_refresh: bool = True) -> dict:
        service = self._log.open_index(index_id) if index_id else self._log
        if auto_refresh:
            self.refresh(index_id)
        events = self.list_events(index_id, start_time, end_time, nid,
                                  limit=100000, auto_refresh=False)
        rows = events["events"]
        networks: dict = {}
        type_counts: dict = {}
        for ev in rows:
            key = ev["nid"]
            net = networks.setdefault(key, {
                "nid": key, "cco_mac": None, "beacon_period_ms": None,
                "stations": set(), "event_count": 0,
            })
            net["event_count"] += 1
            type_counts[ev["event"]] = type_counts.get(ev["event"], 0) + 1
            fields = ev.get("fields") or {}
            if ev["event"] == "beacon_central":
                if fields.get("cco_mac"):
                    net["cco_mac"] = fields["cco_mac"]
                if fields.get("beacon_period_ms"):
                    net["beacon_period_ms"] = fields["beacon_period_ms"]
            for tei in (ev.get("src_tei"), ev.get("dst_tei")):
                if tei and tei not in ("001", "000"):
                    net["stations"].add(tei)
        for net in networks.values():
            net["stations"] = sorted(net["stations"])
            net["station_count"] = len(net["stations"])
        with _connect(service) as connection:
            self._ensure_tables(connection)
            state = connection.execute(
                "SELECT frames_total, mgmt_total, app_total, beacon_total,"
                " sack_total, sack_fail, coord_total, icv_fail, truncated,"
                " decode_fail, updated_at, last_frame_id FROM nwk_scan_state WHERE id = 1"
            ).fetchone()
        counters = {}
        if state:
            keys = ("frames_total", "mgmt_total", "app_total", "beacon_total",
                    "sack_total", "sack_fail", "coord_total", "icv_fail",
                    "truncated", "decode_fail")
            counters = dict(zip(keys, state[:10]))
            counters["updated_at"] = state[10]
            counters["last_frame_id"] = state[11]
            if counters["sack_total"]:
                counters["sack_fail_rate"] = round(
                    counters["sack_fail"] / counters["sack_total"], 4)
        return {
            "networks": sorted(networks.values(), key=lambda n: n["nid"]),
            "event_type_counts": dict(sorted(type_counts.items(), key=lambda kv: -kv[1])),
            "link_counters": counters,
            "event_total": events["total"],
            "groups": EVENT_GROUPS,
            "event_names": EVENT_NAMES,
        }

    def list_beacons(self, index_id: str = "", start_time: str = "", end_time: str = "",
                     nid: str = "", bcn_type: str = "beacon_central",
                     limit: int = 50) -> dict:
        """最近的信标明细（含时隙重建），默认取中央信标。"""
        return self.list_events(index_id, start_time, end_time, nid,
                                event=bcn_type, limit=limit)
