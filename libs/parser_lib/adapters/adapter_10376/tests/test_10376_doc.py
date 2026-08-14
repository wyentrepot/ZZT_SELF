"""Q/GDW 10376.2-2019 蒸馏文档示例帧测试。

来源：蒸馏文档/03_QGDW10376.2_全帧类型.md

注意：10376.2-2019 文档帧格式为单68H（68H|L|C|用户数据|CS|16H），
适配器实现的是双68H格式（68H|L|68H|AFN|SEQ|RTUA|MSAA|PW|数据|CS|16H）。
文档示例帧直接用 adapter.decode() 会因 pos3!=0x68 而被拒绝——此差异记录在失败清单中。
本测试文件同时使用 build_frame() 构造双68H格式帧，验证适配器对相同AFN/数据的解析能力。
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


_RTUA = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x01])


# ========== 文档原始帧格式测试（单68H，预期被适配器拒绝） ==========

def test_doc_10376_confirm_frame_raw_hex(adapter):
    """§确认帧：文档原始hex（单68H格式）。

    文档hex: 68 00 0E 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00 16
    文档说明: L=0x000E(14), C=00H, AFN=00H(确认/否认)

    预期：适配器要求 pos3=0x68，文档帧 pos3=0x00 → confidence=0.0
    """
    raw = bytes.fromhex("68000E00000000000000000000000000010000000016")
    assert adapter.confidence(raw) == 0.0


def test_doc_10376_hardware_init_raw_hex(adapter):
    """§初始化帧：文档原始hex（单68H格式）。

    文档hex: 68 00 09 00 00 00 00 00 00 00 01 01 00 CS 16
    文档说明: AFN=01H(初始化), DT1=01H(F1), DT2=00H

    预期：适配器要求 pos3=0x68，文档帧 pos3=0x00 → confidence=0.0
    """
    raw = bytes.fromhex("68000900000000000000010100CS16".replace("CS", "0B"))
    assert adapter.confidence(raw) == 0.0


# ========== 双68H格式帧测试（使用 build_frame 构造） ==========

def test_doc_10376_confirm_frame_compat(adapter):
    """§确认帧（AFN=00H）— 双68H兼容格式。

    文档AFN=00H(确认/否认)，使用 build_frame 构造双68H帧。
    """
    frame = build_frame(afn=0x00, seq=0x00, rtsa=_RTUA, msaa=0x01,
                        pw=0x0000, userdata=b"")
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "确认" in str(afn.value)


def test_doc_10376_init_frame_compat(adapter):
    """§初始化帧（AFN=01H, F1）— 双68H兼容格式。

    文档AFN=01H(初始化), DT1=01H(F1), DT2=00H
    """
    userdata = bytes([0x01, 0x00])  # DT1=01H, DT2=00H
    frame = build_frame(afn=0x01, seq=0x00, rtsa=_RTUA, msaa=0x01,
                        pw=0x0000, userdata=userdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "初始化" in str(afn.value)


def test_doc_10376_data_relay_with_645(adapter):
    """§数据转发帧（AFN=02H, F1）— 含嵌套645帧。

    文档描述: AFN=02H(数据转发), 用户数据含 DAD + 645帧
    文档示例: 68 xx xx C0 00 00 00 00 00 00 02 01 00 02 0A 68 11 11 11 11 11 11 68 91 0D 33 33 33 33 CS 16
    """
    # 构造数据转发帧，用户数据 = DAD(2B) + 645帧
    userdata = bytes([0x02, 0x0A]) + _f645()
    frame = build_frame(afn=0x02, seq=0x01, rtsa=_RTUA, msaa=0x01,
                        pw=0x0000, userdata=userdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "数据转发" in str(afn.value)
    # 应递归解出嵌套 645 帧
    assert len(fr.nested) == 1
    assert fr.nested[0].structure == "645"


def test_doc_10376_pause_route_compat(adapter):
    """§暂停路由工作（AFN=12H, F2）— 双68H兼容格式。

    文档hex: 68 00 09 00 00 00 00 00 00 00 12 04 00 CS 16
    文档说明: AFN=12H(路由控制), DT1=04H(D1位=F2), DT2=00H
    """
    userdata = bytes([0x04, 0x00])  # DT1=04H, DT2=00H
    frame = build_frame(afn=0x12, seq=0x00, rtsa=_RTUA, msaa=0x01,
                        pw=0x0000, userdata=userdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "路由控制" in str(afn.value)
    assert _item(fr, "10376.2网络层分支") is not None


def test_doc_10376_concurrent_meter_reading(adapter):
    """§并发抄表（AFN=F1H）— 含2个嵌套645帧。

    文档描述: AFN=F1H(并发抄表), 用户数据含多个645帧
    """
    userdata = bytes([0x02, 0x0A]) + _f645() + _f645()
    frame = build_frame(afn=0xF1, seq=0x01, rtsa=_RTUA, msaa=0x01,
                        pw=0x0000, userdata=userdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    afn = _item(fr, "AFN")
    assert afn is not None and "并发抄表" in str(afn.value)
    assert len(fr.nested) == 2
    assert all(n.structure == "645" for n in fr.nested)


def test_doc_10376_route_query_compat(adapter):
    """§路由查询（AFN=10H）— 双68H兼容格式。"""
    userdata = bytes([0x01, 0x00])  # DT1=01H(F1), DT2=00H
    frame = build_frame(afn=0x10, seq=0x00, rtsa=_RTUA, msaa=0x01,
                        pw=0x0000, userdata=userdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    assert _item(fr, "10376.2网络层分支") is not None
    assert "路由查询" in _item(fr, "10376.2网络层分支").value
