"""loghooks 第三来源 concentrator_10376 解析器测试（原占位 → 真实现）。"""
from sim_concentrator.frame_codec import build_13762_frame, frame_to_hex
from loghooks.sources import get_parser, parse_concentrator_10376

RTSA = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])  # 展示: 070919051620


def _frame_hex(afn=0x01, seq=0x01, userdata=b"\x00"):
    raw = build_13762_frame(afn=afn, seq=seq, rtsa=RTSA, msaa=0x01,
                            pw=0x0000, userdata=userdata)
    return frame_to_hex(raw)


def test_registry_has_concentrator():
    parser = get_parser("concentrator_10376")
    assert parser is not None
    assert parser is parse_concentrator_10376


def test_parse_plain_hex_line():
    hx = _frame_hex(afn=0x01)
    pl = parse_concentrator_10376(hx)
    assert pl is not None
    assert pl.source == "concentrator_10376"
    assert pl.direction == "TX"
    assert pl.fields is not None
    assert pl.fields["structure"] == "1376.2"
    assert pl.fields["fields"]["AFN"]["raw"] == 0x01
    assert pl.fields["fields"]["终端地址RTUA"]["value"] == "070919051620"


def test_parse_timestamped_line():
    hx = _frame_hex(afn=0x02)
    line = f"[2026-08-13 10:00:00.123] [TX] {hx}"
    pl = parse_concentrator_10376(line)
    assert pl is not None
    assert pl.time is not None
    assert pl.metadata["frame_hex"] == hx


def test_parse_nested_645_fields():
    f645 = bytes.fromhex("6812345678901268910800000100123456783416")
    userdata = bytes([0x00, 0x01]) + f645
    raw = build_13762_frame(afn=0x02, seq=0x01, rtsa=RTSA, msaa=0x01,
                            pw=0x0000, userdata=userdata)
    hx = frame_to_hex(raw)
    pl = parse_concentrator_10376(hx)
    assert pl is not None
    assert len(pl.fields["nested"]) == 1
    assert pl.fields["nested"][0]["structure"] == "645"


def test_parse_rejects_non_13762():
    assert parse_concentrator_10376("7E 68 00 00 00 7E") is None
    assert parse_concentrator_10376("not a frame") is None


def test_adapter_callback_override():
    called = []

    def cb(frame_hex):
        called.append(frame_hex)
        return {"simple": {"AFN": "0x01"}}

    hx = _frame_hex(afn=0x01)
    pl = parse_concentrator_10376(hx, adapter_callback=cb)
    assert called == [hx]
    assert pl.fields == {"simple": {"AFN": "0x01"}}
