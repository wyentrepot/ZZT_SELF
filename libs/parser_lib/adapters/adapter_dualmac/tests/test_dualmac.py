"""adapter_dualmac 单测：真机样本回归（REQS-0024 阶段0/1/2/3 固化锚点）。

夹具为 reqs/0009 真机样本原样截取；期望值部分来自样本人工核验（CCO MAC、
信标周期 14878ms、心跳 OSA=035 等），部分来自跨帧交叉验证（信标区 3348ms
= 网间协调帧 duration 3348ms）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from parser_lib.adapters.adapter_dualmac import (  # noqa: E402
    decode_frame, parse_beacon, parse_fch, parse_gw_frame, parse_mac_header,
    parse_mgmt, strip_gw, rebuild_schedule,
)
from parser_lib.adapters.adapter_dualmac.tests.samples import (  # noqa: E402
    APP_FRAME, ASSOC_CNF, CENTRAL_BEACON, COORD, HEARTBEAT, PROXY_BEACON,
    PROXY_REQ, SACK,
)


class TestGwLayer:
    def test_strip_variants(self):
        raw = bytes.fromhex(CENTRAL_BEACON)
        assert len(strip_gw(raw)) == len(raw)
        prefixed = b"\x7e\xff\x02" + raw + b"\x7e"
        assert strip_gw(prefixed) == raw
        text = "7E FF 02 " + raw.hex().upper() + " 7E\n"
        assert strip_gw(text) == raw

    def test_delimiter_and_nid(self):
        gw = parse_gw_frame(strip_gw(CENTRAL_BEACON))
        assert gw.delimiter == 0
        assert gw.nid_hex == "947F69"  # LE24 小端口径，与 network_assessment 一致

    def test_sack_no_region(self):
        gw = parse_gw_frame(strip_gw(SACK))
        assert gw.delimiter == 2
        assert gw.region == b""
        assert len(gw.gw_tail) == 4


class TestFch:
    def test_sof_fch(self):
        fch = parse_fch(strip_gw(HEARTBEAT)[20:36])
        assert fch.delimiter == 1
        assert fch.nid_hex == "947F69"
        assert fch.variable["teis"] == 0x035
        assert fch.variable["base_tmi"] == 4  # PB 块体 132B（载荷区实测吻合）

    def test_coord_fch(self):
        fch = parse_fch(strip_gw(COORD)[20:36])
        assert fch.delimiter == 3
        assert fch.variable["duration_ms"] == 3348


class TestMacHeader:
    def test_cco_downlink(self):
        gw = parse_gw_frame(strip_gw(ASSOC_CNF))
        mac = parse_mac_header(gw.region)
        assert mac.teis == 0x001          # CCO
        assert mac.teid == 0x0D8
        assert mac.msdu_type == 0         # 网络管理消息
        assert mac.send_type_name == "单播"
        assert mac.header_len == 16

    def test_msdu_icv(self):
        from parser_lib.adapters.adapter_dualmac.mac_header import extract_msdu
        gw = parse_gw_frame(strip_gw(HEARTBEAT))
        mac = parse_mac_header(gw.region)
        msdu = extract_msdu(gw.region, mac)
        assert msdu.icv_ok is True
        assert not msdu.truncated
        assert len(msdu.data) == mac.msdu_len


class TestBeacon:
    def test_central_beacon(self):
        gw = parse_gw_frame(strip_gw(CENTRAL_BEACON))
        bcn = parse_beacon(gw.region)
        assert bcn.bcn_type_name == "中央信标"
        assert bcn.cco_mac_text == "200000127472"
        assert bcn.cycle_count == 1784
        assert bcn.permit_assoc is True
        assert bcn.bpcs_ok is True
        names = [it.name for it in bcn.items]
        assert names == ["站点能力", "路由参数", "万年历同步", "时隙分配", "无线路由参数"]

    def test_schedule_rebuild(self):
        gw = parse_gw_frame(strip_gw(CENTRAL_BEACON))
        bcn = parse_beacon(gw.region)
        sched = bcn.schedule
        # 实测权威值：信标周期 14878ms（与 network_assessment 实测一致）
        assert sched["beacon_period_ms"] == 14878
        assert sched["no_central_slots"] == 90
        assert sched["central_slots"] == 3
        assert sched["csma_phase_cnt"] == 3
        p = sched["periods_ms"]
        # 信标区 = (90+3)×36ms = 3348ms —— 与网间协调帧 duration 3348ms 交叉一致
        assert p["beacon"] == {"start": 0, "end": 3348}
        assert p["csma"]["start"] == 3348
        assert p["csma"]["end"] <= 14878
        assert len(sched["csma_slots"]) == 3

    def test_proxy_beacon(self):
        gw = parse_gw_frame(strip_gw(PROXY_BEACON))
        bcn = parse_beacon(gw.region)
        assert bcn.bcn_type_name == "代理信标"
        assert bcn.bpcs_ok is True

    def test_synthetic_minimal(self):
        # 合成最小中央信标：头 + 无条目 + BPCS
        import zlib
        content = bytearray(21)
        content[0] = 0x42  # type=2 中央 + 允许关联
        content[2:8] = bytes.fromhex("200000127472")
        content[8:12] = (7).to_bytes(4, "little")
        content[20] = 0
        crc = zlib.crc32(bytes(content)) & 0xFFFFFFFF
        payload = bytes(content) + crc.to_bytes(4, "little")
        bcn = parse_beacon(payload)
        assert bcn.cycle_count == 7
        assert bcn.bpcs_ok is True
        assert bcn.items == []


class TestMgmt:
    def test_heartbeat(self):
        gw = parse_gw_frame(strip_gw(HEARTBEAT))
        msg = parse_mgmt(gw.region[16:16 + ((gw.region[9] & 7) << 8 | gw.region[8])])
        assert msg.mm_name == "心跳检测"
        assert msg.fields["osa_tei"] == 0x035
        assert msg.fields["most_discover_sta"] == 156
        assert msg.fields["bitmap_size"] == 47
        assert msg.fields["active_cnt"] == 136

    def test_assoc_cnf(self):
        gw = parse_gw_frame(strip_gw(ASSOC_CNF))
        mac_len = (gw.region[9] & 7) << 8 | gw.region[8]
        msg = parse_mgmt(gw.region[16:16 + mac_len])
        assert msg.mm_name == "关联确认"
        f = msg.fields
        assert f["cco_mac"] == "200000127472"
        assert f["rslt"] == 0x0A and f["rslt_name"] == "再次关联成功"
        assert f["sta_tei"] == 257
        assert f["proxy_tei"] == 0x0D8
        assert 290000 < f["retry_time_ms"] <= 310000  # ≈ RETRY_ASSOC_TIME 300s

    def test_proxy_change_req(self):
        gw = parse_gw_frame(strip_gw(PROXY_REQ))
        mac_len = (gw.region[9] & 7) << 8 | gw.region[8]
        msg = parse_mgmt(gw.region[16:16 + mac_len])
        assert msg.mm_name == "代理变更请求"
        assert msg.fields["sta_tei"] == 0x0DF
        assert msg.fields["old_proxy_tei"] == 5
        assert msg.fields["why_change"] == 1  # 周期代理变更

    def test_unknown_mmtype(self):
        msg = parse_mgmt(bytes.fromhex("3E000000" + "00" * 8))
        assert msg.mmtype == 0x003E
        assert "table_hex" in msg.fields


class TestEvents:
    def test_heartbeat_event(self):
        r = decode_frame(HEARTBEAT)
        assert len(r.events) == 1
        ev = r.events[0]
        assert ev["event"] == "heartbeat"
        assert ev["direction"] == "mesh"  # PCO(035)→PCO(006) 中继段
        assert "035" in ev["summary"]

    def test_assoc_cnf_event_down(self):
        r = decode_frame(ASSOC_CNF)
        ev = r.events[0]
        assert ev["event"] == "assoc_cnf"
        assert ev["direction"] == "down"  # TEIs=001 CCO
        assert ev["fields"]["rslt_name"] == "再次关联成功"

    def test_beacon_event(self):
        r = decode_frame(CENTRAL_BEACON)
        ev = r.events[0]
        assert ev["event"] == "beacon_central"
        assert ev["fields"]["beacon_period_ms"] == 14878

    def test_coord_event(self):
        r = decode_frame(COORD)
        assert r.events[0]["event"] == "coord_frame"

    def test_app_event(self):
        r = decode_frame(APP_FRAME)
        assert r.events[0]["event"] == "app_data"
        assert r.events[0]["direction"] == "down"

    def test_sack_no_event(self):
        assert decode_frame(SACK).events == []


class TestAdapterProtocol:
    def test_confidence_and_decode(self):
        from parser_lib.adapters.adapter_dualmac import DualMacAdapter
        adapter = DualMacAdapter()
        assert adapter.confidence(bytes.fromhex(CENTRAL_BEACON)) > 0.5
        frame = adapter.decode(bytes.fromhex(HEARTBEAT))
        assert "SOF" in frame.structure
        assert frame.address == "947F69"
        assert any("事件[心跳检测]" in f.name for f in frame.items)

    def test_decode_from_log_line(self):
        line = "7E FF 02 " + HEARTBEAT + " 7E"
        r = decode_frame(line)
        assert r.mac.teis == 0x035
