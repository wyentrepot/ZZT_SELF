"""Q/GDW 10376.2（1376.2）适配器单元测试：信封解析 + 递归嵌套 645/698。"""
import os

import pytest

from parser_lib.adapters.adapter_10376 import QGDW103762Adapter, build_frame
from parser_lib.adapters.adapter_698 import DLT69845Adapter

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")


def _load(name):
    return bytes.fromhex(open(os.path.join(FIX, name)).read().strip())


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
    return bytes.fromhex("6812345678901268910800000100123456783416")


def _build_relay(userdata, afn=0x02):
    rtsa = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])
    return build_frame(afn=afn, seq=0x01, rtsa=rtsa, msaa=0x01,
                       pw=0x0000, userdata=userdata)


def test_try_extract_and_confidence(adapter):
    frame = _build_relay(bytes([0x12, 0x34]) + _f645())
    assert adapter.try_extract(frame) is not None
    assert adapter.confidence(frame) == 1.0
    # 698 必须拒绝双 0x68 的 1376 帧（路由隔离）
    assert DLT69845Adapter().confidence(frame) == 0.0


def test_decode_envelope_and_nested_645(adapter):
    """加载已存 fixture：信封字段 + 内部 645 帧被递归解出。"""
    frame = _load("relay_645.hex")
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _field(fr, "AFN")
    assert afn is not None and "数据转发" in str(afn.value)
    rtsa = _field(fr, "终端地址RTUA")
    assert rtsa is not None and len(rtsa.value) == 12
    assert len(fr.nested) == 1
    n0 = fr.nested[0]
    assert n0.structure == "645"
    names = [(it.name, it.value) for it in n0.items]
    assert ("(当前)正向有功总电能", 123456.78) in names
    assert _item(fr, "10376.2网络层分支") is not None
    assert _item(fr, "数据单元标识DAD") is not None


def test_multi_frame_nesting(adapter):
    """用户数据含两帧 645 → 递归解出 2 个嵌套帧。"""
    userdata = bytes([0x12, 0x34]) + _f645() + _f645()
    frame = _build_relay(userdata)
    fr = adapter.decode(frame)
    assert len(fr.nested) == 2
    assert all(n.structure == "645" for n in fr.nested)


def test_dualmode_43_header_inside_route_data_forward(adapter):
    """AFN=13H 路由数据转发中识别双模4-3通用头与源/目的NA。"""
    dualmode = bytes.fromhex("1103000001020304") + _f645()
    frame = _build_relay(bytes([0x00, 0x01]) + dualmode, afn=0x13)
    fr = adapter.decode(frame)

    assert _item(fr, "路由类AFN").value == "路由数据转发"
    assert _item(fr, "4-3报文端口号").value == "0x11 (普通业务)"
    assert "终端主动并发抄表" in _item(fr, "4-3报文ID").value
    assert _item(fr, "4-3报文控制字").value == "0x00"
    assert _item(fr, "源NA").value == "0201"
    assert _item(fr, "目的NA").value == "0403"
    assert len(fr.nested) == 1
    assert fr.nested[0].structure == "645"


@pytest.mark.parametrize("afn,name", [
    (0x10, "路由查询"),
    (0x11, "路由设置"),
    (0x12, "路由控制"),
])
def test_route_afns_are_marked_as_network_branches(adapter, afn, name):
    frame = _build_relay(bytes.fromhex("0001AABBCC"), afn=afn)
    fr = adapter.decode(frame)

    assert name in _item(fr, "10376.2网络层分支").value
    assert _item(fr, "路由类AFN").value == name
    assert _item(fr, "网络/路由载荷(原始)").hex == "aabbcc"


def test_cs_failure_marks_warning(adapter):
    frame = bytearray(_build_relay(bytes([0x12, 0x34]) + _f645()))
    frame[-2] ^= 0xFF  # 破坏 CS
    frame = bytes(frame)
    fr = adapter.decode(frame)
    assert any("CS校验失败" in w for w in fr.warnings)
