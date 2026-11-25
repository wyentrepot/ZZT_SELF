"""10H-F2 从节点信息字「通信协议类型」位段黄金断言（REQS-0043 工位 I1）。

权威口径：``docs/_references/protocols/安徽集中器交互报文.md`` §5 —— 从节点信息字
高字节 D15 备用 / D14 支持分钟采集 / **D13~D11 通信协议类型** / D10~D8 相位。

E3 真机实锤（REQS-0043 DONE §E3 / CASE-BACKLOG §G-7）：
- 698 建档后 10H-F2 节点信息词 ``0x180F`` → D13~D11=3（698.45，与 11H-F1 实发 0x03 闭环）；
- 645 日常态词 ``0x50F1`` → D13~D11=2（645-2007，与 645 建档一致）。
旧解码 ``(w>>12)&0x7`` 实读 D14~D12（解出 1/5），已校正为 ``(w>>11)&0x7``。

逐字节样本纪律：完整帧 hex 一次性离线构造后钉死在本文件，
断言不再用同一构造函数回算（防「构造错了解析也对」的循环论证）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from parser_lib.adapters.adapter_10376 import QGDW103762Adapter  # noqa: E402

# 完整 10H-F2 上行帧（单节点，地址 BCD 12 34 56 78 90 12，节点信息字见名）：
#   68 1A 00 83 00 00 00 00 00 00 10 02 | 0100 | 01 | 123456789012 | <节点信息字小端> | CS 16
_FRAME_180F = "681A00830000000000001002000100011234567890120F187416"   # 词 0x180F
_FRAME_50F1 = "681A0083000000000000100200010001123456789012F1508E16"   # 词 0x50F1
_FRAME_ZERO = "681A008300000000000010020001000112345678901200004D16"   # 词 0x0000（全 0 边界）
_FRAME_0800 = "681A008300000000000010020001000112345678901200085516"   # 词 0x0800（协议 1 边界）

# (帧 hex, 节点信息词, 期望协议类型, 期望信号品质, 期望中继级别, 期望相位, 出处)
_GOLDEN = [
    (_FRAME_180F, "0x180F", 3, 0, 15, 0,
     "E3 真帧：698 建档（run 20261125-082646-8cff48 s3c）"),
    (_FRAME_50F1, "0x50F1", 2, 15, 1, 0,
     "E3 真帧：645 日常态（CASE-BACKLOG §G-7）"),
    (_FRAME_ZERO, "0x0000", 0, 0, 0, 0,
     "边界：全 0 → 协议 0（透明）"),
    (_FRAME_0800, "0x0800", 1, 0, 0, 0,
     "边界：D11=1 → 协议 1（645-1997）；旧解码 (w>>12)&7 会误读 0"),
]


def _node_info_item(frame_hex: str):
    adapter = QGDW103762Adapter()
    fr = adapter.decode(bytes.fromhex(frame_hex))
    for it in fr.items:
        if it.name == "从节点1信息":
            return it
    raise AssertionError("10H-F2 应答未解出「从节点1信息」")


def _desc_value(desc: str, key: str) -> int:
    for token in str(desc).split():
        if token.startswith(key + "="):
            return int(token[len(key) + 1:])
    raise AssertionError(f"desc 缺 {key}：{desc!r}")


@pytest.mark.parametrize(
    "frame_hex,word,proto,signal,relay,phase,source", _GOLDEN,
    ids=[g[6].split("：")[0] for g in _GOLDEN])
def test_10f2_node_info_protocol_type_bitfield(frame_hex, word, proto, signal,
                                               relay, phase, source):
    """真帧黄金：D13~D11=通信协议类型（0x180F→3、0x50F1→2、全 0→0、0x0800→1）。"""
    it = _node_info_item(frame_hex)
    assert it.value == word, f"节点信息词应 {word}，实得 {it.value}"
    assert _desc_value(it.desc, "协议类型") == proto
    assert _desc_value(it.desc, "信号品质") == signal
    assert _desc_value(it.desc, "中继级别") == relay
    assert _desc_value(it.desc, "相线") == phase
