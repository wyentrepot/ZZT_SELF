"""通信流追踪（需求 0009）G2 回放引擎测试。

fixture 索引库 + 假帧（FakeParser 提供 DLL 摘要），绝不打开真实 COM、
绝不写入 runtime 数据（0007 红线）。用例覆盖 flow/round/campaign 三粒度、
广播轮缺席/否认/正常三分类、重传反证、0x0020 显式确认、坏帧口径与特征校验。
"""
import hashlib
import json
from pathlib import Path

import pytest

from listener.log_service import LogFileService
from listener.trace_service import FeatureError, TraceService


# ---------------------------------------------------------------------------
# 假帧构造
# ---------------------------------------------------------------------------

def meter_app_raw(seq, proto_type, data, *, timeout=0x28, option=0x00, config=0,
                  up=False, resp_bitmap=None):
    header_len = 8
    b0 = 1 | ((header_len & 0x03) << 6)
    b2 = (proto_type & 0x0F) | (((len(data) >> 8) & 0x0F) << 4)
    b3 = len(data) & 0xFF
    if up:
        bitmap = resp_bitmap if resp_bitmap is not None else 0
        head = bytes([b0, (header_len >> 2) & 0x0F, b2, b3,
                      seq & 0xFF, (seq >> 8) & 0xFF, bitmap & 0xFF, (bitmap >> 8) & 0xFF])
    else:
        head = bytes([b0, ((header_len >> 2) & 0x0F) | ((config & 0x0F) << 4), b2, b3,
                      seq & 0xFF, (seq >> 8) & 0xFF, timeout, option])
    return bytes([0x11, 0x00, 0x03, 0x00]) + head + data


def frame645(addr_bcd_le, ctrl=0x11, data=b""):
    body = bytes([0x68]) + addr_bcd_le + bytes([ctrl, len(data)]) + data
    return body + bytes([sum(body[1:]) & 0xFF, 0x16])


def app20(seq, *, down=True, confirm=True):
    b1 = ((0 if down else 1) << 4) | ((1 if confirm else 0) << 5)
    return bytes([0x11, 0x20, 0x00, 0x00, 0x01, b1, seq & 0xFF, (seq >> 8) & 0xFF])


def app08(seq, meter_addr_bcd_le, *, up=True):
    b1 = (1 << 4) if up else 0
    return bytes([0x11, 0x08, 0x00, 0x00, 0x01, b1, 0x00, 0x00,
                  seq & 0xFF, (seq >> 8) & 0xFF]) + meter_addr_bcd_le


def summary0003(app_raw, *, src="001", dst="087", frm="终端主动并发抄表"):
    return {
        "FrmType": frm, "SRC": src, "DST": dst,
        "ORI_S": src, "FINL_D": dst,
        "APP_PORT": "11", "APP_ID": "0003", "APP_RAW": app_raw.hex().upper(),
    }


def summary_ack(dst="001"):
    return {"FrmType": "ACK", "SRC": None, "DST": dst,
            "ORI_S": None, "FINL_D": None}


def ack_raw_hex(peer: str) -> str:
    """构造 ACK 原始帧：[27..28] 打包被确认 STA 端 TEI（DESIGN §10.1）。"""
    peer_val = int(peer, 16)
    payload = bytearray(46)
    payload[0] = 0x7E
    payload[27] = 0x10 | ((peer_val >> 8) & 0x0F)
    payload[28] = peer_val & 0xFF
    payload[-1] = 0x7E
    return " ".join(f"{b:02X}" for b in payload)


def summary0020(app_raw, *, src="001", dst="087"):
    return {
        "FrmType": "确认/否认", "SRC": src, "DST": dst,
        "ORI_S": src, "FINL_D": dst,
        "APP_PORT": "11", "APP_ID": "0020", "APP_RAW": app_raw.hex().upper(),
    }


def summary0008(app_raw, *, src="035", dst="001"):
    return {
        "FrmType": "事件上报", "SRC": src, "DST": dst,
        "ORI_S": src, "FINL_D": dst,
        "APP_PORT": "11", "APP_ID": "0008", "APP_RAW": app_raw.hex().upper(),
    }


class FakeParser:
    """以 fixture 摘要回放 parse_summary（log_service 物化路径全真实执行）。"""

    def __init__(self, summaries_by_hex):
        self._by_hex = {k.replace(" ", "").upper(): v for k, v in summaries_by_hex.items()}

    def parse_summary(self, value):
        key = value.replace(" ", "").upper()
        if key not in self._by_hex:
            raise ValueError("未登记的 fixture 帧")
        return {"simple": self._by_hex[key]}

    def parse(self, value):
        return {"parse_error": "fixture 不支持完整解析", "simple": {}, "full": {}}


def build_index(tmp_path, frames):
    """frames: [(log_time, raw_hex, summary_dict)] → 物化齐备的索引库。"""
    parser = FakeParser({raw: summary for _, raw, summary in frames})
    service = LogFileService(parser=parser, database_path=tmp_path / "idx.sqlite3")
    records = [(str(i + 1), t, raw) for i, (t, raw, _) in enumerate(frames)]
    service.append_frames(records)
    assert service.trace_ready()
    return service


ADDR_A = bytes.fromhex("010000000000")   # 表地址 000000000001
ADDR_B = bytes.fromhex("020000000000")
ADDR_C = bytes.fromhex("030000000000")
ADDR_D = bytes.fromhex("040000000000")
DISPLAY_A = "000000000001"
DISPLAY_B = "000000000002"
DISPLAY_C = "000000000003"
DISPLAY_D = "000000000004"


def down645(seq, addrs, *, t="10:00:00.000", dst="087", config=0):
    data = b"".join(frame645(a) for a in addrs)
    raw = meter_app_raw(seq, 2, data, config=config)
    return t, raw.hex().upper(), summary0003(raw, dst=dst)


def up645(seq, addr, *, denied=False, t="10:00:01.000", src="087"):
    raw = meter_app_raw(seq, 2, frame645(addr, ctrl=0xC1 if denied else 0x11),
                        up=True, resp_bitmap=1)
    return t, raw.hex().upper(), summary0003(raw, src=src, dst="001")


# ---------------------------------------------------------------------------
# flow 粒度
# ---------------------------------------------------------------------------

def test_flow_full_chain_with_ack(tmp_path):
    frames = [
        down645(0x1EC2, [ADDR_A, ADDR_B]),
        ("10:00:00.100", ack_raw_hex("087"), summary_ack()),
        up645(0x1EC2, ADDR_A, t="10:00:01.000"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "flow",
        "feature": {"app_id": "0003", "msg_seq": "1EC2"},
    })
    flow = report["flow"]
    assert flow["stage"] == "confirmed"
    assert flow["s3"]["evidence_kind"] == "no_retransmit_inference"
    assert flow["ack"]["frame_id"] == 2
    assert flow["response"]["frame_id"] == 3
    assert flow["response"]["latency_ms"] == 1000
    assert flow["via_tei"] == "087"
    assert flow["sent"]["retries"] == 0
    assert flow["targets"] == [DISPLAY_A, DISPLAY_B]
    assert report["summary"]["full_chain"] == 1


def test_flow_no_ack_stuck_at_sent(tmp_path):
    service = build_index(tmp_path, [down645(0x1EC3, [ADDR_A])])
    report = TraceService(service).run_replay({
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC3"},
    })
    assert report["flow"]["stage"] == "sent"
    assert report["summary"]["no_ack"] == 1


def test_flow_acked_no_response(tmp_path):
    """有 ACK 无响应 = STA 业务层卡滞（DESIGN §1）。"""
    frames = [
        down645(0x1EC4, [ADDR_A]),
        ("10:00:00.050", ack_raw_hex("087"), summary_ack()),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC4"},
    })
    assert report["flow"]["stage"] == "acked"
    assert report["summary"]["no_response"] == 1


def test_flow_retransmission_breaks_s3_inference(tmp_path):
    """响应之后同序号下行重发 → CCO 接收侧异常（S3 反证失败）。"""
    frames = [
        down645(0x1EC5, [ADDR_A], t="10:00:00.000"),
        up645(0x1EC5, ADDR_A, t="10:00:01.000"),
        down645(0x1EC5, [ADDR_A], t="10:00:02.000"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC5"},
    })
    flow = report["flow"]
    assert flow["stage"] == "responded"
    assert flow["s3"]["verdict"] == "not_confirmed"
    assert flow["s3"]["evidence_kind"] == "retransmitted"
    assert flow["sent"]["retries"] == 1
    assert flow["retransmissions"][0]["interval_ms"] == 2000
    assert report["summary"]["no_confirm"] == 1


def test_flow_retransmission_before_response_is_normal_retry(tmp_path):
    """响应之前的重发 = 正常未应答重试（重发不增序号），不影响确认推断。"""
    frames = [
        down645(0x1EC6, [ADDR_A], t="10:00:00.000"),
        down645(0x1EC6, [ADDR_A], t="10:00:00.500"),
        up645(0x1EC6, ADDR_A, t="10:00:01.000"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC6"},
    })
    flow = report["flow"]
    assert flow["stage"] == "confirmed"
    assert flow["sent"]["retries"] == 1


def test_flow_645_denied_response(tmp_path):
    frames = [
        down645(0x1EC7, [ADDR_A]),
        up645(0x1EC7, ADDR_A, denied=True),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC7"},
    })
    assert report["flow"]["stage"] == "denied"
    assert report["summary"]["denied"] == 1


def test_round_time_range_window(tmp_path):
    frames = [
        down645(0x1EC8, [ADDR_A], t="10:00:00.000"),
        up645(0x1EC8, ADDR_A, t="10:00:01.000"),
        down645(0x1EC9, [ADDR_A], t="10:30:00.000"),
        up645(0x1EC9, ADDR_A, t="10:30:01.000"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round",
        "window": {"mode": "time_range", "start_time": "10:00:00", "end_time": "10:05:00"},
        "feature": {"app_id": "0003"},
    })
    seqs = [f["msg_seq"] for r in report["rounds"] for f in r["flows"]]
    assert seqs == ["0x1EC8"]


def test_flow_requires_msg_seq():
    with pytest.raises(FeatureError):
        TraceService(None).run_replay({"scope": "flow", "feature": {"app_id": "0003"}})


# ---------------------------------------------------------------------------
# round 粒度：广播轮三分类
# ---------------------------------------------------------------------------

def test_round_missing_denied_ok_classification(tmp_path):
    """一轮并发抄表：A 正常、B 否认、C 正常、D 缺席（§3 验收）。"""
    frames = [
        down645(0x2001, [ADDR_A, ADDR_B], t="10:00:00.000", dst="087"),
        down645(0x2002, [ADDR_C], t="10:00:01.000", dst="0D8"),
        down645(0x2003, [ADDR_D], t="10:00:02.000", dst="140"),
        ("10:00:00.100", ack_raw_hex("087"), summary_ack()),
        ("10:00:01.100", ack_raw_hex("0D8"), summary_ack()),
        up645(0x2001, ADDR_A, t="10:00:02.000", src="087"),
        up645(0x2001, ADDR_B, denied=True, t="10:00:03.000", src="087"),
        up645(0x2002, ADDR_C, t="10:00:04.000", src="0D8"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003"},
    })
    assert report["summary"]["rounds"] == 1
    assert report["summary"]["meters"] == 4
    rd = report["rounds"][0]
    by_addr = {m["meter_addr"]: m for m in rd["meter_table"]}
    assert by_addr[DISPLAY_A]["status"] == "ok"
    assert by_addr[DISPLAY_B]["status"] == "denied"
    assert by_addr[DISPLAY_C]["status"] == "ok"
    assert by_addr[DISPLAY_D]["status"] == "missing"
    assert rd["meters"] == {"targets": 4, "responded": 2, "denied": 1, "missing": 1}


def test_round_clusters_by_idle_gap(tmp_path):
    """60s 空闲切簇：两个任务两轮；可配置 cluster_gap_seconds 合并为一轮。"""
    frames = [
        down645(0x2001, [ADDR_A], t="10:00:00.000"),
        up645(0x2001, ADDR_A, t="10:00:01.000"),
        down645(0x2002, [ADDR_A], t="10:01:30.000"),
        up645(0x2002, ADDR_A, t="10:01:31.000"),
    ]
    service = build_index(tmp_path, frames)
    tracer = TraceService(service)
    report = tracer.run_replay({"scope": "round", "feature": {"app_id": "0003"}})
    assert report["summary"]["rounds"] == 2
    merged = tracer.run_replay({
        "scope": "round",
        "response_policy": {"cluster_gap_seconds": 120},
        "feature": {"app_id": "0003"},
    })
    assert merged["summary"]["rounds"] == 1


def test_round_dynamic_extra_response(tmp_path):
    """应答了但不在本轮下行目标里的表：单独列出（extra），不混入缺席。"""
    frames = [
        down645(0x2004, [ADDR_A], t="10:00:00.000"),
        up645(0x2004, ADDR_A, t="10:00:01.000"),
        up645(0x2004, ADDR_C, t="10:00:02.000"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003"},
    })
    rd = report["rounds"][0]
    extra = [m for m in rd["meter_table"] if m.get("extra")]
    assert [m["meter_addr"] for m in extra] == [DISPLAY_C]
    assert rd["meters"]["missing"] == 0
    assert rd["meters"]["responded"] == 1


def test_round_698_oad_targets(tmp_path):
    """698 内嵌 OAD token 作为对账键（真机 0x0003 形态）。"""
    entries = b"".join(bytes([0x00, x, 0x02, 0x01, 0x00]) for x in (0x20, 0x30))
    data = bytes.fromhex("685D004305163568000070107A86" + "10003905032750020200012021"
                         + "02001C07EA061D0A2D000700") + entries + \
        bytes.fromhex("0110F0D55698838E08158F013B87D49A8CD7B3C716")
    raw_down = meter_app_raw(0x2005, 3, data)
    # 上行回显同一 698 帧（请求 tag 05 → 响应 tag 85），位图 bit0=0 bit1=1：
    # 报文有应答，第 2 个条目有数据
    apdu_pos = data.find(bytes.fromhex("10003905")) + 3
    up_data = data[:apdu_pos] + bytes([0x85]) + data[apdu_pos + 1:]
    raw_up = meter_app_raw(0x2005, 3, up_data, up=True, resp_bitmap=0b10)
    frames = [
        ("10:00:00.000", raw_down.hex().upper(), summary0003(raw_down)),
        ("10:00:01.000", raw_up.hex().upper(), summary0003(raw_up, src="087", dst="001")),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003"},
    })
    rd = report["rounds"][0]
    statuses = {m["meter_addr"]: m["status"] for m in rd["meter_table"]}
    # 698 WithList 位图为整帧级应答标志：非 0 即回显条目全部计为应答
    # （条目内 per-meter 结果语义后置，DESIGN §9）
    assert statuses == {"00200201": "ok", "00300201": "ok"}


def test_round_698_bitmap_zero_means_no_answer(tmp_path):
    """应答位图=0：STA 明确报告无应答，条目不进应答集（缺席分类）。"""
    entries = b"".join(bytes([0x00, x, 0x02, 0x01, 0x00]) for x in (0x20, 0x30))
    data = bytes.fromhex("685D004305163568000070107A86" + "10003905032750020200012021"
                         + "02001C07EA061D0A2D000700") + entries + \
        bytes.fromhex("0110F0D55698838E08158F013B87D49A8CD7B3C716")
    raw_down = meter_app_raw(0x2006, 3, data)
    apdu_pos = data.find(bytes.fromhex("10003905")) + 3
    up_data = data[:apdu_pos] + bytes([0x85]) + data[apdu_pos + 1:]
    raw_up = meter_app_raw(0x2006, 3, up_data, up=True, resp_bitmap=0)
    frames = [
        ("10:00:00.000", raw_down.hex().upper(), summary0003(raw_down)),
        ("10:00:01.000", raw_up.hex().upper(), summary0003(raw_up, src="087", dst="001")),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003"},
    })
    rd = report["rounds"][0]
    statuses = {m["meter_addr"]: m["status"] for m in rd["meter_table"]}
    assert statuses == {"00200201": "missing", "00300201": "missing"}
    # STA 应答帧在（回显序号）但位图=0 无表数据：流级算已响应，表级全部缺席
    assert rd["flows"][0]["stage"] == "confirmed"
    assert rd["flows"][0]["responses"] == []


# ---------------------------------------------------------------------------
# campaign 粒度 + 0x0020 + 坏帧 + L2
# ---------------------------------------------------------------------------

def test_campaign_aggregates_rounds(tmp_path):
    frames = [
        down645(0x3001, [ADDR_A], t="10:00:00.000"),
        up645(0x3001, ADDR_A, t="10:00:01.000"),
        down645(0x3002, [ADDR_A], t="10:05:00.000"),
        down645(0x3003, [ADDR_A], t="10:10:00.000"),
        up645(0x3003, ADDR_A, t="10:10:01.000"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "campaign", "feature": {"app_id": "0003"},
    })
    assert report["summary"]["rounds"] == 3
    assert report["summary"]["flows"] == 3
    assert report["summary"]["full_chain"] == 2
    assert report["summary"]["no_ack"] == 1  # 0x3002 无 ACK 无响应，断在 S1
    assert len(report["proxy_graph"]) == 1
    assert report["proxy_graph"][0]["sta_tei"] == "087"
    assert report["proxy_graph"][0]["observations"] == 2


def test_uplink_flow_confirmed_by_0020(tmp_path):
    """STA 主动上报反向流：CCO 回 0x0020 = 显式确认（铁证）。"""
    raw08 = app08(0x0113, ADDR_A)
    raw20 = app20(0x0113, down=True, confirm=True)
    frames = [
        ("10:00:00.000", raw08.hex().upper(), summary0008(raw08)),
        ("10:00:00.500", raw20.hex().upper(), summary0020(raw20)),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "campaign", "feature": {"app_id": "0008"},
    })
    flow = report["rounds"][0]["flows"][0]
    assert flow["stage"] == "confirmed"
    assert flow["s3"]["evidence_kind"] == "explicit_ack"
    assert flow["confirm"]["frame_id"] == 2


def test_uplink_flow_denied_by_0020(tmp_path):
    raw08 = app08(0x0114, ADDR_A)
    raw20 = app20(0x0114, down=True, confirm=False)
    frames = [
        ("10:00:00.000", raw08.hex().upper(), summary0008(raw08)),
        ("10:00:00.500", raw20.hex().upper(), summary0020(raw20)),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "campaign", "feature": {"app_id": "0008"},
    })
    assert report["rounds"][0]["flows"][0]["stage"] == "denied"


def test_bad_frames_counted_not_judged(tmp_path):
    """坏帧口径：不计入判定、单独计数（§9）。"""
    frames = [
        down645(0x3004, [ADDR_A]),
        up645(0x3004, ADDR_A),
    ]
    parser = FakeParser({raw: summary for _, raw, summary in frames})
    service = LogFileService(parser=parser, database_path=tmp_path / "idx.sqlite3")
    records = [("1", frames[0][0], frames[0][1]), ("2", frames[1][0], frames[1][1])]
    service.append_frames(records)
    # 直插一条坏帧（parse_error 非空）
    import sqlite3
    conn = sqlite3.connect(service.database_path)
    conn.execute(
        "INSERT INTO frames (sequence, log_time, byte_length, raw_hex, parse_error, app_id) "
        "VALUES ('x', '10:00:00.500', 10, '7E', 'CRC 错误', '')"
    )
    conn.commit()
    conn.close()
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003"},
    })
    assert report["summary"]["bad_frames"] == 1
    assert report["summary"]["full_chain"] == 1


def test_l2_app_raw_contains(tmp_path):
    frames = [
        down645(0x3005, [ADDR_A]),
        down645(0x3006, [ADDR_B]),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round",
        "feature": {"app_id": "0003", "app_raw_contains": frame645(ADDR_B).hex().upper()[:16]},
    })
    seqs = [f["msg_seq"] for r in report["rounds"] for f in r["flows"]]
    assert seqs == ["0x3006"]


def test_l0_dst_tei_filter(tmp_path):
    frames = [
        down645(0x3007, [ADDR_A], dst="087"),
        down645(0x3008, [ADDR_A], dst="0D8"),
    ]
    service = build_index(tmp_path, frames)
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003", "dst_tei": "0D8"},
    })
    seqs = [f["msg_seq"] for r in report["rounds"] for f in r["flows"]]
    assert seqs == ["0x3008"]


def test_feature_validation_errors():
    tracer = TraceService(None)
    with pytest.raises(FeatureError):
        tracer.run_replay({"scope": "batch", "feature": {"app_id": "0003"}})
    with pytest.raises(FeatureError):
        tracer.run_replay({"scope": "round", "feature": {}})
    with pytest.raises(FeatureError):
        tracer.run_replay({
            "scope": "round",
            "window": {"mode": "time_range", "start_time": "11:00:00", "end_time": "10:00:00"},
            "feature": {"app_id": "0003"},
        })
    with pytest.raises(FeatureError):
        tracer.run_replay({"scope": "round", "feature": {"app_id": "0003", "msg_seq": "ZZZZ"}})


# ---------------------------------------------------------------------------
# REQS-0022 Phase 1：raw_hex_contains 末端验证 + flow frames 方向投影
# ---------------------------------------------------------------------------

def test_l2_raw_hex_contains_is_terminal_filter(tmp_path):
    """raw_hex_contains 对整帧 raw_hex 做末端验证（有 app_id 收窄）。"""
    frames = [
        down645(0x3010, [ADDR_A], t="10:00:00.000"),
        down645(0x3011, [ADDR_B], t="10:00:01.000"),
    ]
    service = build_index(tmp_path, frames)
    # 下行帧 app 头 seq 字段小端：0x3010 → "103028"（seq + timeout）
    report = TraceService(service).run_replay({
        "scope": "round", "feature": {"app_id": "0003", "raw_hex_contains": "103028"},
    })
    seqs = [f["msg_seq"] for r in report["rounds"] for f in r["flows"]]
    assert seqs == ["0x3010"]


def test_raw_hex_contains_format_validation():
    tracer = TraceService(None)
    with pytest.raises(FeatureError):  # 奇数长度
        tracer.run_replay({"scope": "round", "feature": {"app_id": "0003", "raw_hex_contains": "ABC"}})
    with pytest.raises(FeatureError):  # 非 hex 字符
        tracer.run_replay({"scope": "round", "feature": {"app_id": "0003", "raw_hex_contains": "ZZ"}})
    with pytest.raises(FeatureError):  # 超长（>512）
        tracer.run_replay({"scope": "round", "feature": {"app_id": "0003",
                                                         "raw_hex_contains": "AB" * 257}})


def test_flow_frames_projection_and_directions_filter(tmp_path):
    """flow 报告带 frames 方向投影；directions 只筛投影，不动状态机证据。"""
    frames = [
        down645(0x1EC2, [ADDR_A], t="10:00:00.000"),
        ("10:00:00.100", ack_raw_hex("087"), summary_ack()),
        up645(0x1EC2, ADDR_A, t="10:00:01.000"),
    ]
    service = build_index(tmp_path, frames)
    tracer = TraceService(service)

    report = tracer.run_replay({
        "scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC2"},
    })
    projection = report["flow"]["frames"]
    by_role = {f["role"]: f for f in projection}
    assert set(by_role) == {"sent", "ack", "response"}
    assert by_role["sent"]["direction"] == "downlink"
    assert by_role["response"]["direction"] == "uplink"
    assert by_role["ack"]["direction"] == "ack"
    assert by_role["sent"]["frm_type"] == "终端主动并发抄表"
    assert by_role["sent"]["src"] == "001" and by_role["sent"]["dst"] == "087"
    assert by_role["response"]["meter_addrs"][0]["addr"] == DISPLAY_A
    assert [f["frame_id"] for f in projection] == [1, 2, 3]

    down_only = tracer.run_replay(
        {"scope": "flow", "feature": {"app_id": "0003", "msg_seq": "1EC2"}},
        directions=["downlink"],
    )
    # 方向筛选后：状态机与必要证据（ack/response）保持完整，仅投影收窄
    assert down_only["flow"]["stage"] == "confirmed"
    assert down_only["flow"]["ack"] is not None
    assert down_only["flow"]["response"] is not None
    assert [f["direction"] for f in down_only["flow"]["frames"]] == ["downlink"]


# ---------------------------------------------------------------------------
# REQS-0022 Phase 0：默认解析资格确认
#
# 目的：确认侦听台默认解析后端（GwHPLCAnalysis.dll）已经暴露并发抄表所需的
# 全部字段，使 REQS-0022 无需新建第二套搜索服务或再分类一遍业务数据。
# 这里只做「资格」确认，不是最终业务验收（最终验收见 Phase 4）。
# ---------------------------------------------------------------------------

_DLL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "libs" / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
)

# 一帧真实的终端主动并发抄表帧（源自 测试文件/并发抄表-样本.txt）。
# 内联以免资格测试依赖被 .gitignore 忽略的样本文件（176 字节 / 352 hex 字符）。
RAW_0003 = (
    "7E FF 02 FF 2E 6B 68 5F 67 05 99 03 1E 70 CB ED 00 FF 03 94 00 01 00 01 "
    "69 7F 94 01 70 08 03 DC 13 79 40 00 1E 98 F7 10 00 87 00 0A BC AF 30 6F "
    "88 FF 00 00 33 00 00 11 03 00 00 01 02 33 06 C2 1E 28 0A FE FE FE FE 68 "
    "5D 00 43 05 16 35 68 00 00 70 10 7A 86 10 00 39 05 03 27 50 02 02 00 01 "
    "20 21 02 00 1C 07 EA 06 1D 0A 2D 00 07 00 00 20 02 01 00 00 30 02 01 00 "
    "00 40 02 01 00 00 50 02 01 00 00 60 02 01 00 00 70 02 01 00 00 80 02 01 "
    "00 01 10 F0 D5 56 98 83 8E 08 15 8F 01 3B 87 D4 9A 8C D7 22 80 16 AD 57 "
    "E6 9C 00 B7 5B 0E 25 7E"
)

_Q_FIELDS = ("FrmType", "SNID", "SRC", "DST", "ORI_S", "APP_ID", "APP_RAW")


@pytest.fixture(scope="module")
def parser():
    """侦听台默认解析后端（真实 GwHPLCAnalysis.dll）。

    DLL 缺失时 skip，绝不降级为「通过」——资格确认失败必须可见。
    """
    if not _DLL_PATH.exists():
        pytest.skip(f"缺解析库：{_DLL_PATH}")
    from shared.dotnet_parser import DotNetHplcParser
    from shared.parser_service import ParserService
    return ParserService(DotNetHplcParser(_DLL_PATH))


@pytest.fixture(scope="module")
def raw_0003():
    return RAW_0003


def test_default_summary_exposes_parallel_meter_reading_fields(parser, raw_0003):
    """一帧并发抄表摘要必须含 FrmType、NID、源/目的、APP_ID 和 APP_RAW。"""
    simple = parser.parse_summary(raw_0003)["simple"]
    assert simple["APP_ID"] == "0003"
    assert simple["FrmType"] == "终端主动并发抄表"
    assert simple["SNID"]
    assert simple["SRC"] and simple["DST"]
    assert simple["APP_RAW"]


def test_temp_index_qualification_report(tmp_path, parser, raw_0003):
    """为原始样本建立只读临时索引的资格报告（**不是**最终业务验收）。

    只记录 parser backend、字段是否存在和样本 SHA-256；不声明业务验收通过。
    """
    service = LogFileService(parser=parser, database_path=tmp_path / "qual.sqlite3")
    service.append_frames([("1", "00:00:00.000", raw_0003)])
    summary = service.get_frame(1)["summary"]

    report = {
        "parser_backend": "dotnet",
        "sha256": hashlib.sha256(raw_0003.replace(" ", "").encode("ascii")).hexdigest(),
        "fields": {key: bool(summary.get(key)) for key in _Q_FIELDS},
        "trace_ready": service.trace_ready(),
    }
    assert len(report["sha256"]) == 64
    assert report["trace_ready"] is True
    assert all(report["fields"].values()), report["fields"]
    # 资格报告只资格、不判业务：不得出现 verdict / pass 之类的结论字段
    assert not {"verdict", "pass", "accepted"} & set(report)
