"""NwkService 单测：临时索引库 + 真机夹具帧 → 事件落库/总览/信标查询。"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from listener.nwk_service import NwkService  # noqa: E402
from parser_lib.adapters.adapter_dualmac.tests import samples as S  # noqa: E402


class _StubLogService:
    """LogFileService 最小桩：暴露 _connect / _time_range_bound / open_index。"""

    def __init__(self, db_path):
        self.database_path = db_path

    def _connect(self):
        return sqlite3.connect(self.database_path)

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
