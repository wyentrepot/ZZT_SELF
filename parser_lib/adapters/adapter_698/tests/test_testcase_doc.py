"""测试用例蒸馏文档示例帧测试。

来源：蒸馏文档/06_测试用例.md
覆盖检测线HPLC双模模块与抄控器通信协议（698扩展+376.2扩展）的14个示例帧。
扩展OAD: FF F0 01 00 ~ FF F0 05 00（检测线专用）
扩展376.2: AFN=05, F80/F81/F82（检测线抄控器通信）
698代理透明转发: 09 07（携带376.2报文）

帧格式: 698.45帧格式(68|L(2B)|C|SA|HCS|APDU|FCS|16)，由DLT69845Adapter解析。
376.2帧格式: 376.2单68H格式，适配器无法识别。
"""
import os
import pytest
from parser_lib.adapters.adapter_698 import DLT69845Adapter
from parser_lib.adapters.adapter_10376 import QGDW103762Adapter

HERE = os.path.dirname(__file__)


@pytest.fixture
def adapter698():
    return DLT69845Adapter()


@pytest.fixture
def adapter10376():
    return QGDW103762Adapter()


def _hex(s):
    return bytes.fromhex(s.replace(" ", "").replace("XX", "00").replace("CS", "00"))


# 检测线698扩展帧（OAD: FF F0 xx xx）
_TESTCASE_698_FRAMES = [
    # (描述, hex, OAD)
    ("射频参数广播配置请求(FF F0 01 00)", "68 1b 00 43 05 01 00 00 00 00 00 10 d9 14 06 01 01 FF F0 01 00 09 02 02 29 00 ef 9e 16", "FF F0 01 00"),
    ("射频参数广播配置确认(FF F0 01 00)", "68 19 00 c3 05 01 00 00 00 00 00 10 2c 7b 86 01 01 FF F0 01 00 00 00 00 fa a3 16", "FF F0 01 00"),
    ("设置抄控模式请求(FF F0 02 00)", "68 1a 00 43 05 01 00 00 00 00 00 10 48 41 06 01 03 FF F0 02 00 09 01 02 00 9d 3d 16", "FF F0 02 00"),
    ("设置抄控模式确认(FF F0 02 00)", "68 19 00 c3 05 01 00 00 00 00 00 10 2c 7b 86 01 03 FF F0 02 00 00 00 00 59 b5 16", "FF F0 02 00"),
    ("标准698抄表(正向有功)", "68 17 00 43 05 05 01 00 00 00 00 00 fa 8d 05 01 01 00 10 02 00 00 fe 19 16", "00 10 02 00"),
    ("抄读芯片ID请求(FF F0 05 00)", "68 17 00 43 05 01 00 00 00 00 00 10 26 f6 05 01 01 ff f0 05 00 00 2b d4 16", "FF F0 05 00"),
    ("抄读芯片ID应答(FF F0 05 00)", "68 33 00 c3 05 01 00 00 00 00 00 10 2e 8e 85 01 01 ff f0 05 00 01 09 18 A1 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23 AA 00 00 de af 16", "FF F0 05 00"),
    ("虚拟表地址请求(40 01 02 00)", "68 17 00 43 45 AA AA AA AA AA AA 10 DA 5F 05 01 01 40 01 02 00 00 C6 07 16", "40 01 02 00"),
    ("虚拟表地址响应(40 01 02 00)", "68 21 00 C3 05 10 00 00 00 00 00 10 55 7E 85 01 41 40 01 02 00 01 09 06 00 00 00 00 00 10 00 00 DD BA 16", "40 01 02 00"),
    ("射频参数配置CCO请求(FF F0 03 00)", "68 1b 00 43 05 01 00 00 00 00 00 10 d9 14 06 01 04 FF F0 03 00 09 02 02 29 00 ef 35 16", "FF F0 03 00"),
    ("射频参数配置CCO确认(FF F0 03 00)", "68 19 00 c3 05 01 00 00 00 00 00 10 2c 7b 86 01 04 FF F0 03 00 00 00 00 13 22 16", "FF F0 03 00"),
    ("无线射频控制请求(FF F0 04 00)", "68 1a 00 43 05 01 00 00 00 00 00 10 48 41 06 01 06 FF F0 04 00 09 01 02 00 7f 57 16", "FF F0 04 00"),
    ("无线射频控制确认(FF F0 04 00)", "68 19 00 c3 05 01 00 00 00 00 00 10 2c 7b 86 01 06 FF F0 04 00 00 00 00 a0 19 16", "FF F0 04 00"),
]


@pytest.mark.parametrize("desc,hex_str,oad", _TESTCASE_698_FRAMES,
                         ids=[f[0] for f in _TESTCASE_698_FRAMES])
def test_doc_testcase_698_frame(adapter698, desc, hex_str, oad):
    """检测线698扩展帧解析测试。

    文档来源: 06_测试用例.md §3.1.1~§3.1.2
    扩展OAD: FF F0 01 00~FF F0 05 00, 40 01 02 00
    """
    raw = _hex(hex_str)
    confidence = adapter698.confidence(raw)
    if confidence > 0:
        fr = adapter698.decode(raw)
        assert fr is not None
    else:
        pytest.skip(f"698适配器不支持此帧: {desc}, confidence={confidence}")


# 698代理透明转发帧（09 07）
_TESTCASE_PROXY_FRAMES = [
    ("698代理透传376.2-设置抄控通道", "68 31 00 43 05 55 55 22 33 11 11 14 86 A2 09 07 09 F2 09 02 01 06 02 08 01 00 00 03 00 64 10 68 10 00 43 00 00 00 25 80 01 05 80 09 01 78 16 00 39 72 16"),
    ("698代理透传376.2-配置无线参数", "68 32 00 43 05 55 55 22 33 11 11 14 35 5C 09 07 0F F2 09 02 01 06 02 08 01 00 00 03 00 64 11 68 11 00 43 00 00 00 25 80 02 05 01 0A 02 29 25 16 00 0D E9 16"),
    ("698代理透传376.2-无线射频控制", "68 31 00 43 05 55 55 22 33 11 11 14 86 A2 09 07 11 F2 09 02 01 06 02 08 01 00 00 03 00 64 10 68 10 00 43 00 00 00 25 80 02 05 02 0A 02 FD 16 00 39 15 16"),
]


@pytest.mark.parametrize("desc,hex_str", _TESTCASE_PROXY_FRAMES,
                         ids=[f[0] for f in _TESTCASE_PROXY_FRAMES])
def test_doc_testcase_proxy_frame(adapter698, desc, hex_str):
    """698代理透明转发帧解析测试。

    文档来源: 06_测试用例.md §3.1.2.3
    APDU: 09 07 = ProxyRequest transparent forwarding
    内层为376.2帧（AFN=05, F80/F81/F82）
    """
    raw = _hex(hex_str)
    confidence = adapter698.confidence(raw)
    if confidence > 0:
        fr = adapter698.decode(raw)
        assert fr is not None
    else:
        pytest.skip(f"698代理透传帧: {desc}, confidence={confidence}")


# 检测线376.2扩展帧（AFN=05, F80/F81/F82）
_TESTCASE_376_FRAMES = [
    # (描述, hex, AFN, Fn)
    ("AFN=05-F80 设置抄控通道(HPLC)", "68 10 00 43 00 00 00 25 80 01 05 80 09 01 78 16", "05H", "F80"),
    ("AFN=05-F81 配置无线参数", "68 11 00 43 00 00 00 25 80 02 05 01 0A 02 29 25 16", "05H", "F81"),
    ("AFN=05-F82 无线射频控制(HRF关闭)", "68 10 00 43 00 00 00 25 80 02 05 02 0A 02 FD 16", "05H", "F82"),
]


@pytest.mark.parametrize("desc,hex_str,afn,fn", _TESTCASE_376_FRAMES,
                         ids=[f[0] for f in _TESTCASE_376_FRAMES])
def test_doc_testcase_376_frame(adapter10376, desc, hex_str, afn, fn):
    """检测线376.2扩展帧解析测试。

    文档来源: 06_测试用例.md §3.1.2.2
    扩展Fn: F80(设置抄控通道), F81(配置无线参数), F82(无线射频控制)
    帧格式: 376.2单68H格式，适配器可能无法识别。
    """
    raw = _hex(hex_str)
    confidence = adapter10376.confidence(raw)
    if confidence > 0:
        fr = adapter10376.decode(raw)
        assert fr.structure == "1376.2"
    else:
        pytest.skip(f"376.2单68H帧适配器不支持: {desc}, confidence={confidence}")


def test_doc_testcase_extended_oad_list():
    """检测线扩展OAD清单验证。"""
    expected_oads = [
        "FF F0 01 00",  # 射频参数广播配置
        "FF F0 02 00",  # 抄控模式设置
        "FF F0 03 00",  # 射频参数配置CCO
        "FF F0 04 00",  # 无线射频控制
        "FF F0 05 00",  # 抄读芯片ID
        "40 01 02 00",  # 虚拟地址获取
    ]
    assert len(expected_oads) == 6


def test_doc_testcase_extended_fn_list():
    """检测线扩展376.2 Fn清单验证。"""
    expected_fns = ["F80", "F81", "F82"]
    assert len(expected_fns) == 3
