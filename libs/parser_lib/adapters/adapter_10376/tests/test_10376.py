"""Q/GDW 10376.2（1376.2）适配器单元测试（单 68 标准帧，ADR-44）。

覆盖：信封解析（C/R/A/AFN/FN/CS）+ 递归嵌套 645/698 + 应用数据解析。
"""
import os

import pytest

from parser_lib.adapters.adapter_10376 import QGDW103762Adapter, build_frame
from parser_lib.adapters.adapter_698 import DLT69845Adapter

HERE = os.path.dirname(__file__)


@pytest.fixture
def adapter():
    return QGDW103762Adapter()


def _field(frame, name):
    for f in frame.fields:
        if f.name == name:
            return f
    return None


def _item(frame, name):
    for f in frame.items:
        if f.name == name:
            return f
    return None


def _f645():
    """标准 645 读数据应答帧（数据域已 +33H 加密，decode 可正确解析）。

    对应逻辑帧：68|12 34 56 78 90 12|68|91|08|00 00 01 00 78 56 34 12|CS|16
    传输加密后：数据域 00 00 01 00 78 56 34 12 → 33 33 34 33 AB 89 67 45
    （DI 00 00 01 00 解密后命中"(当前)正向有功总电能"，数据 78 56 34 12 → 123456.78）
    """
    return bytes.fromhex("6812345678901268910833333433AB896745CC16")


def _build_relay(appdata, afn=0x02, fn=1):
    """构造单 68 数据转发帧（AFN=02H 数据转发 / 13H 路由数据转发等）。"""
    return build_frame(afn=afn, fn=fn, direction="down", appdata=appdata)


def test_try_extract_and_confidence(adapter):
    frame = _build_relay(bytes([0x02, len(_f645())]) + _f645())
    assert adapter.try_extract(frame) is not None
    assert adapter.confidence(frame) == 1.0
    # 698 必须拒绝单 68 的 1376 帧（第3字节非 0x68 的结构区分）
    assert DLT69845Adapter().confidence(frame) == 0.0


def test_decode_envelope_and_nested_645(adapter):
    """信封字段 + 内部 645 帧被递归解出（单 68）。"""
    appdata = bytes([0x02, len(_f645())]) + _f645()
    frame = _build_relay(appdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _field(fr, "AFN")
    assert afn is not None and "数据转发" in str(afn.value)
    assert _field(fr, "长度L") is not None
    assert _field(fr, "控制域C") is not None
    assert _field(fr, "信息域R") is not None
    assert _field(fr, "FN") is not None
    assert len(fr.nested) == 1
    n0 = fr.nested[0]
    assert n0.structure == "645"
    names = [(it.name, it.value) for it in n0.items]
    assert ("(当前)正向有功总电能", 123456.78) in names
    # 通信协议类型被解析（AFN=02 数据转发）
    assert _item(fr, "通信协议类型") is not None


def test_multi_frame_nesting(adapter):
    """应用数据含两帧 645 → 递归解出 2 个嵌套帧。"""
    appdata = bytes([0x02, len(_f645()) * 2]) + _f645() + _f645()
    frame = _build_relay(appdata)
    fr = adapter.decode(frame)
    assert len(fr.nested) == 2
    assert all(n.structure == "645" for n in fr.nested)


def test_up_direction_control(adapter):
    """上行帧（DIR=1）控制域/信息域正确。"""
    frame = build_frame(afn=0x06, fn=1, direction="up", info={"seq": 7},
                        appdata=bytes([1]) + bytes.fromhex("123456789012") + bytes([2, 0, 1]))
    fr = adapter.decode(frame)
    ctl = _field(fr, "控制域C")
    assert ctl is not None and "DIR=1" in str(ctl.value)
    info = _field(fr, "信息域R")
    assert info is not None and "seq=7" in str(info.desc)
    # 主动上报 F1 应用解析
    assert _item(fr, "上报从节点数量") is not None
    assert _item(fr, "从节点1地址") is not None


def test_address_domain(adapter):
    """module_id=1 时地址域 A（src + dst）正确解析。"""
    frame = build_frame(
        afn=0x03, fn=1, direction="down",
        info={"module_id": 1, "relay_level": 0},
        address={"src": "010203040506", "dst": "999999999999"},
    )
    fr = adapter.decode(frame)
    addr = _field(fr, "地址域A")
    assert addr is not None
    assert "010203040506" in addr.value
    assert "999999999999" in addr.value


@pytest.mark.parametrize("afn,name", [
    (0x10, "路由查询"),
    (0x11, "路由设置"),
    (0x12, "路由控制"),
    (0x13, "路由数据转发"),
])
def test_route_afns_recognized(adapter, afn, name):
    """路由类 AFN 被识别并保留应用数据（未识别内容不丢）。"""
    frame = _build_relay(bytes.fromhex("AABBCC"), afn=afn)
    fr = adapter.decode(frame)
    assert name in str(_field(fr, "AFN").value)


def test_cs_failure_marks_warning(adapter):
    frame = bytearray(_build_relay(bytes([0x02, len(_f645())]) + _f645()))
    frame[-2] ^= 0xFF  # 破坏 CS
    frame = bytes(frame)
    fr = adapter.decode(frame)
    assert any("CS校验失败" in w for w in fr.warnings)
