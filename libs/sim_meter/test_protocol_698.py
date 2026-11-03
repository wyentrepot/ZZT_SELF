"""698.45 从站侧（模拟电表）协议与脑应答单元测试。

口径对齐 STA 源码 /home/H_STA/01/sta：
- 读 43000200：chongqing_make_698_frame(dlt698.c:146) 发 SECURITY-Request，
  应答定长头 chongqing_parser(aps_stack.c:4376)：85 01 PIID 43 00 02 00 01 0A N。
- 主动上报：REPORT-Notification list 由 procedure_server_report_698_frame
  (dlt698_45.c:3693) 解析，要求 dir=1、prm=0 → 控制域 0x83（不是响应帧的 0xC3）。
"""
import pytest

from parser_lib.adapters.adapter_645 import build_frame as build_645
from parser_lib.adapters.adapter_698 import _check_fcs, build_frame as build_698
from sim_meter.meter import MeterBrain
from sim_meter.protocol_698 import (
    FE_PREAMBLE,
    JZJC_OAD,
    build_event_report,
    build_get_request_43000200,
    build_jzjc_response,
    parse_698_frame,
)

ADDR = bytes.fromhex("000000000001")     # 本表地址（6B）
STA = bytes.fromhex("1200000000AA")      # 目标 STA 地址（6B）


def _link_fields(frame: bytes) -> bytes:
    """剥离 FE 前导并校验 68/L/16/HCS/FCS，返回 68 起的链路帧。"""
    assert frame[:4] == FE_PREAMBLE
    f = frame[4:]
    assert f[0] == 0x68 and f[-1] == 0x16
    L = f[1] | (f[2] << 8)
    assert len(f) == L + 2
    sa_len = (f[4] & 0x0F) + 2
    hcs_pos = 4 + sa_len + 1
    fcs_pos = len(f) - 3
    assert _check_fcs(f[1:4 + sa_len + 1], f[hcs_pos:hcs_pos + 2]), "HCS 校验失败"
    assert _check_fcs(f[1:fcs_pos], f[fcs_pos:fcs_pos + 2]), "FCS 校验失败"
    return f


def _get_request(addr: bytes, oad: bytes, piid: int = 1, security: bool = True,
                 control: int = 0x43) -> bytes:
    inner = bytes([0x05, 0x01, piid]) + bytes(oad) + b"\x00"
    if security:
        apdu = bytes([0x10, 0x00, len(inner)]) + inner + b"\x01\x10" + b"\x00" * 16
    else:
        apdu = inner
    return FE_PREAMBLE + build_698(apdu, b"\x05" + addr, ca=0x00, control=control)


# ---------------------------------------------------------------------------
# parse_698_frame
# ---------------------------------------------------------------------------
class TestParse698:
    def test_get_request_secure(self):
        req = build_get_request_43000200(ADDR, piid=1, security=True)
        f = _link_fields(req)
        assert f[3] == 0x43
        assert f[4] == 0x05 and f[5:11] == ADDR and f[11] == 0x00
        p = parse_698_frame(req)
        assert p.raw == f
        assert p.control == 0x43
        assert p.addr == b"\x05" + ADDR
        assert p.ca == 0x00
        assert p.security == 0x10
        assert p.service == 0x05
        assert p.request_type == 1
        assert p.piid == 1
        assert p.oad == JZJC_OAD
        assert p.inner_apdu == bytes([0x05, 0x01, 0x01]) + JZJC_OAD + b"\x00"
        assert p.list_num is None and p.data is None

    def test_get_request_plain(self):
        req = build_get_request_43000200(ADDR, piid=2, security=False)
        p = parse_698_frame(req)
        assert p.security is None
        assert p.inner_apdu is None
        assert p.service == 0x05
        assert p.piid == 2
        assert p.oad == JZJC_OAD

    def test_report_notification(self):
        wire = build_event_report(STA, bytes.fromhex("20140200"), bytes.fromhex("00"), piid=1)
        p = parse_698_frame(wire)
        assert p.service == 0x88
        assert p.request_type == 1
        assert p.piid == 1
        assert p.list_num == 1
        assert p.oad == bytes.fromhex("20140200")
        assert p.data == bytes.fromhex("00")
        assert p.security is None and p.inner_apdu is None

    def test_reject_bad_hcs(self):
        req = bytearray(build_get_request_43000200(ADDR))
        req[4 + 12] ^= 0xFF                      # HCS lo（68 帧偏移 12）
        with pytest.raises(ValueError, match="HCS"):
            parse_698_frame(bytes(req))

    def test_reject_bad_fcs(self):
        req = bytearray(build_get_request_43000200(ADDR))
        req[-3] ^= 0xFF                          # FCS lo
        with pytest.raises(ValueError, match="FCS"):
            parse_698_frame(bytes(req))

    def test_reject_bad_structure(self):
        req = build_get_request_43000200(ADDR)
        with pytest.raises(ValueError, match="起始符|结束符|长度"):
            parse_698_frame(req[5:])             # 去掉 68 起始
        with pytest.raises(ValueError, match="结束符"):
            parse_698_frame(req[:-1] + b"\x00")

    def test_reject_ciphertext_security(self):
        inner = bytes([0x05, 0x01, 0x01]) + JZJC_OAD + b"\x00"
        apdu = bytes([0x10, 0x01, len(inner)]) + inner      # choice=1 密文
        frame = build_698(apdu, b"\x05" + ADDR, ca=0, control=0x43)
        with pytest.raises(ValueError, match="非明文"):
            parse_698_frame(FE_PREAMBLE + frame)


# ---------------------------------------------------------------------------
# build_jzjc_response
# ---------------------------------------------------------------------------
class TestBuildJzjcResponse:
    def test_secure_response_wire(self):
        req = build_get_request_43000200(ADDR, piid=1, security=True)
        parsed = parse_698_frame(req)
        wire = build_jzjc_response(parsed, b"DDZY666-Z")
        f = _link_fields(wire)
        assert f[3] == 0xC3                       # 服务器响应 DIR=1 PRM=1
        assert f[4] == 0x05 and f[5:11] == ADDR    # SA 回显请求
        assert f[11] == 0x00
        p = parse_698_frame(wire)
        assert p.security == 0x90
        assert p.service == 0x85
        inner = p.inner_apdu
        # 定长头 85 01 PIID 43 00 02 00 01 0A N（10 字节）+ N 字节描述符
        assert inner[:9] == bytes([0x85, 0x01, 0x01]) + JZJC_OAD + bytes([0x01, 0x0A])
        n = inner[9]
        assert inner[10:10 + n] == b"DDZY666-Z"

    def test_plain_response_no_security_wrap(self):
        req = build_get_request_43000200(ADDR, piid=3, security=False)
        parsed = parse_698_frame(req)
        wire = build_jzjc_response(parsed, b"DTSU666-D")
        p = parse_698_frame(wire)
        assert p.security is None
        assert p.apdu[0] == 0x85                   # 直接回内层，无 90 00 包装
        assert p.inner_apdu is None


# ---------------------------------------------------------------------------
# build_event_report
# ---------------------------------------------------------------------------
class TestBuildEventReport:
    def test_apdu_and_target_addr(self):
        wire = build_event_report(STA, bytes.fromhex("20140200"), bytes.fromhex("00"), piid=1)
        f = _link_fields(wire)
        assert f[3] == 0x83                       # 服务器主动上报 DIR=1 PRM=0
        assert f[4] == 0x05 and f[5:11] == STA and f[11] == 0x00
        p = parse_698_frame(wire)
        assert p.service == 0x88
        assert p.list_num == 1
        assert p.oad == bytes.fromhex("20140200")
        assert p.data == bytes.fromhex("00")

    def test_reject_bad_target_or_oad(self):
        with pytest.raises(ValueError, match="target_addr"):
            build_event_report(b"\x01", bytes.fromhex("20140200"), b"\x00")
        with pytest.raises(ValueError, match="OAD"):
            build_event_report(STA, b"\x20\x14", b"\x00")


# ---------------------------------------------------------------------------
# MeterBrain 698 应答与事件上报
# ---------------------------------------------------------------------------
class TestBrain698:
    def test_secure_request_answer(self):
        brain = MeterBrain(addr="000000000001")
        res = brain.handle_frame(build_get_request_43000200(ADDR, piid=1, security=True))
        assert res.reply is None
        assert res.reply_wire is not None
        _link_fields(res.reply_wire)
        assert res.note["func"] == "即装即采读设备描述符(43000200)"
        p = parse_698_frame(res.reply_wire)
        assert p.security == 0x90 and p.service == 0x85
        n = p.inner_apdu[9]
        assert p.inner_apdu[10:10 + n] == b"DDZY666-Z"

    def test_descriptor_follows_config(self):
        brain = MeterBrain(addr="000000000001")
        brain.set_jzjc(descriptor="DTSU666-D")
        res = brain.handle_frame(build_get_request_43000200(ADDR, piid=1))
        p = parse_698_frame(res.reply_wire)
        n = p.inner_apdu[9]
        assert p.inner_apdu[10:10 + n] == b"DTSU666-D"

    def test_broadcast_addr_answered(self):
        brain = MeterBrain(addr="000000000001")
        # 广播地址：SA 头 0x45（通配）+ AA×6
        inner = bytes([0x05, 0x01, 0x01]) + JZJC_OAD + b"\x00"
        apdu = bytes([0x10, 0x00, len(inner)]) + inner + b"\x01\x10" + b"\x00" * 16
        req = FE_PREAMBLE + build_698(apdu, b"\x45" + b"\xAA" * 6, ca=0, control=0x43)
        res = brain.handle_frame(req)
        assert res.reply_wire is not None
        assert res.note["func"] == "即装即采读设备描述符(43000200)"

    def test_non_own_addr_no_reply(self):
        brain = MeterBrain(addr="000000000001")
        res = brain.handle_frame(build_get_request_43000200(bytes.fromhex("999999999999")))
        assert res.reply is None and res.reply_wire is None
        assert res.note.get("mine") is False

    def test_unknown_oad_no_reply(self):
        brain = MeterBrain(addr="000000000001")
        res = brain.handle_frame(_get_request(ADDR, bytes.fromhex("00000001")))
        assert res.reply is None and res.reply_wire is None
        assert res.note.get("reason") == "未知 OAD"
        assert "43000200" in res.note.get("func", "")

    def test_server_dir_frame_ignored(self):
        brain = MeterBrain(addr="000000000001")
        # DIR=1（服务器发出）的 GET-Request → 直接忽略，避免 loop:// 自激
        req = _get_request(ADDR, JZJC_OAD, control=0xC3)
        res = brain.handle_frame(req)
        assert res.reply is None and res.reply_wire is None
        assert "服务器帧" in res.note.get("func", "")

    def test_non_user_data_func_ignored(self):
        brain = MeterBrain(addr="000000000001")
        req = _get_request(ADDR, JZJC_OAD, control=0x41)   # 功能码 1（链路管理）
        res = brain.handle_frame(req)
        assert res.reply_wire is None
        assert "非用户数据" in res.note.get("func", "")

    def test_event_report_config(self):
        brain = MeterBrain()
        with pytest.raises(ValueError, match="未配置"):
            brain.build_event_report()
        assert brain.set_event_target("1200000000AA") == "1200000000AA"
        assert brain.get_event_config() == {"event_target_addr": "1200000000AA"}
        wire = brain.build_event_report(data_hex="00")
        p = parse_698_frame(wire)
        assert p.addr == b"\x05" + STA
        assert p.oad == bytes.fromhex("20140200")
        assert p.data == bytes.fromhex("00")

    def test_event_report_target_override(self):
        brain = MeterBrain()
        brain.set_event_target("1200000000AA")
        wire = brain.build_event_report(target_addr="0000000000FF")
        assert parse_698_frame(wire).addr == b"\x05" + bytes.fromhex("0000000000FF")


# ---------------------------------------------------------------------------
# 645 回归：698 识别不得抢占 645 帧
# ---------------------------------------------------------------------------
class Test645Regression:
    def test_645_read_addr_still_answered(self):
        brain = MeterBrain(addr="000000000001")
        res = brain.handle_frame(build_645(ADDR, 0x13, b""))
        assert res.reply is not None
        assert res.reply[8] == 0x93
        assert res.note["func"] == "读通信地址"

    def test_645_with_fe_preamble_still_answered(self):
        brain = MeterBrain(addr="000000000001")
        req = b"\xFE" * 4 + build_645(ADDR, 0x13, b"")
        res = brain.handle_frame(req)
        assert res.reply is not None
        assert res.reply[8] == 0x93
