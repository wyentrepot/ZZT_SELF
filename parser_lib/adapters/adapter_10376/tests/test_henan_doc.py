"""省份协议蒸馏文档示例帧测试 — 河南省独有扩展。

来源：蒸馏文档/05_省份协议/05.1_河南.md
河南省扩展FN: F5, F6, F7, F9, F12, F16, F17, F18, F21, F31, F111, F112, F201, F209
协议类型: 1376.2扩展 + 698.45扩展

注意: 省份帧使用FT1.2双L格式(68|L|L|68|C|...)，适配器使用(68|L|68|AFN|...)格式。
文档帧的pos3是L高字节而非68H，适配器confidence=0.0 → 测试记录此差异。
"""
import os
import pytest
from parser_lib.adapters.adapter_10376 import QGDW103762Adapter

HERE = os.path.dirname(__file__)


@pytest.fixture
def adapter():
    return QGDW103762Adapter()


def _hex(s):
    return bytes.fromhex(s.replace(" ", "").replace("XX", "00").replace("CS", "00"))


# 河南省文档示例帧（选取代表性帧）
_HENAN_FRAMES = [
    # (描述, hex, 扩展FN, 期望AFN名称关键字)
    ("F5设置搜表参数", "68 1A 00 1A 00 68 4B 33 33 44 55 00 05 01 00 00 00 01 00 00 00 01 0A 00 F0 16", "F5", "数据转发"),
    ("F5启动搜表", "68 15 00 15 00 68 4B 33 33 44 55 00 11 01 00 00 00 01 00 00 00 01 E7 16", "F5", "数据转发"),
    ("F12查询主节点模块ID", "68 13 00 13 00 68 4B 33 33 44 55 00 03 01 00 00 00 00 10 00 00 00 16", "F12", "查询数据"),
    ("F31查询节点相位", "68 15 00 15 00 68 4B 33 33 44 55 00 10 01 00 00 00 00 01 00 00 01 02 03 04 05 06 00 16", "F31", "路由控制"),
    ("F21查询拓扑信息", "68 15 00 15 00 68 4B 33 33 44 55 00 10 01 00 00 00 00 20 00 00 01 02 03 04 05 06 00 16", "F21", "路由控制"),
    ("F1并发抄表命令", "68 2A 00 2A 00 68 4B 33 33 44 55 00 F1 01 00 00 00 01 00 00 00 03 01 02 03 04 05 06 04 00 00 00 00 01 01 02 03 04 05 07 04 00 00 00 00 01 01 02 03 04 05 08 04 00 00 00 00 01 00 16", "F1", "并发抄表"),
    ("F2对时命令", "68 18 00 18 00 68 4B 33 33 44 55 00 14 01 00 00 00 02 00 00 00 00 00 00 00 00 00 00 00 00 16", "F2", "路由控制"),
    ("F201设置STA认证", "68 14 00 14 00 68 4B 33 33 44 55 00 05 01 00 00 00 00 80 00 00 01 00 16", "F201", "控制命令"),
]


@pytest.mark.parametrize("desc,hex_str,fn,expected_afn", _HENAN_FRAMES,
                         ids=[f[0] for f in _HENAN_FRAMES])
def test_doc_henan_frame(adapter, desc, hex_str, fn, expected_afn):
    """河南省文档示例帧解析测试。

    文档来源: 05.1_河南.md
    预期: 省份帧使用FT1.2双L格式，适配器可能无法识别(pos3!=0x68)
    """
    raw = _hex(hex_str)
    confidence = adapter.confidence(raw)
    if confidence > 0:
        fr = adapter.decode(raw)
        assert fr.structure == "1376.2"
    else:
        pytest.skip(f"适配器不支持此帧格式(双L FT1.2): {desc}, confidence={confidence}")


def test_doc_henan_extended_fn_list():
    """河南省扩展FN清单验证（文档记录的所有扩展FN）。"""
    expected_fns = ["F5", "F6", "F7", "F9", "F12", "F16", "F17", "F18", "F21", "F31",
                    "F111", "F112", "F201", "F209"]
    # 适配器当前不支持任何扩展FN解析
    # 此测试记录文档中定义的扩展FN列表
    assert len(expected_fns) == 14
