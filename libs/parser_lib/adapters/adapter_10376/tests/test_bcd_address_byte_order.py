"""BCD 地址字节序口径回归（2026-11-19 真机实证）。

背景：编码侧 `_bcd_bytes` 曾按「正序」写入，而读侧还原成「低字节在前」，
两边不对称，导致同一张表建档后查档显示成另一个地址（看起来像多了几条档案）。

真机证据链（模拟电表 + CCO 实测）：
    起点：模拟电表 brain 地址 000000000088
    建档 11H-F1 帧内地址字节 = 88 00 00 00 00 00  ->  确认帧
    查档 10H-F2 回读展示    = 000000000088        ->  与表地址一致
    抄表 02H-F1 地址域      = 000000000088        ->  success=1（表侧真应答）

历史真机佐证（2026-10-27 台架，组网成功）：
    人读地址 201807240099 的帧内字节 = 99 00 24 07 18 20

非对称地址（能区分反转方向，避免回文地址造成的假通过）：
    102030405060 <-> 60 50 40 30 20 10（已真机建档+查档+抄表 success=1）
"""
from __future__ import annotations

import pytest

from parser_lib.adapters.adapter_10376 import (
    _bcd_addr_to_str,
    _bcd_bytes,
    encode_app_data,
)


# 真机实证的 (人读地址, 帧内字节) 对照
VERIFIED = [
    ("000000000088", "880000000000"),   # 2026-11-19 模拟电表实测
    ("102030405060", "605040302010"),   # 2026-11-19 非对称地址实测
    ("201807240099", "990024071820"),   # 2026-10-27 台架组网帧
]


@pytest.mark.parametrize("human,wire", VERIFIED)
def test_bcd_bytes_low_byte_first(human, wire):
    """人读地址 -> 帧内字节：低字节在前（整字节反序）。"""
    assert _bcd_bytes(human).hex() == wire


@pytest.mark.parametrize("human,wire", VERIFIED)
def test_bcd_bytes_roundtrip(human, wire):
    """编解码严格互逆：帧内字节 -> 展示地址 == 原始人读地址。"""
    assert _bcd_addr_to_str(bytes.fromhex(wire)) == human


@pytest.mark.parametrize("human,wire", VERIFIED)
def test_encode_decode_symmetry(human, wire):
    """编码与解码使用同一口径，不会出现「建档后查档变成另一个地址」。"""
    assert _bcd_bytes(_bcd_addr_to_str(bytes.fromhex(wire))).hex() == wire


def test_11f1_frame_bytes_match_field_session():
    """11H-F1 添加 000000000088 的整帧，与 2026-11-19 真机发送帧一致。"""
    out = encode_app_data(0x11, 1, {"nodes": [{"addr": "000000000088", "protocol": 2}]})
    # 数量1 + 地址6B(低字节在前) + 协议02
    assert out == bytes.fromhex("01 88 00 00 00 00 00 02")


def test_non_palindromic_addr_guards_against_wrong_direction():
    """非回文地址必须能分辨方向：若退化成直序，本用例立刻失败。"""
    raw = bytes.fromhex("102030405060")
    assert _bcd_bytes("102030405060") != raw, "地址必须反序写入，否则与读侧不对称"
    assert _bcd_bytes("102030405060") == raw[::-1]


def test_time_bcd_not_reversed():
    """时间类 BCD 不受影响（秒分时日月年本身就是低字节在前）。"""
    from parser_lib.adapters.adapter_10376 import _bcd_to_str
    # 时间解码保持原样，未被地址口径污染
    assert _bcd_to_str(bytes.fromhex("001122334455")) == "001122334455"
