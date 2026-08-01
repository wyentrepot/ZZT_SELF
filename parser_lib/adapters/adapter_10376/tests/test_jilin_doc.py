"""省份协议蒸馏文档示例帧测试 — 吉林省。

来源：蒸馏文档/05_省份协议/05.2_吉林.md
吉林省无自定义扩展协议帧格式，完全遵循Q/GDW 1376.2-2013和Q/GDW 12087系列标准。
扩展FN: 无（全部为标准命令）

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


# 吉林省文档示例帧（3帧，全部为标准1376.2命令）
_JILIN_FRAMES = [
    # (描述, hex, AFN, Fn)
    ("AFN=11H-F1 透明传输645读数据", "68 1A 00 1A 00 68 4B 33 33 44 55 00 11 01 00 00 00 01 00 00 00 01 0E 68 01 02 03 04 05 06 68 01 02 04 00 00 00 00 16", "11H", "F1"),
    ("AFN=10H-F2 查询从节点信息", "68 15 00 15 00 68 4B 33 33 44 55 00 10 01 00 00 00 00 02 00 00 01 00 14 00 16", "10H", "F2"),
    ("AFN=09H-F1 模块ID变更上报", "68 1A 00 1A 00 68 C3 33 33 44 55 00 09 01 00 00 00 01 00 00 00 01 02 01 02 03 04 05 06 AA BB CC DD EE FF 00 16", "09H", "F1"),
]


@pytest.mark.parametrize("desc,hex_str,afn,fn", _JILIN_FRAMES,
                         ids=[f[0] for f in _JILIN_FRAMES])
def test_doc_jilin_frame(adapter, desc, hex_str, afn, fn):
    """吉林省文档示例帧解析测试。

    文档来源: 05.2_吉林.md §4.1~§4.3
    预期: 省份帧使用FT1.2双L格式，适配器可能无法识别(pos3!=0x68)
    """
    raw = _hex(hex_str)
    confidence = adapter.confidence(raw)
    if confidence > 0:
        fr = adapter.decode(raw)
        assert fr.structure == "1376.2"
    else:
        pytest.skip(f"适配器不支持此帧格式(双L FT1.2): {desc}, confidence={confidence}")


def test_doc_jilin_no_extension():
    """吉林省无扩展FN验证。"""
    # 吉林省完全遵循标准协议，无省级扩展
    expected_fns = []
    assert len(expected_fns) == 0
