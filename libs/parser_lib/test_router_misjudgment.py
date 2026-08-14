"""协议嗅探路由隔离测试（M7 #3 校准验证）。

校准后的 confidence() 必须让 ProtocolRouter 在 645 / 698.45 / 1376.2 三种帧之间
做出正确选择，杜绝误判。核心识别特征（索引 95 帧类别）：

  - 645（FT1.2）：68 | A(6) | 68(pos7) | C | L | ... ，pos3 是地址字节（绝不可能是 0x68）
  - 698.45     ：68 | L(2) | C(非0x68) | SA | CA | HCS | APDU | FCS | 16
  - 1376.2     ：68 | L(2) | 68(pos3) | AFN | SEQ | RTUA(6) | MSAA | PW | 用户数据 | CS | 16

即 1376.2 的第二个 0x68 固定在 pos3，而 645 在 pos7、698 在 pos3 是控制域 ——
这个位置差异是三者互不抢占的关键。
"""
import os

import pytest

from parser_lib.core.router import ProtocolRouter
from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_645 import DLT645Adapter, build_frame as build_645
from parser_lib.adapters.adapter_698 import DLT69845Adapter, build_frame as build_698
from parser_lib.adapters.adapter_10376 import QGDW103762Adapter, build_frame as build_1376

HERE = os.path.dirname(__file__)
ADP = os.path.join(HERE, "adapters")


@pytest.fixture
def router():
    store = MetadataStore()
    store.load_protocol("645", os.path.join(ADP, "adapter_645", "metadata"))
    store.load_protocol("698.45", os.path.join(ADP, "adapter_698", "metadata"))
    a645 = DLT645Adapter(metadata_store=store)
    a698 = DLT69845Adapter(metadata_store=store)
    a1376 = QGDW103762Adapter()
    return ProtocolRouter([a645, a698, a1376])


def _f645():
    # 645 读数据帧（正向有功总电能 DI=9010FF00）
    return build_645(bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]), 0x11,
                     bytes([0x90, 0x10, 0xFF, 0x00]) + bytes([0, 0, 0, 0]))


def _f698():
    # 698.45 GET-ResponseNormal：OAD=40010200 通信地址，octet-string "123456789012"
    apdu = bytes([0x85, 0x01, 0x01,
                  0x40, 0x01, 0x02, 0x00,
                  0x01, 0x09, 0x06, 0x12, 0x34, 0x56, 0x78, 0x90, 0x12])
    return build_698(apdu, bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20]),
                     ca=0x01, control=0x43)


def _f1376():
    # 1376.2 AFN=02 数据转发，用户数据内嵌一帧 645
    rtsa = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])
    return build_1376(afn=0x02, seq=0x01, rtsa=rtsa, msaa=0x01,
                      pw=0x0000, userdata=bytes([0x12, 0x34]) + _f645())


def test_router_routes_645(router):
    sel = router.select(_f645())
    assert sel is not None and sel.protocol == "645"


def test_router_routes_698(router):
    sel = router.select(_f698())
    assert sel is not None and sel.protocol == "698.45"


def test_router_routes_1376(router):
    # 1376.2 信封必须被路由到 1376.2 适配器，而非被 698/645 抢占
    sel = router.select(_f1376())
    assert sel is not None and sel.protocol == "1376.2"


def test_1376_not_misrouted_to_698_or_645(router):
    """关键隔离：双 0x68 的 1376.2 帧，698 与 645 适配器都必须打 0 分。"""
    f = _f1376()
    a698 = next(a for a in router.adapters.values() if a.protocol == "698.45")
    a645 = next(a for a in router.adapters.values() if a.protocol == "645")
    assert a698.confidence(f) == 0.0, "698 必须拒绝 pos3==0x68 的 1376.2 帧"
    assert a645.confidence(f) == 0.0, "645 必须拒绝 pos3==0x68 的 1376.2 帧（pos3 是地址字节，非 0x68）"


def test_645_not_misrouted_to_698(router):
    """645 帧（pos7==0x68）不得被 698 抢占；698 见到 pos7==0x68 且符合 645 结构应让位。"""
    f = _f645()
    a698 = next(a for a in router.adapters.values() if a.protocol == "698.45")
    assert a698.confidence(f) == 0.0


def test_all_three_distinct_scores():
    """同一帧只应被一个适配器给高分，其他应明显更低（验证打分梯度）。"""
    f698 = _f698()
    a698 = DLT69845Adapter()
    assert a698.confidence(f698) == 1.0
    # 698 帧对 645/1376 不应被误认为高分（虽结构上可能非零，但应 < 698 自身）
    a645 = DLT645Adapter()
    a1376 = QGDW103762Adapter()
    assert a645.confidence(f698) < 1.0
    assert a1376.confidence(f698) < 1.0
