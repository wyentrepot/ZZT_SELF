"""frame_codec 层单元测试：构帧→解析往返、嵌套 645/698、帧提取。"""
import os

import pytest

from sim_concentrator.frame_codec import (
    build_13762_frame,
    decode_frame,
    extract_frame,
    frame_to_hex,
    hex_to_bytes,
    scan_frame,
)

HERE = os.path.dirname(__file__)


def _f645():
    # 一条简单 645 帧（数据域带 0x0000 数据标识 + 4 字节数据）
    return bytes.fromhex("68 12 34 56 78 90 12 68 91 08 00 00 00 01 00 12 34 56 34 16".replace(" ", ""))


def test_build_roundtrip_basic():
    rtsa = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])
    raw = build_13762_frame(afn=0x01, seq=0x01, rtsa=rtsa, msaa=0x01,
                            pw=0x0000, userdata=b"\x00")
    assert raw[0] == 0x68 and raw[3] == 0x68 and raw[-1] == 0x16
    L = raw[1] | (raw[2] << 8)
    assert L == len(raw) - 2
    assert sum(raw[1:-2]) % 256 == raw[-2]  # CS 正确


def test_build_roundtrip_decode_envelope():
    rtsa = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])
    raw = build_13762_frame(afn=0x01, seq=0x01, rtsa=rtsa, msaa=0x01,
                            pw=0x0000, userdata=b"\x00")
    d = decode_frame(raw)
    assert d["structure"] == "1376.2"
    assert "AFN" in d["fields"]
    assert "数据转发" in str(d["fields"]["AFN"]["value"]) or "初始化" in str(d["fields"]["AFN"]["value"])
    # 终端地址展示为人读顺序（反转）：20 16 05 19 09 07 -> 07 09 19 05 16 20
    assert d["fields"]["终端地址RTUA"]["value"] == "070919051620"


def test_decode_nested_645():
    rtsa = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])
    # AFN=02H 数据转发：用户数据 = DAD(2B) + 645 帧
    userdata = bytes([0x00, 0x01]) + _f645()
    raw = build_13762_frame(afn=0x02, seq=0x01, rtsa=rtsa, msaa=0x01,
                            pw=0x0000, userdata=userdata)
    d = decode_frame(raw)
    assert len(d["nested"]) == 1
    assert d["nested"][0]["structure"] == "645"
    # DAD 应被解析出
    names = [it["name"] for it in d["items"]]
    assert "数据单元标识DAD" in names


def test_extract_frame_roundtrip_and_partial():
    raw = build_13762_frame(afn=0x01, seq=0x01,
                            rtsa=bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07]),
                            msaa=0x01, pw=0x0000, userdata=b"\x00")
    # 完整帧可切出
    got = extract_frame(raw)
    assert got == raw
    # 半包返回 None
    assert extract_frame(raw[:-3]) is None
    # 前有脏字节时，从脏字节后的 68 开始切
    stream = b"\xaa\xbb" + raw
    got2 = extract_frame(stream[2:])
    assert got2 == raw


def test_hex_conversions():
    raw = build_13762_frame(afn=0x01, seq=0x01,
                            rtsa=bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07]),
                            msaa=0x01, pw=0x0000, userdata=b"\x00")
    hx = frame_to_hex(raw)
    assert hx == " ".join(f"{b:02X}" for b in raw)
    assert hex_to_bytes(hx) == raw
    assert hex_to_bytes("") == b""


def test_scan_frame_skips_dirty_bytes():
    raw = build_13762_frame(afn=0x01, seq=0x01,
                            rtsa=bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07]),
                            msaa=0x01, pw=0x0000, userdata=b"\x00")
    # 前导脏字节 + 帧 + 半包
    frame, consumed = scan_frame(b"\xaa\xbb" + raw + raw[:5])
    assert frame == raw
    assert consumed == 2 + len(raw)
    # 无帧
    frame2, c2 = scan_frame(b"\xaa\xbb\xcc")
    assert frame2 is None and c2 == 0
