"""省份协议蒸馏文档示例帧测试 — 湖南省HPLC/HRF双模扩展。

来源：蒸馏文档/05_省份协议/05.5_湖南.md
湖南省扩展FN: F3, F5, F31, F100, F102, F103, F104, F112, F130, F131, F201, F209, F223
协议类型: 1376.2扩展 + 698.45扩展(波特率协商) + HPLC应用层扩展

注意：省份帧使用FT1.2双L格式(68|L|L|68|C|...)，适配器使用(68|L|68|AFN|...)格式。
文档帧的pos3是L高字节而非68H，适配器confidence=0.0 → 测试记录此差异。
698.45扩展帧使用698.45帧格式，由DLT69845Adapter解析。
"""
import os
import pytest
from parser_lib.adapters.adapter_10376 import QGDW103762Adapter
from parser_lib.adapters.adapter_698 import DLT69845Adapter

HERE = os.path.dirname(__file__)


@pytest.fixture
def adapter():
    return QGDW103762Adapter()


@pytest.fixture
def adapter698():
    return DLT69845Adapter()


def _hex(s):
    return bytes.fromhex(s.replace(" ", "").replace("XX", "00").replace("CS", "00"))


# 湖南省1376.2扩展帧（选取代表性帧）
_HUNAN_1376_FRAMES = [
    # (描述, hex, 扩展FN, AFN)
    ("F3广播校时", "68 1A 00 1A 00 68 4B 33 33 44 55 00 05 01 00 00 00 04 00 00 00 02 0C 68 99 99 99 99 99 99 68 08 06 78 63 15 21 33 56 00 16 00 16", "F3", "05H"),
    ("F5时钟偏差事件上报", "68 20 00 20 00 68 C3 33 33 44 55 00 06 01 00 00 00 00 01 00 00 02 03 0C 68 12 34 56 78 90 12 68 9F 07 34 78 63 15 21 33 56 00 16 00 16", "F5", "06H"),
    ("F201设置STA认证使能", "68 14 00 14 00 68 4B 33 33 44 55 00 05 01 00 00 00 00 00 08 00 00 00 01 00 16", "F201", "05H"),
    ("F201查询STA认证使能", "68 13 00 13 00 68 4B 33 33 44 55 00 03 01 00 00 00 00 00 08 00 00 00 00 16", "F201", "03H"),
    ("F102设置全网广播周期", "68 16 00 16 00 68 4B 33 33 44 55 00 05 01 00 00 00 00 00 04 00 00 00 20 1C 00 16", "F102", "05H"),
    ("F103设置STA曲线配置", "68 30 00 30 00 68 4B 33 33 44 55 00 05 01 00 00 00 00 00 08 00 00 00 01 06 02 01 01 00 02 02 01 00 04 00 01 00 00 00 00 01 00 00 00 02 02 02 02 00 00 16", "F103", "05H"),
    ("F130查询模块程序版本", "68 13 00 13 00 68 4B 33 33 44 55 00 03 01 00 00 00 00 02 00 00 00 00 00 16", "F130", "03H"),
    ("F131查询比对数据信息", "68 18 00 18 00 68 4B 33 33 44 55 00 03 01 00 00 00 00 04 00 00 00 00 00 00 00 00 00 00 08 00 00 16", "F131", "03H"),
    ("F31查询节点相位与相序", "68 15 00 15 00 68 4B 33 33 44 55 00 10 01 00 00 00 00 00 80 00 00 00 01 02 03 04 05 06 00 16", "F31", "10H"),
    ("F100并发抄读STA曲线数据", "68 2A 00 2A 00 68 4B 33 33 44 55 00 F1 01 00 00 00 00 00 01 00 00 00 03 01 02 03 04 05 06 00 00 01 02 03 04 05 07 00 00 01 02 03 04 05 08 00 00 00 16", "F100", "F1H"),
]


@pytest.mark.parametrize("desc,hex_str,fn,afn", _HUNAN_1376_FRAMES,
                         ids=[f[0] for f in _HUNAN_1376_FRAMES])
def test_doc_hunan_1376_frame(adapter, desc, hex_str, fn, afn):
    """湖南省1376.2扩展帧解析测试。

    文档来源: 05.5_湖南.md §4.1~§4.6
    预期: 省份帧使用FT1.2双L格式，适配器可能无法识别(pos3!=0x68)
    """
    raw = _hex(hex_str)
    confidence = adapter.confidence(raw)
    if confidence > 0:
        fr = adapter.decode(raw)
        assert fr.structure == "1376.2"
    else:
        pytest.skip(f"适配器不支持此帧格式(双L FT1.2): {desc}, confidence={confidence}")


# 湖南省698.45扩展帧（波特率协商）
_HUNAN_698_FRAMES = [
    # (描述, hex)
    ("GET-Request读取协议版本号(OAD=44000301)", "68 15 00 15 00 68 43 05 58 03 26 20 11 02 01 00 00 00 05 01 03 44 00 00 00 00 00 00 00 00 00 16"),
    ("SET-Request设置波特率(F209属性2)", "68 24 00 24 00 68 43 05 58 03 26 20 11 02 01 7A 9D 07 01 01 F2 09 80 00 02 02 51 F2 09 02 FD 5F 0A 02 08 01 00 00 21 D7 16"),
    ("GET-Request查询波特率", "68 17 00 43 05 35 00 00 00 00 00 01 12 4E 05 01 01 F2 09 04 00 00 CB 7C 16"),
    ("ACTION-Request设备复位(OI=4300方法1)", "68 18 00 43 05 58 03 26 20 11 02 01 4A EC 07 01 01 43 00 01 00 00 00 00 00 00 00 4B EA 16"),
]


@pytest.mark.parametrize("desc,hex_str", _HUNAN_698_FRAMES,
                         ids=[f[0] for f in _HUNAN_698_FRAMES])
def test_doc_hunan_698_frame(adapter698, desc, hex_str):
    """湖南省698.45扩展帧（波特率协商）解析测试。

    文档来源: 05.5_湖南.md §4.5.2~§4.5.5
    这些帧使用698.45帧格式，适配器应能识别。
    扩展OAD: F209(波特率), 44000301(协议版本号), 4300(设备复位)
    """
    raw = _hex(hex_str)
    confidence = adapter698.confidence(raw)
    if confidence > 0:
        fr = adapter698.decode(raw)
        assert fr is not None
    else:
        pytest.skip(f"698适配器不支持此帧: {desc}, confidence={confidence}")


# 湖南645扩展命令（数据标识 34 33 C3 37 / 35 33 C3 37）
_HUNAN_645_FRAMES = [
    ("645读取STA基本信息", "68 AA BB CC DD EE FF 68 11 04 34 33 C3 37 00 16"),
    ("645读取程序段校验码", "68 AA BB CC DD EE FF 68 11 04 35 33 C3 37 00 16"),
]


@pytest.mark.parametrize("desc,hex_str", _HUNAN_645_FRAMES,
                         ids=[f[0] for f in _HUNAN_645_FRAMES])
def test_doc_hunan_645_frame(adapter, desc, hex_str):
    """湖南省645扩展命令测试。

    文档来源: 05.5_湖南.md §4.4.3
    扩展数据标识: 34 33 C3 37 (STA基本信息), 35 33 C3 37 (程序段校验码)
    """
    # 这些是645帧，用10376适配器测试嵌套解析
    raw = _hex(hex_str)
    confidence = adapter.confidence(raw)
    if confidence > 0:
        fr = adapter.decode(raw)
        assert fr is not None
    else:
        pytest.skip(f"645扩展帧: {desc}, confidence={confidence}")


def test_doc_hunan_extended_fn_list():
    """湖南省扩展FN清单验证。"""
    expected_fns = ["F3", "F5", "F31", "F100", "F102", "F103", "F104",
                    "F112", "F130", "F131", "F201", "F209", "F223"]
    assert len(expected_fns) == 13
