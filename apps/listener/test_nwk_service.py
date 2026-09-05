"""NwkService 单测：临时索引库 + 真机夹具帧 → 事件落库/总览/信标查询。"""
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from listener.nwk_service import (  # noqa: E402
    LEVEL_ALARM,
    LEVEL_NORMAL,
    LEVEL_WATCH,
    NwkService,
    classify_level,
)
from parser_lib.adapters.adapter_dualmac.tests import samples as S  # noqa: E402


class _StubLogService:
    """LogFileService 最小桩：_connect 与真实实现同为 @contextmanager 生成器
    （回归防线：2026-09-05 曾因桩用裸连接漏掉 contextmanager 形态缺陷）。"""

    def __init__(self, db_path):
        self.database_path = str(db_path)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _time_range_bound(value, is_end=False):
        if not value:
            return ""
        return f"{value}{'999' if is_end else '000'}" if "." not in value else value

    def open_index(self, index_id):
        return self


@pytest.fixture()
def nwk(tmp_path):
    db = tmp_path / "idx.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE frames (id INTEGER PRIMARY KEY AUTOINCREMENT, sequence TEXT,"
        " log_time TEXT NOT NULL, byte_length INTEGER, raw_hex TEXT NOT NULL,"
        " summary_json TEXT)"
    )
    rows = [
        ("00:00:00.010", S.CENTRAL_BEACON),
        ("00:00:00.020", S.COORD),
        ("00:00:01.000", S.HEARTBEAT),
        ("00:00:02.000", S.ASSOC_CNF),
        ("00:00:03.000", S.PROXY_REQ),
        ("00:00:04.000", S.SACK),
        ("00:00:05.000", S.APP_FRAME),
    ]
    for i, (ts, raw) in enumerate(rows, start=1):
        connection.execute(
            "INSERT INTO frames(id, sequence, log_time, byte_length, raw_hex)"
            " VALUES (?, ?, ?, ?, ?)",
            (i, str(406727 + i), ts, len(raw) // 2, raw),
        )
    connection.commit()
    connection.close()
    return NwkService(_StubLogService(db))


def test_refresh_scans_all(nwk):
    result = nwk.refresh()
    assert result["scanned"] == 7
    assert result["pending"] is False


def test_refresh_incremental(nwk):
    nwk.refresh()
    connection = sqlite3.connect(nwk._log.database_path)
    connection.execute(
        "INSERT INTO frames(sequence, log_time, byte_length, raw_hex) VALUES (?,?,?,?)",
        ("x", "00:00:06.000", 40, S.SACK),
    )
    connection.commit()
    connection.close()
    result = nwk.refresh()
    assert result["scanned"] == 1
    again = nwk.refresh()
    assert again["scanned"] == 0


def test_list_events_filters(nwk):
    data = nwk.list_events()
    kinds = {e["event"] for e in data["events"]}
    assert {"beacon_central", "coord_frame", "heartbeat", "assoc_cnf",
            "proxy_change_req", "app_data"} <= kinds
    assert "sack" not in kinds  # SACK 只进质量统计

    only_hb = nwk.list_events(event="heartbeat")
    assert only_hb["total"] == 1
    ev = only_hb["events"][0]
    assert ev["fields"]["osa_tei"] == 0x035
    assert ev["fields"]["active_cnt"] == 136

    assoc = nwk.list_events(event="assoc_cnf")["events"][0]
    assert assoc["direction"] == "down"
    assert assoc["fields"]["rslt_name"] == "再次关联成功"

    grouped = nwk.list_events(group="信标")
    assert grouped["total"] == 1

    empty = nwk.list_events(nid="123456")
    assert empty["total"] == 0
    # 字节序反转写法（空气字节序 697F94）也能命中
    by_nid = nwk.list_events(nid="697F94")
    assert by_nid["total"] == data["total"]


def test_overview(nwk):
    data = nwk.overview()
    assert data["event_total"] >= 6
    net = data["networks"][0]
    assert net["cco_mac"] == "200000127472"
    assert net["beacon_period_ms"] == 14878
    assert "035" in net["stations"]
    counters = data["link_counters"]
    assert counters["frames_total"] == 7
    assert counters["sack_total"] == 1
    assert counters["app_total"] == 1


def test_list_beacons(nwk):
    data = nwk.list_beacons()
    assert data["total"] == 1
    beacon = data["events"][0]
    assert beacon["fields"]["beacon_period_ms"] == 14878
    assert beacon["fields"]["periods_ms"]["beacon"]["end"] == 3348


def test_scan_survives_bad_frame(nwk):
    nwk.refresh()  # 先扫完夹具帧
    connection = sqlite3.connect(nwk._log.database_path)
    connection.execute(
        "INSERT INTO frames(sequence, log_time, byte_length, raw_hex) VALUES (?,?,?,?)",
        ("bad", "00:00:07.000", 3, "7EFF"),
    )
    connection.commit()
    connection.close()
    result = nwk.refresh()
    assert result["scanned"] == 1
    state = nwk.overview()["link_counters"]
    assert state["decode_fail"] == 1


# ===== REQS-0026：分级 / 翻译 / digest / brief =====

def _inject_alarm(service, frame_id=900, log_time="00:01:30.500"):
    """真机夹具没有被拒关联帧：注入一条 alarm 级事件行模拟（rslt=1 不在白名单）。"""
    connection = sqlite3.connect(service._log.database_path)
    connection.execute(
        "INSERT OR REPLACE INTO nwk_events(frame_id, log_time, nid, event, name,"
        " direction, src_tei, dst_tei, summary, fields_json, level)"
        " VALUES (?, ?, '947F69', 'assoc_cnf', '关联确认', 'down', '001', '0D8',"
        " '确认 101 结果[不在白名单]', ?, 'alarm')",
        (frame_id, log_time,
         json.dumps({"sta_mac": "300009049239", "rslt": 1,
                     "rslt_name": "不在白名单", "sta_tei": 257,
                     "proxy_tei": 216, "retry_time_ms": 600000})),
    )
    connection.commit()
    connection.close()


def test_classify_levels():
    # 异常：入网被拒 rslt ∉ {0, 0xA}（07-NWK 关联确认结果码）
    assert classify_level("assoc_cnf", {"rslt": 1}) == LEVEL_ALARM
    assert classify_level("assoc_cnf", {"rslt": 0xA}) == LEVEL_NORMAL
    assert classify_level("assoc_cnf", {"rslt": 0}) == LEVEL_NORMAL
    for kind in ("leave_ind", "nid_conflict", "rf_conflict", "route_error"):
        assert classify_level(kind, {}) == LEVEL_ALARM
    # BPCS 失败的信标是异常（08-MAC 冲突仲裁）
    assert classify_level("beacon_central", {"bpcs_ok": False}) == LEVEL_ALARM
    assert classify_level("beacon_central", {"bpcs_ok": True}) == LEVEL_NORMAL
    # 关注：代理变更 / 网间协调 / 低成功率（<90%，08-MAC 成功率门限）
    assert classify_level("proxy_change_req", {}) == LEVEL_WATCH
    assert classify_level("coord_frame", {}) == LEVEL_WATCH
    assert classify_level("success_rate",
                          {"entries": [{"tei": 5, "up": 85}]}) == LEVEL_WATCH
    assert classify_level("success_rate",
                          {"entries": [{"tei": 5, "up": 95}]}) == LEVEL_NORMAL
    # 常规：心跳等
    assert classify_level("heartbeat", {}) == LEVEL_NORMAL


def test_list_events_decorated(nwk):
    nwk.refresh()
    _inject_alarm(nwk)
    data = nwk.list_events(level="alarm")
    assert data["total"] == 1
    event = data["events"][0]
    assert event["level"] == LEVEL_ALARM
    # 人话摘要：TEI → 表号 + 处置线索（退避秒数）
    assert event["human"] == "表 300009049239 入网被拒：不在白名单，退避 600s 后重试"

    heartbeat = nwk.list_events(event="heartbeat")["events"][0]
    assert heartbeat["level"] == LEVEL_NORMAL
    assert "136 站活跃" in heartbeat["human"]

    assoc = nwk.list_events(event="assoc_cnf", level="")["events"]
    levels = {e["level"] for e in assoc}
    assert levels == {LEVEL_ALARM, LEVEL_NORMAL}  # 注入的被拒 + 夹具的再次关联成功


def test_events_level_default_keeps_all(nwk):
    """默认不带 level 过滤时全量返回（降噪是前端行为）。"""
    nwk.refresh()
    data = nwk.list_events()
    assert data["total"] >= 6
    assert all(e.get("level") for e in data["events"])


def test_digest_without_alarm(nwk):
    data = nwk.digest()
    # 夹具无异常：coord_frame（网间协调）属关注级
    assert data["level"] == LEVEL_WATCH
    assert data["alarm_count"] == 0
    assert data["alarms"] == []
    assert data["watch_count"] >= 1
    assert "无异常" in data["verdict"]
    assert data["buckets"] and data["bucket_seconds"] == 60
    assert data["network"]["cco_mac"] == "200000127472"
    assert len(json.dumps(data, ensure_ascii=False)) <= 4096  # ≤4KB 口径


def test_digest_with_alarm(nwk):
    nwk.refresh()
    _inject_alarm(nwk)
    data = nwk.digest()
    assert data["level"] == LEVEL_ALARM
    assert data["alarm_count"] == 1
    alarm = data["alarms"][0]
    assert alarm["type"] == "assoc_cnf"
    assert alarm["count"] == 1
    assert "入网被拒" in alarm["sample_human"]
    # 异常落在 00:01 分钟桶 → 该桶标红（alarms>0），其余桶不误报
    alarm_buckets = [b for b in data["buckets"] if b["alarms"]]
    assert len(alarm_buckets) == 1
    assert alarm_buckets[0]["start"] == "00:01"
    assert "发现 1 次异常" in data["verdict"]


def test_time_buckets_adaptive():
    # 跨度 ≤30min → 1 分钟粒度
    rows = [(0, 5, 1), (65, 3, 0), (125, 2, 2)]
    buckets, seconds = NwkService._time_buckets(rows, 0, 125)
    assert seconds == 60
    assert [(b["start"], b["total"], b["alarms"]) for b in buckets] == [
        ("00:00", 5, 1), ("00:01", 3, 0), ("00:02", 2, 2)]
    # 大跨度（24h，每 10 分钟一个采样点）→ 归并到 ≤60 桶，粒度取整分钟
    big = [(sec, 10, 2) for sec in range(0, 24 * 3600, 600)]
    buckets, seconds = NwkService._time_buckets(big, 0, 24 * 3600 - 600)
    assert len(buckets) <= NwkService.MAX_DIGEST_BUCKETS
    assert seconds >= 60
    assert sum(b["total"] for b in buckets) == sum(r[1] for r in big)
    # 跨午夜：max < min 按加一天算跨度
    rows = [(86340, 1, 0), (30, 1, 1)]
    buckets, _ = NwkService._time_buckets(rows, 86340, 30)
    assert len(buckets) == 2


def test_frame_brief_layers(nwk):
    nwk.refresh()
    brief = nwk.frame_brief(4)  # ASSOC_CNF 夹具帧
    assert brief["frame_id"] == 4
    titles = [layer["title"] for layer in brief["layers"]]
    assert any("MAC 头" in t for t in titles)
    assert any("管理消息" in t for t in titles)
    mgmt = next(l for l in brief["layers"] if l["title"].startswith("管理消息"))
    assert mgmt["fields"]["站点MAC"] == "300009049238"
    assert mgmt["fields"]["关联结果"] == "再次关联成功"
    assert brief["events"] and brief["events"][0]["level"] == LEVEL_NORMAL
    # ≤2KB 口径
    assert len(json.dumps(brief, ensure_ascii=False)) <= 2048


def test_frame_brief_beacon_and_missing(nwk):
    nwk.refresh()
    brief = nwk.frame_brief(1)  # 中央信标
    titles = [layer["title"] for layer in brief["layers"]]
    assert any("信标" in t for t in titles)
    with pytest.raises(KeyError):
        nwk.frame_brief(999)


def test_ensure_tables_backfills_levels(tmp_path):
    """0024 老库（无 level 列）打开时补列并回填，不丢历史事件分级。"""
    db = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE frames (id INTEGER PRIMARY KEY AUTOINCREMENT, sequence TEXT,"
        " log_time TEXT NOT NULL, byte_length INTEGER, raw_hex TEXT NOT NULL,"
        " summary_json TEXT)"
    )
    connection.execute(
        "INSERT INTO frames(id, sequence, log_time, byte_length, raw_hex)"
        " VALUES (1, '1', '00:00:01.000', 40, ?)", (S.HEARTBEAT,))
    # 0024 老结构：nwk_events 无 level 列
    connection.execute(
        "CREATE TABLE nwk_events (frame_id INTEGER PRIMARY KEY, log_time TEXT NOT NULL,"
        " nid TEXT NOT NULL, event TEXT NOT NULL, name TEXT NOT NULL,"
        " direction TEXT NOT NULL, src_tei TEXT, dst_tei TEXT, summary TEXT,"
        " fields_json TEXT)")
    connection.execute(
        "INSERT INTO nwk_events(frame_id, log_time, nid, event, name, direction,"
        " src_tei, dst_tei, summary, fields_json)"
        " VALUES (1, '00:00:01.000', '947F69', 'leave_ind', '离网指示', 'down',"
        " '001', '035', '离网 1 站', '{}')")
    connection.commit()
    connection.close()

    service = NwkService(_StubLogService(db))
    data = service.list_events(auto_refresh=False)
    assert data["events"][0]["level"] == LEVEL_ALARM  # leave_ind 回填为异常
