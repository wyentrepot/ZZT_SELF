"""厂测 A5 编解码单元测试（使用现场样帧）。"""

from __future__ import annotations

import pytest

from factory_test.codec import (
    FrameError,
    FrameStream,
    build_frame,
    crc32_mpeg2,
    parse_frame,
    parse_hex,
)
from factory_test.commands import (
    CTRL_READ,
    CTRL_WRITE,
    DI_ADDRESS,
    DI_ENTER_FACTORY,
    DI_HPLC_CALL_RESULT,
    DI_RESET,
    DI_RF_CALL_RESULT,
    DI_RF_CHANNEL,
    build_enter,
    build_read,
    build_reset,
    build_set_channel,
    decode_hplc_call_result,
    decode_rf_call_result,
    decode_version,
    describe_frame,
)


def test_crc32_mpeg2_known_values():
    cases = {
        "A5 FE 82 00": "33 5E 1D 78",
        "A5 FE 83 00": "EF 9F 04 AA",
        "A5 FE 10 02 00 01": "56 A3 8B 32",
        "A5 FE 10 02 01 00": "3D 7F 53 E4",
        "A5 FE 90 0A 01 00 55 55 55 55 55 55 00 00": "1F BA BA 7A",
        "A5 FE 90 03 00 01 29": "1A DA 00 3D",
        "A5 FE 01 02 05 00": "80 E9 0E 8A",
        "A5 FE 01 02 04 00": "5C 28 17 58",
    }
    for prefix, expected in cases.items():
        value = crc32_mpeg2(parse_hex(prefix))
        assert value.to_bytes(4, "little").hex(" ").upper() == expected


def test_build_known_frames():
    assert build_read(DI_RF_CHANNEL).hex(" ").upper() == "A5 FE 10 02 00 01 56 A3 8B 32"
    assert build_read(DI_ADDRESS).hex(" ").upper() == "A5 FE 10 02 01 00 3D 7F 53 E4"
    assert build_enter().hex(" ").upper() == "A5 FE 11 02 05 00 23 C4 1A 7D"
    assert build_reset().hex(" ").upper() == "A5 FE 11 02 04 00 FF 05 03 AF"
    assert build_set_channel(0x29).hex(" ").upper() == "A5 FE 11 03 00 01 29 2B BB 99 79"
    # 与现场抓包一致：写信道 50（0x32）
    assert build_set_channel(0x32).hex(" ").upper() == "A5 FE 11 03 00 01 32 3A AB C3 1E"


def test_parse_address_response():
    frame = parse_frame(parse_hex("A5 FE 90 0A 01 00 55 55 55 55 55 55 00 00 1F BA BA 7A"))
    assert frame.ctrl == 0x90
    assert frame.di == DI_ADDRESS
    assert frame.data == bytes.fromhex("55 55 55 55 55 55 00 00")
    assert frame.crc == 0x7ABABA1F
    info = describe_frame(frame)
    assert info["mac"] == "55:55:55:55:55:55"
    assert info["name"] == "地址/MAC"


def test_parse_channel_response():
    frame = parse_frame(parse_hex("A5 FE 90 03 00 01 29 1A DA 00 3D"))
    assert frame.ctrl == 0x90
    assert frame.di == DI_RF_CHANNEL
    assert frame.data == b"\x29"
    assert describe_frame(frame)["channel"] == 41


def test_write_ack_has_no_di():
    frame = parse_frame(parse_hex("A5 FE 82 00 33 5E 1D 78"))
    assert frame.ctrl == 0x82
    assert frame.di is None
    assert frame.data == b""
    assert frame.is_write_ok


def test_stream_resync_with_noise_and_partial():
    good = parse_hex("A5 FE 90 03 00 01 29 1A DA 00 3D")
    payload = b"\x00\x11\x22" + good[:5]
    stream = FrameStream()
    assert stream.feed(payload) == []
    frames = stream.feed(good[5:])
    assert len(frames) == 1
    assert frames[0].di == DI_RF_CHANNEL
    assert frames[0].data == b"\x29"


def test_describe_request_does_not_decode_payload():
    frame = parse_frame(parse_hex("A5 FE 10 02 00 01 56 A3 8B 32"))
    info = describe_frame(frame)
    assert info["direction"] == "request"
    assert "channel" not in info
    assert "decode_error" not in info


def test_bad_crc_is_rejected_and_stream_resyncs():
    bad = bytearray(parse_hex("A5 FE 90 03 00 01 29 1A DA 00 3D"))
    bad[-1] ^= 0xFF
    with pytest.raises(FrameError):
        parse_frame(bytes(bad))

    good = parse_hex("A5 FE 10 02 00 01 56 A3 8B 32")
    stream = FrameStream()
    frames = stream.feed(bytes(bad) + good)
    assert len(frames) == 1
    assert frames[0].di == DI_RF_CHANNEL
    assert frames[0].ctrl == CTRL_READ


def test_decode_call_results():
    rf = decode_rf_call_result(bytes((0x21, 0x00, 0x12, 0x0A, 0x0B)))
    assert rf == {"rx_rssi": 0x21, "freq_dev": 0, "tx_rssi": 0x12, "rx_snr": 10, "tx_snr": 11}

    hplc = decode_hplc_call_result(bytes((0x30, 0x08, 0x20, 0x07)))
    assert hplc == {"rx_rssi": 0x30, "rx_snr": 8, "tx_rssi": 0x20, "tx_snr": 7}


def test_decode_version_and_internal_version():
    version = decode_version(bytes.fromhex("01 01 01 01 00 00 00"))
    assert version["hversion"] == 0x0101
    assert version["sversion"] == 0x000101
    assert version["sversion_hex"] == "0x000101"


def test_build_frame_rejects_oversize_data():
    with pytest.raises(ValueError):
        build_frame(CTRL_WRITE, 0x010C, bytes(246))
