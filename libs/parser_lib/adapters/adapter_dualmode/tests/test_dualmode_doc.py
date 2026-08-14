"""双模4-3应用层通信协议蒸馏文档示例帧测试。

来源：蒸馏文档/04_双模4-3_应用层通信协议.md

文档中无完整hex帧，仅有6个概念性报文结构描述。
本测试根据文档字段定义构造测试帧，验证适配器对4-3通用报文头的解析能力。

帧格式: 报文端口号(1B) + 报文ID(2B,小端) + 报文控制字(1B) + 业务报文(NB)
端口号: 0x11=普通业务, 0x12=升级业务, 0x1A=鉴权安全
安全机制: 报文ID高4位(0=明文,1=机密性,2=完整性,3=全面保护)
"""
import os

import pytest

from parser_lib.adapters.adapter_dualmode import DualMode43Adapter
from parser_lib.adapters.adapter_645 import build_frame as build_645

HERE = os.path.dirname(__file__)


def _field(frame, name):
    for f in frame.fields:
        if f.name == name:
            return f
    return None


def _f645():
    """构造一个标准 645 读数据应答帧。"""
    return build_645(
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),
        0x91,
        bytes([0x00, 0x00, 0x01, 0x00, 0x12, 0x34, 0x56, 0x78]),
    )


def _build_43(port, msg_id, control=0x00, business=b""):
    """构造双模4-3帧: 端口号 + 报文ID(小端) + 控制字 + 业务报文。"""
    header = bytes([port, msg_id & 0xFF, (msg_id >> 8) & 0xFF, control])
    return header + business


def _meter_business(proto_type, data, seq=0x0001, timeout=0x0A):
    """构造抄表类业务报文头（协议版本号=1, 报文头长度=8）+ DATA。"""
    ver = 1
    header_len = 8
    b0 = ver | ((header_len & 0x03) << 6)
    b1 = (header_len >> 2) & 0x0F
    b2 = (proto_type & 0x0F) | (((len(data) >> 8) & 0x0F) << 4)
    b3 = len(data) & 0xFF
    return bytes([b0, b1, b2, b3, seq & 0xFF, (seq >> 8) & 0xFF, timeout, 0x00]) + data


@pytest.fixture
def adapter():
    return DualMode43Adapter()


# ========== §终端主动抄表 (msg_id=0x0001) ==========

def test_doc_dualmode_meter_reading(adapter):
    """终端主动抄表（报文ID=0x0001）。

    文档定义: 端口号=0x11(普通业务), 报文ID=0x0001(终端主动抄表)
    业务报文含抄表报文头 + 规约类型(645/698) + DATA 内嵌帧
    """
    raw = _build_43(0x11, 0x0001, business=_meter_business(2, _f645()))
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    assert _field(frame, "报文端口号").value == "0x11 (普通业务)"
    assert "终端主动抄表" in _field(frame, "报文ID").value
    assert _field(frame, "报文控制字").value == "0x00"
    assert _field(frame, "转发数据规约类型").raw == 2
    assert _field(frame, "源NA") is None
    assert _field(frame, "目的NA") is None
    assert len(frame.nested) == 1
    assert frame.nested[0].structure == "645"


# ========== §终端主动并发抄表 (msg_id=0x0003) ==========

def test_doc_dualmode_concurrent_meter_reading(adapter):
    """终端主动并发抄表（报文ID=0x0003）。

    文档定义: 端口号=0x11(普通业务), 报文ID=0x0003(终端主动并发抄表)
    """
    raw = _build_43(0x11, 0x0003, business=_meter_business(2, _f645()))
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    assert "终端主动并发抄表" in _field(frame, "报文ID").value
    assert _field(frame, "转发数据规约类型").raw == 2
    assert len(frame.nested) == 1


# ========== §校时 (msg_id=0x0004) ==========

def test_doc_dualmode_time_sync(adapter):
    """校时（报文ID=0x0004）。

    文档定义: 端口号=0x11(普通业务), 报文ID=0x0004(校时)
    """
    raw = _build_43(0x11, 0x0004, business=b"\x07\xE0\x01\x14\x10\x1B\x0B")
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    assert "校时" in _field(frame, "报文ID").value


# ========== §事件上报 (msg_id=0x0008) ==========

def test_doc_dualmode_event_report(adapter):
    """事件上报（报文ID=0x0008）。

    文档定义: 端口号=0x11(普通业务), 报文ID=0x0008(事件上报)
    """
    raw = _build_43(0x11, 0x0008, business=b"\x00\x01\x00\x02")
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    assert "事件上报" in _field(frame, "报文ID").value


# ========== §查询从节点主动注册 (msg_id=0x0011) ==========

def test_doc_dualmode_query_registration(adapter):
    """查询从节点主动注册（报文ID=0x0011）。

    文档定义: 端口号=0x11(普通业务), 报文ID=0x0011(查询从节点主动注册)
    """
    raw = _build_43(0x11, 0x0011, business=b"\x00")
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    assert "查询从节点主动注册" in _field(frame, "报文ID").value


# ========== §鉴权安全 (msg_id=0x00A0, port=0x1A) ==========

def test_doc_dualmode_auth_security(adapter):
    """鉴权安全（报文ID=0x00A0, 端口号=0x1A）。

    文档定义: 端口号=0x1A(鉴权安全), 报文ID=0x00A0(鉴权安全)
    """
    raw = _build_43(0x1A, 0x00A0, business=b"\x00" * 16)
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    assert _field(frame, "报文端口号").value == "0x1A (鉴权安全)"
    assert "鉴权安全" in _field(frame, "报文ID").value


# ========== §安全机制验证 ==========

def test_doc_dualmode_security_plaintext(adapter):
    """安全机制: 明文传输（报文ID高4位=0）。"""
    raw = _build_43(0x11, 0x0001, business=_meter_business(2, _f645()))  # msg_id=0x0001, security=0
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
    # 报文ID描述中应包含"明文传输"


def test_doc_dualmode_security_confidentiality(adapter):
    """安全机制: 数据机密性保护（报文ID高4位=1）。"""
    raw = _build_43(0x11, 0x1001, business=_meter_business(2, _f645()))  # msg_id=0x1001, security=1
    frame = adapter.decode(raw)
    assert frame.structure == "双模4-3"
