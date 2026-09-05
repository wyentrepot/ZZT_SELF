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

# ===== REQS-0026：事件三级分级 + 人话翻译（轻量内联版，完整断言库归 REQS-0025）=====
# 分级/门限出处：蒸馏库 CCO实现逻辑/07-NWK、08-MAC
#（心跳 4 周期离网、通信成功率 98/90%、CSMA 占用 60%/80%、冲突仲裁时序）。
LEVEL_ALARM = "alarm"    # 异常：需定点排查
LEVEL_WATCH = "watch"    # 关注：拓扑/链路变化线索
LEVEL_NORMAL = "normal"  # 常规：心跳/信标/成功入网等，前端默认折叠

# 入网成功结果码：0=成功、0xA=再次关联成功（sta.h ASSOC_RSLT）
_ASSOC_OK_RSLT = (0, 0xA)
# 通信成功率亚健康门限 90%（08-MAC：≥98 健康 / 90-98 亚健康 / <90 故障）
_SUCCESS_RATE_BAD = 90

_EVENT_LEVELS = {
    "leave_ind": LEVEL_ALARM,
    "nid_conflict": LEVEL_ALARM,
    "rf_conflict": LEVEL_ALARM,
    "route_error": LEVEL_ALARM,
    "proxy_change_req": LEVEL_WATCH,
    "proxy_change_cnf": LEVEL_WATCH,
    "coord_frame": LEVEL_WATCH,
}


def classify_level(event: str, fields: dict) -> str:
    """单事件分级（纯函数）：alarm / watch / normal。"""
    if event in _EVENT_LEVELS:
        return _EVENT_LEVELS[event]
    if event == "assoc_cnf":
        # 入网被拒：rslt ∉ {0, 0xA}（07-NWK 关联确认结果码）
        rslt = fields.get("rslt")
        return LEVEL_ALARM if rslt is not None and rslt not in _ASSOC_OK_RSLT else LEVEL_NORMAL
    if event in ("beacon_central", "beacon_proxy", "beacon_discover"):
        # BPCS（信标相位冲突调度）失败 = 帧调度校验异常（08-MAC 冲突仲裁）
        return LEVEL_ALARM if fields.get("bpcs_ok") is False else LEVEL_NORMAL
    if event == "success_rate":
        # 成功率上报：任一子站上行 <90% 记关注（08-MAC 成功率门限）
        entries = fields.get("entries") or []
        for entry in entries:
            up = entry.get("up")
            if isinstance(up, (int, float)) and up < _SUCCESS_RATE_BAD:
                return LEVEL_WATCH
        return LEVEL_NORMAL
    return LEVEL_NORMAL


def _station_label(tei, profile: dict) -> str:
    """TEI → 站点标签：档案命中显示表号/MAC，否则原样 TEI。

    tei 两种形态：fields 里是十进制 int（如 0x35=53），事件行是 hex 文本
    （"035"）——统一转 3 位 hex 后查档案。001 视为 CCO。
    """
    text = f"{tei:03X}" if isinstance(tei, int) else str(tei or "").strip().upper()
    if not text or text in ("000", "0", "001"):
        return "CCO"
    info = profile.get(text)
    if info:
        mac = info.get("mac") or ""
        if info.get("addr_type") == "模块MAC":
            return f"MAC {mac}"
        return f"表 {mac}" if mac else f"TEI {text}"
    return f"TEI {text}"


def _short_mac(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # 全数字视为表地址（BCD 表号），否则按 MAC 展示
    return f"表 {text}" if text.isdigit() else f"MAC {text}"


def _humanize(event: str, fields: dict, profile: dict) -> str:
    """事件 → 人话摘要（TEI 翻译为表号/MAC，异常给处置线索）。"""
    label_src = _station_label(fields.get("sta_tei") or fields.get("osa_tei")
                               or fields.get("proxy_tei"), profile)
    if event == "assoc_cnf":
        rslt = fields.get("rslt")
        station = _short_mac(fields.get("sta_mac")) or label_src
        if rslt in _ASSOC_OK_RSLT:
            proxy = _station_label(fields.get("proxy_tei"), profile)
            again = "（再次关联）" if rslt == 0xA else ""
            return f"{station} 入网成功{again}，代理 {proxy}"
        retry = fields.get("retry_time_ms")
        backoff = f"，退避 {retry / 1000:.0f}s 后重试" if retry else ""
        return f"{station} 入网被拒：{fields.get('rslt_name', '未知原因')}{backoff}"
    if event == "assoc_req":
        station = _short_mac(fields.get("sta_mac"))
        candidates = fields.get("proxy_candidates") or []
        cand = "、".join(
            f"{c['tei']:03X}({c['link_type']})" for c in candidates if isinstance(c, dict))
        return f"{station} 申请入网，候选代理 [{cand}]"
    if event == "leave_ind":
        macs = fields.get("macs") or []
        stations = "、".join(_short_mac(m) for m in macs[:4]) or "站点"
        more = f" 等{fields.get('sta_cnt')}站" if (fields.get("sta_cnt") or 0) > 4 else ""
        delay = fields.get("delay_time_ms")
        delay_text = f"，{delay / 1000:.0f}s 后可重新入网" if delay else ""
        return f"{stations}{more} 离网：{fields.get('reason_name', '未知原因')}{delay_text}"
    if event == "heartbeat":
        active = fields.get("active_cnt")
        return f"心跳：{label_src} 上报 {active if active is not None else '—'} 站活跃"
    if event == "proxy_change_req":
        return (f"{_station_label(fields.get('sta_tei'), profile)} "
                f"申请变更代理（原 {_station_label(fields.get('old_proxy_tei'), profile)}）")
    if event == "proxy_change_cnf":
        return (f"{_station_label(fields.get('sta_tei'), profile)} 变更代理 → "
                f"{_station_label(fields.get('proxy_tei'), profile)}"
                f"{'（失败）' if fields.get('result') not in (0, None) else ''}")
    if event == "nid_conflict":
        return f"检测到 NID 冲突，邻居网络 {fields.get('neighbor_nids') or '—'}"
    if event == "rf_conflict":
        return f"检测到无线信道冲突（信道 {fields.get('chnls') or '—'}）"
    if event == "route_error":
        unreach = fields.get("unreach_teis") or []
        targets = "、".join(_station_label(t, profile) for t in unreach[:4]) or "—"
        return f"路由错误：{targets} 不可达"
    if event == "coord_frame":
        return f"网间协调：带宽宣告 {fields.get('duration_ms')}ms，邻居 NID {fields.get('neighbor_nid', 0):06X}"
    if event == "success_rate":
        return f"成功率上报：{label_src}（{fields.get('sub_sta_cnt', '—')} 子站）"
    return ""  # 其余类型沿用解析层 summary


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
                fields_json TEXT,
                level TEXT
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
        # REQS-0026 迁移：0024 建的老库无 level 列 → 补列并回填存量行
        #（level 索引必须等补列完成后再建，故不放在上面的 executescript 里）
        columns = {row[1] for row in connection.execute("PRAGMA table_info(nwk_events)")}
        if "level" not in columns:
            connection.execute("ALTER TABLE nwk_events ADD COLUMN level TEXT")
            self._backfill_levels(connection)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_nwk_events_level ON nwk_events(level)")

    @staticmethod
    def _backfill_levels(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT frame_id, event, fields_json FROM nwk_events WHERE level IS NULL"
        ).fetchall()
        updates = [
            (classify_level(event, json.loads(fields or "{}")), frame_id)
            for frame_id, event, fields in rows
        ]
        connection.executemany(
            "UPDATE nwk_events SET level = ? WHERE frame_id = ?", updates)

    def _load_tei_profile(self, connection: sqlite3.Connection) -> dict:
        """TEI → MAC/表地址档案（REQS-0026 G2）：实时聚合关联帧自带地址，无独立表。

        关联确认（表70）/关联汇总（表76）自带 sta_mac+TEI；关联请求（表60）补
        地址类型（电能表地址 / 模块MAC）。只扫最近 4000 条关联事件，够建全网档案。
        """
        rows = connection.execute(
            "SELECT event, fields_json FROM nwk_events"
            " WHERE event IN ('assoc_req','assoc_cnf','assoc_gather')"
            " ORDER BY frame_id DESC LIMIT 4000"
        ).fetchall()
        profile: dict = {}
        for event, fields_json in rows:
            try:
                fields = json.loads(fields_json or "{}")
            except ValueError:
                continue
            if event == "assoc_cnf" and fields.get("sta_tei") and fields.get("sta_mac"):
                profile.setdefault(f"{fields['sta_tei']:03X}", {"mac": fields["sta_mac"]})
            elif event == "assoc_gather":
                for sta in fields.get("stas") or []:
                    if isinstance(sta, dict) and sta.get("tei") and sta.get("mac"):
                        profile.setdefault(f"{sta['tei']:03X}", {"mac": sta["mac"]})
            elif event == "assoc_req" and fields.get("sta_mac"):
                addr_type = fields.get("mac_addr_type")
                for info in profile.values():
                    if info.get("mac") == fields["sta_mac"] and addr_type:
                        info["addr_type"] = addr_type
        return profile

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
                    " name, direction, src_tei, dst_tei, summary, fields_json, level)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
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
                classify_level(ev["event"], ev["fields"]),
            ))

    # ---------- 查询 ----------

    def list_events(self, index_id: str = "", start_time: str = "", end_time: str = "",
                    nid: str = "", event: str = "", group: str = "",
                    direction: str = "", query: str = "",
                    limit: int = 200, offset: int = 0,
                    auto_refresh: bool = True,
                    level: str = "", decorate: bool = True) -> dict:
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
        levels = [item.strip() for item in level.split(",")
                  if item.strip() in (LEVEL_ALARM, LEVEL_WATCH, LEVEL_NORMAL)]
        if levels:
            marks = ",".join("?" for _ in levels)
            clauses.append(f"level IN ({marks})")
            params.extend(levels)
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
                f" dst_tei, summary, fields_json, level FROM nwk_events{where}"
                f" ORDER BY frame_id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            profile = self._load_tei_profile(connection) if decorate else {}
        events = []
        for r in rows:
            fields = json.loads(r[9] or "{}")
            row_level = r[10] or classify_level(r[3], fields)
            item = {
                "frame_id": r[0], "log_time": r[1], "nid": r[2], "event": r[3],
                "name": r[4], "direction": r[5], "src_tei": r[6], "dst_tei": r[7],
                "summary": r[8], "fields": fields, "level": row_level,
            }
            if decorate:
                item["human"] = _humanize(r[3], fields, profile) or r[8]
                item["src_label"] = _station_label(r[6], profile)
                item["dst_label"] = _station_label(r[7], profile)
            events.append(item)
        return {"total": total, "events": events, "limit": limit, "offset": offset,
                "groups": EVENT_GROUPS, "refresh": refresh_info}

    def overview(self, index_id: str = "", start_time: str = "", end_time: str = "",
                 nid: str = "", auto_refresh: bool = True) -> dict:
        service = self._log.open_index(index_id) if index_id else self._log
        if auto_refresh:
            self.refresh(index_id)
        events = self.list_events(index_id, start_time, end_time, nid,
                                  limit=100000, auto_refresh=False, decorate=False)
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

    # ---------- REQS-0026：印象结论 digest + 按需粗略解析 brief ----------

    MAX_DIGEST_BUCKETS = 60
    MAX_DIGEST_ALARMS = 6
    MAX_DIGEST_WATCH = 3

    def digest(self, index_id: str = "", start_time: str = "", end_time: str = "",
               nid: str = "", auto_refresh: bool = True) -> dict:
        """印象结论包（≤4KB）：一句话判定 + 异常清单 + 网络概要 + 时间桶计数。

        人与 AI 同源（L1 结论层）；明细走 /api/network/events（L2）、
        单帧走 /api/network/events/{frame_id}/brief（L3），对齐 REQS-0022 分层。
        """
        service = self._log.open_index(index_id) if index_id else self._log
        if auto_refresh:
            self.refresh(index_id)
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
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        level_suffix = (" AND" if clauses else " WHERE") + " level = ?"
        with _connect(service) as connection:
            self._ensure_tables(connection)
            profile = self._load_tei_profile(connection)
            level_counts = dict(connection.execute(
                f"SELECT level, COUNT(*) FROM nwk_events{where} GROUP BY level", params
            ).fetchall())
            total = sum(level_counts.values())
            groups = []
            for row_level, cap in ((LEVEL_ALARM, self.MAX_DIGEST_ALARMS),
                                   (LEVEL_WATCH, self.MAX_DIGEST_WATCH)):
                stats = connection.execute(
                    f"SELECT event, name, COUNT(*), MIN(log_time), MAX(log_time),"
                    f" MAX(frame_id) FROM nwk_events{where}{level_suffix}"
                    f" GROUP BY event ORDER BY 3 DESC LIMIT ?",
                    (*params, row_level, cap),
                ).fetchall()
                samples = {}
                if stats:
                    marks = ",".join("?" for _ in stats)
                    sample_rows = connection.execute(
                        f"SELECT event, fields_json FROM nwk_events"
                        f" WHERE frame_id IN ({marks})",
                        [s[5] for s in stats],
                    ).fetchall()
                    samples = {ev: json.loads(fj or "{}") for ev, fj in sample_rows}
                groups.append([
                    {
                        "type": ev, "name": name, "count": cnt,
                        "first_time": first, "last_time": last,
                        "sample_human": (_humanize(ev, samples.get(ev, {}), profile)
                                         or name)[:64],
                    }
                    for ev, name, cnt, first, last, _ in stats
                ])
            alarms, watch = groups
            beacon_suffix = (" AND" if clauses else " WHERE") + " event = 'beacon_central'"
            beacon = connection.execute(
                f"SELECT fields_json FROM nwk_events{where}{beacon_suffix}"
                f" ORDER BY frame_id DESC LIMIT 1",
                params,
            ).fetchone()
            state = connection.execute(
                "SELECT frames_total, sack_total, sack_fail, icv_fail, truncated,"
                " decode_fail, last_frame_id FROM nwk_scan_state WHERE id = 1"
            ).fetchone()
            max_frame = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM frames"
            ).fetchone()[0]
            # 秒级聚合（log_time 形如 HH:MM:SS.mmm）→ 自适应归并时间桶
            bucket_rows = connection.execute(
                "SELECT CAST(substr(log_time,1,2) AS INTEGER)*3600"
                " + CAST(substr(log_time,4,2) AS INTEGER)*60"
                " + CAST(substr(log_time,7,2) AS INTEGER) AS sec, COUNT(*),"
                " SUM(CASE WHEN level = 'alarm' THEN 1 ELSE 0 END)"
                f" FROM nwk_events{where} GROUP BY sec ORDER BY sec",
                params,
            ).fetchall()
        sec_rows = [(r[0], r[1], r[2] or 0) for r in bucket_rows if r[0] is not None]
        buckets, bucket_seconds = self._time_buckets(
            sec_rows,
            min((r[0] for r in sec_rows), default=0),
            max((r[0] for r in sec_rows), default=0),
        )
        cco_mac = None
        if beacon:
            try:
                cco_mac = json.loads(beacon[0] or "{}").get("cco_mac")
            except ValueError:
                cco_mac = None
        sack_total = state[1] if state else 0
        sack_fail = state[2] if state else 0
        alarm_total = level_counts.get(LEVEL_ALARM, 0)
        watch_total = level_counts.get(LEVEL_WATCH, 0)
        if not total:
            verdict = "当前时间窗内没有组网事件（未建索引或过滤过严）"
        elif alarm_total:
            kinds = "、".join(f"{g['name']}×{g['count']}" for g in alarms[:3])
            verdict = f"发现 {alarm_total} 次异常（{kinds}），建议按时间桶定点排查"
        elif watch_total:
            verdict = f"无异常，{watch_total} 次值得关注事件（代理变更/低成功率等）"
        else:
            verdict = f"组网平稳：共 {total} 条事件，无异常"
        overall = (LEVEL_ALARM if alarm_total
                   else LEVEL_WATCH if watch_total else LEVEL_NORMAL)
        return {
            "verdict": verdict,
            "level": overall,
            "alarm_count": alarm_total,
            "watch_count": watch_total,
            "event_total": total,
            "alarms": alarms,
            "watch": watch,
            "network": {
                "nid": (nid.strip().upper() or None),
                "cco_mac": cco_mac,
                "station_count": len(profile),
            },
            "quality": {
                "frames_total": state[0] if state else 0,
                "sack_total": sack_total,
                "sack_fail": sack_fail,
                "sack_fail_rate": round(sack_fail / sack_total, 4) if sack_total else None,
                "icv_fail": state[3] if state else 0,
                "truncated": state[4] if state else 0,
                "decode_fail": state[5] if state else 0,
            },
            "scan_pending": bool(state and state[6] is not None and state[6] < max_frame),
            "buckets": buckets,
            "bucket_seconds": bucket_seconds,
        }

    @staticmethod
    def _fmt_bucket_time(seconds: int, with_seconds: bool) -> str:
        base = f"{seconds % 86400 // 3600:02d}:{seconds % 3600 // 60:02d}"
        return f"{base}:{seconds % 60:02d}" if with_seconds else base

    @classmethod
    def _time_buckets(cls, rows, min_sec: int, max_sec: int):
        """秒级 (sec, total, alarms) 聚合行 → 自适应粒度桶列表（只含非空桶）。

        粒度：跨度 ≤30min 用 1 分钟；更大取 ceil(跨度/60/60) 分钟，保证 ≤60 桶。
        """
        if not rows:
            return [], 60
        if max_sec < min_sec:  # 跨午夜：末段加一天算跨度
            max_sec += 86400
        span = max(max_sec - min_sec, 0)
        bucket_seconds = 60 if span <= 1800 else max(
            60, -(-span // (60 * cls.MAX_DIGEST_BUCKETS)) * 60)
        merged: dict = {}
        for sec, total, alarms in rows:
            t = sec if sec >= min_sec else sec + 86400
            key = (t - min_sec) // bucket_seconds
            cell = merged.setdefault(key, [0, 0])
            cell[0] += total
            cell[1] += alarms
        with_seconds = bucket_seconds < 60
        start_base = (min_sec // bucket_seconds) * bucket_seconds
        buckets = []
        for key in sorted(merged):
            total, alarms = merged[key]
            b_start = start_base + key * bucket_seconds
            buckets.append({
                "start": cls._fmt_bucket_time(b_start, with_seconds),
                "end": cls._fmt_bucket_time(b_start + bucket_seconds, with_seconds),
                "total": total,
                "alarms": alarms,
            })
        return buckets, bucket_seconds

    def frame_brief(self, frame_id: int, index_id: str = "") -> dict:
        """单帧粗略解析（≤2KB 分层中文摘要）：点击事件时按需调用，不预载全量。"""
        service = self._log.open_index(index_id) if index_id else self._log
        with _connect(service) as connection:
            row = connection.execute(
                "SELECT log_time, raw_hex FROM frames WHERE id = ?", (frame_id,)
            ).fetchone()
            if row is None:
                raise KeyError(frame_id)
            self._ensure_tables(connection)
            profile = self._load_tei_profile(connection)
        log_time, raw_hex = row
        try:
            parsed = decode_frame(raw_hex)
        except Exception as exc:
            raise ValueError(f"帧 #{frame_id} 解析失败：{exc}") from exc

        def value(value_text):
            if isinstance(value_text, bool):
                return "是" if value_text else "否"
            if isinstance(value_text, (dict, list)):
                value_text = json.dumps(value_text, ensure_ascii=False)
            text = str(value_text)
            return text[:64] + "…" if len(text) > 64 else text

        # 常见解析字段的中文名（粗略解析面向排障人员，其余字段保留英文原名）
        FIELD_LABELS = {
            "sta_mac": "站点MAC", "cco_mac": "CCO MAC", "rslt": "结果码",
            "rslt_name": "关联结果", "sta_tei": "站点TEI", "proxy_tei": "代理TEI",
            "old_proxy_tei": "原代理TEI", "retry_time_ms": "退避时长ms",
            "active_cnt": "活跃站点数", "osa_tei": "原发TEI", "sta_cnt": "站点数",
            "sub_sta_cnt": "子站数", "reason_name": "离网原因", "reason": "离网原因码",
            "delay_time_ms": "延迟ms", "neighbor_nids": "邻居NID",
            "unreach_teis": "不可达TEI", "sqn": "序号", "cycle_count": "周期计数",
            "permit_assoc": "允许关联", "networking_stop": "停止组网",
            "bpcs_ok": "BPCS校验", "beacon_period_ms": "信标周期ms",
            "link_type": "链路类型", "sta_layer": "站点层级", "hplc_band": "HPLC频段",
            "mac_addr_type": "地址类型", "terminater_name": "终端类型",
            "module_type": "模块类型", "chnls": "冲突信道", "bitmap_size": "位图字节数",
        }
        layers = [{"title": "GW 封装", "fields": {
            "定界符": parsed.delimiter_name,
            "NID": parsed.nid_hex,
            "帧长": f"{len(raw_hex or '') // 2} B",
        }}]
        if parsed.mac is not None:
            mac_fields = {
                "源TEI": parsed.mac.teis_text,
                "目的TEI": parsed.mac.teid_text,
                "源站点": _station_label(parsed.mac.teis, profile),
                "目的站点": _station_label(parsed.mac.teid, profile),
                "发送类型": parsed.mac.send_type_name,
                "MSDU类型": parsed.mac.msdu_type_name,
                "MSDU序号": parsed.mac.msdu_sqn,
                "路由跳数": f"{parsed.mac.hops}/{parsed.mac.remain_hops}",
                "ICV校验": "失败" if (parsed.msdu and parsed.msdu.icv_ok is False)
                           else "通过" if parsed.msdu else "未校验",
            }
            layers.append({"title": "MAC 头", "fields": mac_fields})
            if parsed.mac.msdu_type == 0 and parsed.mgmt is not None:
                fields = {}
                for key, val in list(parsed.mgmt.fields.items())[:14]:
                    if key == "table_hex":
                        fields["原始表内容"] = value(val)
                        continue
                    fields[FIELD_LABELS.get(key, key)] = value(val)
                layers.append({"title": f"管理消息·{parsed.mgmt.mm_name}",
                               "fields": fields})
            elif parsed.mac.msdu_type == 48:
                layers.append({"title": "业务载荷", "fields": {
                    "载荷长度": f"{parsed.mac.msdu_len} B",
                    "载荷截断": "是（网关截断长帧）" if (parsed.msdu and parsed.msdu.truncated) else "否",
                }})
        elif parsed.fch.delimiter == 2:
            layers.append({"title": "选择确认", "fields": {
                key: value(val) for key, val in list(variable.items())[:8]
            }})
        elif parsed.beacon is not None:
            layers.append({"title": f"信标·{parsed.beacon.bcn_type_name}", "fields": {
                "CCO MAC": parsed.beacon.cco_mac_text,
                "周期计数": parsed.beacon.cycle_count,
                "允许关联": parsed.beacon.permit_assoc,
                "BPCS": "通过" if parsed.beacon.bpcs_ok else "失败",
                "条目数": len(parsed.beacon.items),
                "信标周期ms": (parsed.beacon.schedule or {}).get("beacon_period_ms"),
            }})
        events = [
            {
                "level": classify_level(ev["event"], ev["fields"]),
                "name": ev["name"],
                "human": _humanize(ev["event"], ev["fields"], profile) or ev["summary"],
            }
            for ev in parsed.events
        ]
        return {
            "frame_id": frame_id,
            "log_time": log_time,
            "nid": parsed.nid_hex,
            "layers": layers,
            "events": events,
            "warnings": parsed.warnings[:6],
        }
