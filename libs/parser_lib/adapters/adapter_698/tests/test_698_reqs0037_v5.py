"""REQS-0037 重庆 STA 分钟采集 698 11E3 上行帧（v5）真实帧回归。

帧来源：/home/H_STA/01/sta/reqs/0005-chongqing-sta-minute-collection-698-11e3/
evidence/BENCH-v5-taskid-RECOVERED.md（2026-11-02 真机台架 task 1 全链路 PASS）。
只读证据，测试内固化为常量。

覆盖点：
- 内层 698：C=0xC3、APDU 85 02 01 03（无任务号）、OAD1/2 正常值、
  缺项 OAD3 20000200 全 FF 标注"缺失(FF填充)"、9B 固定尾 TimeTag 可读；
- 00E3 上行业务体（mclt_data_up_t 23B 头）：报文条数/转发数据长度/内嵌 698；
- 06H-F230（1376.2）：任务号/协议/报文长度/嵌套 698 与内层一致。
"""
import os

import pytest

from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_698 import DLT69845Adapter
from parser_lib.adapters.adapter_dualmode import DualMode43Adapter

HERE = os.path.dirname(__file__)

# REQS-0037 v5 台架真实帧（BENCH-v5-taskid-RECOVERED.md）
INNER_698_HEX = (
    "683400c30500000000000101cda99000228502010320000201011101200102010111"
    "022000020001ffff00000100046ae8c3e84f7716"
)
OUTER_11E3_UP_HEX = (
    "11e30000c115240000000300000000000101001017021126033600"
    + INNER_698_HEX
)
F230_FULL_HEX = (
    "685500c300004000002006201c01000000000001030010170211263600"
    + INNER_698_HEX + "8616"
)


@pytest.fixture
def adapter():
    store = MetadataStore()
    store.load_protocol("698.45", os.path.join(HERE, "..", "metadata"))
    return DLT69845Adapter(metadata_store=store)


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


class TestInner698V5:
    def test_extract_confidence_full(self, adapter):
        raw = bytes.fromhex(INNER_698_HEX)
        res = adapter.try_extract(raw)
        assert res is not None
        assert res.consumed == 54
        assert adapter.confidence(res.raw) == 1.0

    def test_link_layer_and_apdu_header(self, adapter):
        frame = adapter.decode(bytes.fromhex(INNER_698_HEX))
        assert frame.structure == "698.45"
        assert _field(frame, "控制域C").value == "0xC3"
        assert "校验通过" in _field(frame, "帧头校验HCS").desc
        assert "校验通过" in _field(frame, "帧校验FCS").desc
        assert _field(frame, "APDU类型").value == "SECURITY-Response"
        assert _field(frame, "内层APDU类型").value == "GET-Response"
        assert _field(frame, "响应类型").value == "0x02 (GetResponseNormalList)"
        # APDU 第三字节是 PIID-ACD，不是任务号（QUOK-17 修复点）
        assert _field(frame, "PIID-ACD").raw == 1
        assert _field(frame, "OAD个数").raw == 3
        assert frame.warnings == []

    def test_missing_oad_ff_labeled_not_garbled(self, adapter):
        """缺项 OAD 20000200 全 0xFF：标注"缺失(FF填充)"，不吞 TimeTag/其他 OAD。"""
        frame = adapter.decode(bytes.fromhex(INNER_698_HEX))
        missing = _item(frame, "电压")
        assert missing is not None
        assert missing.value == "缺失(FF填充)"
        assert missing.hex == "ffff"
        assert missing.desc == "缺项"
        # 前两个 OAD 数值正常，未被 FF 干扰
        assert _item(frame, "A相计量电压").value == 0.1
        assert _item(frame, "A相计量电流").value == 0.002

    def test_timetag_readable_raw_hex_kept(self, adapter):
        """9 字节固定尾解为可读时间（原始 hex 保留在 hex 字段）。"""
        frame = adapter.decode(bytes.fromhex(INNER_698_HEX))
        tag = _field(frame, "时间标签TimeTag")
        assert tag is not None
        assert tag.value == "2026-11-02 17:10:00"
        assert tag.hex == "00000100046AE8C3E8"
        # 台架记录 TimeTag = 2026-11-02 17:10:00


class TestE3UpBusinessV5:
    """00E3 上行业务体（STA mclt_data_up_t 23B 头）+ 内嵌 698。"""

    def test_e3_up_header_fields(self):
        frame = DualMode43Adapter().decode(bytes.fromhex(OUTER_11E3_UP_HEX))
        assert frame.structure == "双模4-3"
        assert _field(frame, "报文ID").raw == 0x00E3
        assert _field(frame, "分钟采集类型").value == "读取应答"
        assert _field(frame, "方向").value == "上行"
        assert _field(frame, "报文头长度").raw == 23
        assert _field(frame, "报文序号").raw == 0x00000024
        assert _field(frame, "协议类型").raw == 3
        assert _field(frame, "响应结果").raw == 0
        assert _field(frame, "源MAC地址").value == "00:00:00:00:00:01"
        assert _field(frame, "任务号").raw == 1
        assert _field(frame, "冻结时刻").value == "2026-11-02 17:10:00"
        assert _field(frame, "报文条数").raw == 3
        assert _field(frame, "转发数据长度").raw == 54
        assert frame.warnings == []

    def test_e3_up_nested_698_consistent(self):
        frame = DualMode43Adapter().decode(bytes.fromhex(OUTER_11E3_UP_HEX))
        assert len(frame.nested) == 1
        nested = frame.nested[0]
        assert nested.structure == "698.45"
        assert nested.raw_hex == INNER_698_HEX
        assert _field(nested, "控制域C").value == "0xC3"
        assert _field(nested, "时间标签TimeTag").value == "2026-11-02 17:10:00"
        assert _item(nested, "电压").value == "缺失(FF填充)"


class TestF230V5:
    """06H-F230 完整帧：任务号/协议/长度/嵌套 698 一致。"""

    def test_f230_decode(self):
        from parser_lib.adapters.adapter_10376 import decode_frame_json
        out = decode_frame_json({"frame": F230_FULL_HEX})
        assert out["ok"] is True
        items = {f["name"]: f for f in out["items"]}
        assert items["任务号"]["value"] == "1"
        assert "698.45" in items["通信协议类型"]["value"]
        assert items["报文长度"]["value"] == "54B"
        assert items["采集时间"]["value"] == "2026年11月02日 17:10:00"
        assert out["warnings"] == []
        assert len(out["nested"]) == 1
        nested = out["nested"][0]
        assert nested["structure"] == "698.45"
        nested_items = {f["name"]: f for f in nested["items"]}
        assert nested_items["电压"]["value"] == "缺失(FF填充)"
