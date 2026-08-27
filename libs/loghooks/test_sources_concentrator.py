"""loghooks 第三来源 concentrator_10376 解析器测试（单 68 标准帧，ADR-44）。"""
from sim_concentrator.frame_codec import build_13762_frame, frame_to_hex
from loghooks.sources import get_parser, parse_concentrator_10376


def _frame_hex(afn=0x01, fn=1, seq=1, appdata=b""):
    raw = build_13762_frame(afn=afn, fn=fn, info={"seq": seq}, appdata=appdata)
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


def test_parse_timestamped_line():
    hx = _frame_hex(afn=0x02)
    line = f"[2026-08-13 10:00:00.123] [TX] {hx}"
    pl = parse_concentrator_10376(line)
    assert pl is not None
    assert pl.time is not None
    assert pl.metadata["frame_hex"] == hx


def test_parse_nested_645_fields():
    # 数据转发 AFN=02H F1：应用数据 = 协议类型(02) + 长度 + 645帧
    f645 = bytes.fromhex("6812345678901268910833333433AB896745CC16")
    appdata = bytes([0x02, len(f645)]) + f645
    raw = build_13762_frame(afn=0x02, fn=1, appdata=appdata)
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
