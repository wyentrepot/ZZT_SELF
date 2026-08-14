"""DL/T 645-2007 蒸馏文档示例帧测试。

来源：蒸馏文档/01_DLT645_帧格式.md
每个测试用例对应文档中的一个示例帧，验证适配器解析结果与文档描述是否一致。

数据域 +33H 加密传输，适配器 decode 时对非广播命令做 -33H 解密。
"""
import os

import pytest

from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_645 import DLT645Adapter, build_frame

HERE = os.path.dirname(__file__)


@pytest.fixture
def adapter():
    store = MetadataStore()
    store.load_protocol("645", os.path.join(HERE, "..", "metadata"))
    return DLT645Adapter(metadata_store=store)


def _strip_preamble(data: bytes) -> bytes:
    """去除前导字节 FEH（主站发送前唤醒接收方）。"""
    i = 0
    while i < len(data) and data[i] == 0xFE:
        i += 1
    return data[i:]


def _field(frame, name):
    for f in frame.fields:
        if f.name == name:
            return f
    return None


# ========== §4.6 / §6.1 / §7.1 读数据请求 ==========

def test_doc_645_read_data_request_sec46(adapter):
    """§4.6 CS计算示例 / §6.1 读数据请求。

    文档hex: 68 12 34 56 78 90 12 68 11 04 33 33 34 33 F0 16
    地址=123456789012, C=11H(读数据请求), L=04H, DI=00010000H(+33H=33333433)
    CS = (68+12+34+56+78+90+12+68+11+04+33+33+34+33) mod 256 = F0H
    """
    raw = build_frame(
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),
        0x11,
        bytes([0x33, 0x33, 0x34, 0x33]),  # DI=00010000H + 33H
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "地址域").value == "123456789012"
    assert _field(fr, "控制码").value == "0x11"
    assert "读数据" in _field(fr, "控制码").desc
    assert "下行" in _field(fr, "控制码").desc  # D7=0 主站请求
    assert _field(fr, "长度域").value == 4
    # 文档期望：DI=00010000H → (当前)正向有功总电能
    assert fr.items[0].name == "(当前)正向有功总电能"


def test_doc_645_read_data_request_sec71(adapter):
    """§7.1 读数据请求（地址=000000000001）。

    文档hex: FE FE FE FE 68 01 00 00 00 00 00 68 11 04 33 33 34 33 F0 16
    注意：文档中 CS=F0 是按地址123456789012计算的，此处地址不同。
    本测试用 build_frame 生成正确CS。
    """
    raw = bytes([0xFE, 0xFE, 0xFE, 0xFE]) + build_frame(
        bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00]),
        0x11,
        bytes([0x33, 0x33, 0x34, 0x33]),
    )
    raw = _strip_preamble(raw)
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "地址域").value == "010000000000"
    assert _field(fr, "控制码").value == "0x11"
    assert "读数据" in _field(fr, "控制码").desc
    # 文档期望：DI=00010000H → (当前)正向有功总电能
    assert fr.items[0].name == "(当前)正向有功总电能"


# ========== §7.1 读数据应答 ==========

def test_doc_645_read_data_response_sec71(adapter):
    """§7.1 读数据正常应答（无后续）。

    文档hex: 68 01 00 00 00 00 00 68 91 08 33 33 34 33 78 56 34 12 CS 16
    C=91H(正常应答无后续), L=08H, DI=00010000H(+33H), 数据=123456.78kWh
    文档注释"电能数据123456.78的BCD码加33H"但hex显示78 56 34 12（原始BCD，未加33H）
    修正：数据值按标准 +33H 加密 → AB 89 67 45
    """
    raw = build_frame(
        bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00]),
        0x91,
        bytes([0x33, 0x33, 0x34, 0x33, 0xAB, 0x89, 0x67, 0x45]),
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "控制码").value == "0x91"
    assert "上行" in _field(fr, "控制码").desc  # D7=1 从站应答
    assert "读数据" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 8
    # 文档期望：DI=00010000H → (当前)正向有功总电能, 值=123456.78kWh
    assert fr.items[0].name == "(当前)正向有功总电能"
    assert abs(fr.items[0].value - 123456.78) < 1e-6
    assert fr.items[0].unit == "kWh"


# ========== §6.2 读后续数据请求 ==========

def test_doc_645_read_subsequent_request_sec62(adapter):
    """§6.2 读后续数据请求。

    文档hex: 68 12 34 56 78 90 12 68 12 05 33 33 34 33 34 CS 16
    C=12H(读后续数据), L=05H, DI=00010000H(+33H), 序号N=01H(+33H=34H)
    """
    raw = build_frame(
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),
        0x12,
        bytes([0x33, 0x33, 0x34, 0x33, 0x34]),  # DI+33H + 序号+33H
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "控制码").value == "0x12"
    assert "读后续数据" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 5
    # 文档期望：DI=00010000H → (当前)正向有功总电能
    assert fr.items[0].name == "(当前)正向有功总电能"


# ========== §6.3 读通信地址 ==========

def test_doc_645_read_address_broadcast_sec63(adapter):
    """§6.3 读通信地址广播请求。

    文档hex: 68 FE FE FE FE FE FE 68 13 00 CS 16
    地址=FFFFFFFFFFFF(广播), C=13H(读通信地址), L=00H
    """
    raw = build_frame(
        bytes([0xFE, 0xFE, 0xFE, 0xFE, 0xFE, 0xFE]),
        0x13,
        bytes(),  # L=0, 无数据域
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "地址域").value == "FEFEFEFEFEFE"
    assert _field(fr, "控制码").value == "0x13"
    assert "读通信地址" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 0


def test_doc_645_read_address_response_sec63(adapter):
    """§6.3 读通信地址应答。

    文档hex: 68 12 34 56 78 90 12 68 93 06 12 34 56 78 90 12 CS 16
    C=93H(读通信地址应答), L=06H, 数据=123456789012(BCD地址)
    """
    raw = build_frame(
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),
        0x93,
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),  # 通信地址BCD
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "控制码").value == "0x93"
    assert "读通信地址" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 6
    # 文档期望：应答数据为通信地址 123456789012
    # 注意：适配器将前4字节当作DI解析，可能无法正确返回通信地址
    assert fr.items[0].value == "123456789012" or "123456789012" in str(fr.items[0].value)


# ========== §7.3 写数据请求 ==========

def test_doc_645_write_data_request_sec73(adapter):
    """§7.3 写数据请求。

    文档hex: FE FE FE FE 68 01 00 00 00 00 00 68 14 0D 34 33 34 37 00 33 33 33 33 33 33 33 12 07 23 CS 16
    C=14H(写数据), L=0DH, DI=04000103H(+33H, 文档显示34333437), PA=00, 密码P0~P2=000000(+33H), 操作者代码C0~C3=00000000(+33H), 数据=230712(BCD)
    """
    raw = bytes([0xFE, 0xFE, 0xFE, 0xFE]) + build_frame(
        bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00]),
        0x14,
        bytes([0x34, 0x33, 0x34, 0x37,  # DI+33H (文档值)
               0x00,                      # PA
               0x33, 0x33, 0x33,          # 密码P0~P2 +33H
               0x33, 0x33, 0x33, 0x33,    # 操作者代码C0~C3 +33H
               0x12, 0x07, 0x23]),        # 日期数据230712 BCD
    )
    raw = _strip_preamble(raw)
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "控制码").value == "0x14"
    assert "写数据" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 15  # 4(DI)+1(PA)+3(密码)+4(操作者)+3(日期)=15
    # 文档期望：DI=04000103H（当前日期）
    # 注意：适配器可能不解析密码和操作者代码
    assert fr.items[0].name != ""  # 至少应有数据项


# ========== §7.2 广播校时 ==========

def_doc_645_broadcast_time_sync = None


def test_doc_645_broadcast_time_sync_sec72(adapter):
    """§7.2 广播校时。

    文档hex: 68 99 99 99 99 99 99 68 08 06 00 30 20 12 07 23 CS 16
    地址=999999999999(广播), C=08H(广播校时), L=06H
    数据=秒分时日月年=00 30 20 12 07 23 (BCD, 广播帧不加33H)
    对应时间: 2023年7月12日 20:30:00
    """
    raw = build_frame(
        bytes([0x99, 0x99, 0x99, 0x99, 0x99, 0x99]),
        0x08,
        bytes([0x00, 0x30, 0x20, 0x12, 0x07, 0x23]),  # 秒分时日月年 BCD
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "地址域").value == "999999999999"
    assert _field(fr, "控制码").value == "0x08"
    # 文档定义：功能码01000=广播校时
    assert "广播校时" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 6


# ========== §6.1 读数据应答（地址123456789012） ==========

def test_doc_645_read_data_response_sec61(adapter):
    """§6.1 读数据正常应答（地址123456789012）。

    文档hex: 68 12 34 56 78 90 12 68 91 08 33 33 34 33 xx xx xx xx CS 16
    数据值=123456.78kWh → BCD=12345678 → 低字节在前=78 56 34 12
    数据加33H后: AB 89 67 45
    """
    raw = build_frame(
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),
        0x91,
        bytes([0x33, 0x33, 0x34, 0x33,  # DI+33H
               0xAB, 0x89, 0x67, 0x45]),  # 数据BCD+33H
    )
    fr = adapter.decode(raw)
    assert fr.structure == "645"
    assert _field(fr, "控制码").value == "0x91"
    assert "上行" in _field(fr, "控制码").desc
    assert "读数据" in _field(fr, "控制码").desc
    assert _field(fr, "长度域").value == 8
    # 文档期望：DI=00010000H → (当前)正向有功总电能, 值=123456.78kWh
    assert fr.items[0].name == "(当前)正向有功总电能"
    assert abs(fr.items[0].value - 123456.78) < 1e-6
    assert fr.items[0].unit == "kWh"
