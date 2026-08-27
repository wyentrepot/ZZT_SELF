"""Q/GDW 10376.2-2019 蒸馏文档示例帧测试（单 68 标准帧，ADR-44）。

来源：蒸馏文档/03_QGDW10376.2_全帧类型.md

ADR-44 重构后，适配器实现即文档标准单 68H 帧
（68H|L|C|R|A|AFN|DT1|DT2|应用数据|CS|16H），文档示例帧可直接解析。
本文件用 build_frame 构造单 68 帧，验证各 AFN 的解析（应用层语义与文档一致）。
"""
import os

import pytest

from parser_lib.adapters.adapter_10376 import QGDW103762Adapter, build_frame
from parser_lib.adapters.adapter_645 import DLT645Adapter
from parser_lib.adapters.adapter_645 import build_frame as build_645

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
    for it in frame.items:
        if it.name == name:
            return it
    return None


def _f645():
    """构造一个标准 645 读数据应答帧。"""
    return build_645(
        bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]),
        0x91,
        bytes([0x00, 0x00, 0x01, 0x00, 0x12, 0x34, 0x56, 0x78]),
    )


# ========== 单 68 标准帧（文档 AFN 语义） ==========

def test_doc_10376_confirm_frame(adapter):
    """§确认帧（AFN=00H）。"""
    frame = build_frame(afn=0x00, fn=1, direction="up", appdata=b"\x00" * 6)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "确认" in str(afn.value)


def test_doc_10376_init_frame(adapter):
    """§初始化帧（AFN=01H, F1），无应用数据。"""
    frame = build_frame(afn=0x01, fn=1, direction="down")
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "初始化" in str(afn.value)
    fn = _field(fr, "FN")
    assert fn is not None and fn.raw == 1


def test_doc_10376_data_relay_with_645(adapter):
    """§数据转发帧（AFN=02H, F1）— 含嵌套645帧。

    文档示例: 68 xx xx C0 00 00 00 00 00 00 02 01 00 02 0A [645] CS 16
    单 68 下：AFN=02H, F1, 应用数据 = 协议类型(02) + 长度(0A) + 645帧。
    """
    frame = build_frame(afn=0x02, fn=1, direction="down",
                        appdata=bytes([0x02, 0x0A]) + _f645())
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "数据转发" in str(afn.value)
    # 应递归解出嵌套 645 帧
    assert len(fr.nested) == 1
    assert fr.nested[0].structure == "645"
    assert _item(fr, "通信协议类型") is not None


def test_doc_10376_pause_route(adapter):
    """§暂停路由工作（AFN=12H 路由控制, F2）。"""
    frame = build_frame(afn=0x12, fn=2, direction="down")
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "路由控制" in str(afn.value)
    fn = _field(fr, "FN")
    assert fn is not None and fn.raw == 2


def test_doc_10376_concurrent_meter_reading(adapter):
    """§并发抄表（AFN=F1H）— 含 2 个嵌套 645 帧。"""
    frame = build_frame(afn=0xF1, fn=1, direction="down",
                        appdata=bytes([0x02, 0x0A]) + _f645() + _f645())
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "并发抄表" in str(afn.value)
    assert len(fr.nested) == 2
    assert all(n.structure == "645" for n in fr.nested)


def test_doc_10376_route_query(adapter):
    """§路由查询（AFN=10H, F1）。"""
    frame = build_frame(afn=0x10, fn=1, direction="down")
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "路由查询" in str(afn.value)


def test_doc_10376_hardware_init_raw_hex_parses(adapter):
    """文档原始单 68H 帧（初始化 F1）应能被直接解析。

    文档hex: 68 00 09 00 00 00 00 00 00 00 01 01 00 CS 16
    单 68 下 L 字段 = 帧长（此处按 build_frame 构造校验 L 一致性）。
    """
    raw = build_frame(afn=0x01, fn=1, direction="down")
    assert adapter.confidence(raw) == 1.0
    fr = adapter.decode(raw)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "初始化" in str(afn.value)
