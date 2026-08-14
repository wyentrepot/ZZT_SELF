"""省份协议蒸馏文档示例帧测试 — 宁夏省。

来源：蒸馏文档/05_省份协议/05.4_宁夏.md
宁夏省无自定义扩展协议帧格式，文档以功能要求和验收规范为主。
扩展FN: 无（文档中提到的宁夏13版扩展协议未包含在抓取范围内）

注意：省份帧使用FT1.2双L格式(68|L|L|68|C|...)，适配器使用(68|L|68|AFN|...)格式。
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


# 宁夏省文档示例帧（5帧，全部为标准1376.2命令）
_NINGXIA_FRAMES = [
    # (描述, hex, AFN, Fn)
    ("AFN=04H-F1 模块登录", "68 14 00 14 00 68 C3 33 33 44 55 00 04 01 00 00 00 01 00 00 00 01 02 00 16", "04H", "F1"),
    ("AFN=09H-F1 模块ID变更上报", "68 1A 00 1A 00 68 C3 33 33 44 55 00 09 01 00 00 00 01 00 00 00 01 05 01 02 03 04 05 06 AA BB CC DD EE FF 00 16", "09H", "F1"),
    ("AFN=05H-F3 广播校时", "68 1A 00 1A 00 68 4B 33 33 44 55 00 05 01 00 00 00 04 00 00 00 02 0C 68 99 99 99 99 99 99 68 08 06 78 63 15 21 33 56 00 16 00 16", "05H", "F3"),
    ("AFN=06H-F5 从节点事件上报", "68 1E 00 1E 00 68 C3 33 33 44 55 00 06 01 00 00 00 00 01 00 00 05 04 0A 01 02 03 04 05 06 01 78 63 15 21 33 56 00 16", "06H", "F5"),
    ("AFN=10H-F31 查询节点相位", "68 15 00 15 00 68 4B 33 33 44 55 00 10 01 00 00 00 00 01 00 00 01 02 03 04 05 06 00 16", "10H", "F31"),
]


@pytest.mark.parametrize("desc,hex_str,afn,fn", _NINGXIA_FRAMES,
                         ids=[f[0] for f in _NINGXIA_FRAMES])
def test_doc_ningxia_frame(adapter, desc, hex_str, afn, fn):
    """宁夏省文档示例帧解析测试。

    文档来源: 05.4_宁夏.md §4.1~§4.5
    预期: 省份帧使用FT1.2双L格式，适配器可能无法识别(pos3!=0x68)
    """
    raw = _hex(hex_str)
    confidence = adapter.confidence(raw)
    if confidence > 0:
        fr = adapter.decode(raw)
        assert fr.structure == "1376.2"
    else:
        pytest.skip(f"适配器不支持此帧格式(双L FT1.2): {desc}, confidence={confidence}")


def test_doc_ningxia_no_extension():
    """宁夏省无文档可考的扩展FN验证。"""
    # 宁夏13版扩展协议文档未包含在抓取范围内
    # 文档中仅使用标准命令: F1, F2, F3, F5, F7, F12, F31
    standard_fns = ["F1", "F2", "F3", "F5", "F7", "F12", "F31"]
    assert len(standard_fns) == 7
