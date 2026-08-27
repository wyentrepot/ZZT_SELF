"""frame_codec 层单元测试：单 68 构帧→解析往返、嵌套 645/698、帧提取。"""
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


def _f698():
    # 一条简单 698.45 链路层帧（用 adapter_698 build 构造）
    from parser_lib.adapters.adapter_698 import build_frame as build_698
    return build_698(
        apdu=bytes.fromhex("85 01 02 00 00 00 00 01 00"),
        addr_bytes=bytes.fromhex("11 22 33 44 55 66"),
    )


def test_build_roundtrip_basic():
    raw = build_13762_frame(afn=0x01, fn=1, info={"seq": 1})
    assert raw[0] == 0x68 and raw[3] != 0x68 and raw[-1] == 0x16  # 单68
    L = raw[1] | (raw[2] << 8)
    assert L == len(raw)  # 单68：L = 整帧长
    assert sum(raw[3:-2]) % 256 == raw[-2]  # CS 正确（从控制域起）


def test_build_roundtrip_decode_envelope():
    raw = build_13762_frame(afn=0x01, fn=1, direction="down",
                            info={"seq": 1, "module_id": 1},
                            address={"src": "070919051620", "dst": "999999999999"})
    d = decode_frame(raw)
    assert d["structure"] == "1376.2"
    assert "AFN" in d["fields"]
    assert "初始化" in str(d["fields"]["AFN"]["value"])
    # 地址域 src/dst 解析
    assert "070919051620" in d["fields"]["地址域A"]["value"]
    assert "999999999999" in d["fields"]["地址域A"]["value"]


def test_decode_nested_645():
    # AFN=02H 数据转发 F1：应用数据 = 协议类型(02) + 报文长度 + 645帧
    appdata = bytes([0x02, len(_f645())]) + _f645()
    raw = build_13762_frame(afn=0x02, fn=1, appdata=appdata)
    d = decode_frame(raw)
    assert len(d["nested"]) == 1
    assert d["nested"][0]["structure"] == "645"
    # 通信协议类型应被解析
    names = [it["name"] for it in d["items"]]
    assert "通信协议类型" in names


def test_extract_frame_roundtrip_and_partial():
    raw = build_13762_frame(afn=0x01, fn=1, info={"seq": 1})
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
    raw = build_13762_frame(afn=0x01, fn=1, info={"seq": 1})
    hx = frame_to_hex(raw)
    assert hx == " ".join(f"{b:02X}" for b in raw)
    assert hex_to_bytes(hx) == raw
    assert hex_to_bytes("") == b""


def test_scan_frame_skips_dirty_bytes():
    raw = build_13762_frame(afn=0x01, fn=1, info={"seq": 1})
    # 前导脏字节 + 帧 + 半包
    frame, consumed = scan_frame(b"\xaa\xbb" + raw + raw[:5])
    assert frame == raw
    assert consumed == 2 + len(raw)
    # 无帧
    frame2, c2 = scan_frame(b"\xaa\xbb\xcc")
    assert frame2 is None and c2 == 0
