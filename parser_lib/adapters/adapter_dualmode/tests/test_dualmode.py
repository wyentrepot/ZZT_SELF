"""双模 4-3 通用报文头解析测试。"""
import os

from parser_lib.adapters import build_adapters
from parser_lib.adapters.adapter_dualmode import DualMode43Adapter
from parser_lib.core.splitter import FrameSplitter

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _fixture(*parts):
    path = os.path.join(ROOT, *parts)
    return bytes.fromhex(open(path, encoding="utf-8").read().strip())


def _f645():
    return bytes.fromhex("6812345678901268910800000100123456783416")


def _meter_business(proto_type, data, seq=0x0001, timeout=0x0A):
    """构造抄表类业务报文头（协议版本号=1, 报文头长度=8）+ DATA。"""
    ver = 1
    header_len = 8
    b0 = ver | ((header_len & 0x03) << 6)
    b1 = (header_len >> 2) & 0x0F
    b2 = (proto_type & 0x0F) | (((len(data) >> 8) & 0x0F) << 4)
    b3 = len(data) & 0xFF
    return bytes([b0, b1, b2, b3, seq & 0xFF, (seq >> 8) & 0xFF, timeout, 0x00]) + data


def _field(frame, name):
    for f in frame.fields:
        if f.name == name:
            return f
    return None


def test_decode_43_header_with_nested_645():
    raw = bytes.fromhex("11030000") + _meter_business(2, _f645())
    frame = DualMode43Adapter().decode(raw)

    assert frame.structure == "双模4-3"
    assert _field(frame, "报文端口号").value == "0x11 (普通业务)"
    assert "终端主动并发抄表" in _field(frame, "报文ID").value
    assert _field(frame, "报文控制字").value == "0x00"
    # 抄表业务报文头正确解析，不再有伪「源NA/目的NA」字段
    assert _field(frame, "协议版本号").raw == 1
    assert _field(frame, "报文头长度").raw == 8
    assert _field(frame, "转发数据规约类型").raw == 2
    assert _field(frame, "源NA") is None
    assert _field(frame, "目的NA") is None
    assert len(frame.nested) == 1
    assert frame.nested[0].structure == "645"


def test_splitter_keeps_43_envelope_before_nested_698():
    inner_698 = _fixture("adapter_698", "tests", "fixtures", "login_req.hex")
    raw = bytes.fromhex("11030000") + _meter_business(3, inner_698)
    adapters, _ = build_adapters()
    splitter = FrameSplitter(adapters)

    frames = splitter.feed(raw)

    assert len(frames) == 1
    assert frames[0]["protocol"] == "双模4-3"
    parsed = next(a for a in adapters if a.protocol == "双模4-3").decode(frames[0]["raw"])
    assert parsed.structure == "双模4-3"
    # 按报文头长度定位 DATA 并递归出 698，不再有伪 NA 字段
    assert _field(parsed, "转发数据规约类型").raw == 3
    assert _field(parsed, "源NA") is None
    assert _field(parsed, "目的NA") is None
    assert len(parsed.nested) == 1
    assert parsed.nested[0].structure == "698.45"


def test_real_concurrent_meter_frame_starts_at_1103():
    raw = bytes.fromhex(
        "110300000102630859050100688400c30535378109003010f18390006b850337"
        "5002020008002021020000200104000020000200002001020000200402000020"
        "0a02000000100201000020020101011c07ea061d0e1e00050000000001011208"
        "a30101050000000001020500000000050000000001021003e81003e806000000"
        "0006000000000000010004d0c1a502010016"
    )
    adapters, _ = build_adapters()
    frames = FrameSplitter(adapters).feed(raw)

    assert len(frames) == 1
    assert frames[0]["protocol"] == "双模4-3"
    assert frames[0]["raw"] == raw
    parsed = next(a for a in adapters if a.protocol == "双模4-3").decode(raw)
    assert _field(parsed, "报文端口号").hex == "11"
    assert _field(parsed, "报文ID").raw == 0x0003
    assert parsed.nested[0].structure == "698.45"


def test_user_3line_concurrent_meter_frame_with_698():
    """用户真实日志（跨3行）：并发抄表下行，DATA 承载 698.45 帧。

    验证：通用头不含 NA；按报文头长度定位 DATA；按规约类型(698.45)递归解出
    内嵌 698 帧（含 GetResponseRecord 与读表数据），而非把业务头误标为源/目的NA。
    """
    raw = bytes.fromhex(
        "11030000010263085d050100688400c30544378109003010680d90006b85033c5"
        "0020200080020210200002001040000200002000020010200002004"
        "020000200a02000000100201000020020101011c07ea061d0e1e0005000009ad"
        "01011208a1010105000009a6010205000007e505000007e501021001"
        "72100172060007ba1a06000000000000010004a13645e433b216"
    )
    adapters, _ = build_adapters()
    frames = FrameSplitter(adapters).feed(raw)

    assert len(frames) == 1
    assert frames[0]["protocol"] == "双模4-3"
    parsed = next(a for a in adapters if a.protocol == "双模4-3").decode(frames[0]["raw"])
    assert parsed.structure == "双模4-3"
    # 通用头无 MAC/NA 字段
    assert _field(parsed, "源NA") is None
    assert _field(parsed, "目的NA") is None
    # 抄表业务报文头正确解析
    assert _field(parsed, "协议版本号").raw == 1
    assert _field(parsed, "报文头长度").raw == 8
    assert _field(parsed, "转发数据规约类型").raw == 3  # DL/T698.45
    assert _field(parsed, "转发数据长度").raw == 0x086
    # DATA 递归解出 698.45 帧
    assert len(parsed.nested) == 1
    assert parsed.nested[0].structure == "698.45"
    n698 = parsed.nested[0]
    assert any("GetResponseRecord" in str(f.value) for f in n698.fields)


def test_payload_12_with_unknown_message_id_is_not_upgrade_envelope():
    # This is the residual sequence from the Rizhao capture.  It used to be
    # accepted as port 0x12 + unknown message ID 0x0000 merely because a valid
    # 698 frame occurred later in the bytes.
    false_envelope = bytes.fromhex(
        "12000020555278000070f1010003006300fefefefe"
        "685d0043055552780000701032c61000390503285002020001202102001c"
        "07ea061d0a2d000700002002010000300201000040020100005002010000"
        "60020100007002010000800201000110f0d55698838e08158f013b87d49a"
        "8cd7aabf16"
    )
    adapter = DualMode43Adapter()

    assert adapter.try_extract(false_envelope) is None
    assert adapter.confidence(false_envelope) == 0.0


def test_splitter_skips_unknown_total_length_outer_envelope():
    inner_698 = _fixture("adapter_698", "tests", "fixtures", "login_req.hex")
    valid_43 = bytes.fromhex("1103000001026308") + inner_698
    body = bytes.fromhex("830400100002a5463781090030")
    total = 3 + len(body) + 1
    unknown_outer = bytes([0x68, total & 0xFF, total >> 8]) + body + b"\x16"
    adapters, _ = build_adapters()
    frames = FrameSplitter(adapters).feed(unknown_outer + valid_43)
    assert len(frames) == 1
    assert frames[0]["protocol"] == "双模4-3"
    assert frames[0]["raw"] == valid_43


def test_splitter_recovers_after_truncated_698_before_1103():
    inner_698 = _fixture("adapter_698", "tests", "fixtures", "login_req.hex")
    valid_43 = bytes.fromhex("1103000001026308") + inner_698
    # L says 0x87, but the record ends early and lacks its final 0x16.
    truncated = bytes.fromhex("68870043040000000029727412000020") + bytes(120)
    adapters, _ = build_adapters()
    frames = FrameSplitter(adapters).feed(truncated + valid_43)
    assert len(frames) == 1
    assert frames[0]["protocol"] == "双模4-3"
    assert frames[0]["raw"] == valid_43
