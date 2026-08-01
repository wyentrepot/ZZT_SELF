import os
import sys

import pytest

from parser_lib.core.splitter import FrameSplitter
from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_645 import DLT645Adapter, build_frame

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")


@pytest.fixture
def adapter():
    store = MetadataStore()
    store.load_protocol("645", os.path.join(HERE, "..", "metadata"))
    return DLT645Adapter(metadata_store=store)


def _load(name):
    with open(os.path.join(FIX, name)) as f:
        return bytes.fromhex(f.read().strip())


def test_single_decode(adapter):
    fr = adapter.decode(_load("single.hex"))
    assert fr.structure == "645"
    assert fr.fields[0].value == "123456789012"
    assert fr.items[0].name == "(当前)正向有功总电能"
    assert abs(fr.items[0].value - 123456.78) < 1e-6
    assert fr.items[0].unit == "kWh"


def test_multi_split(adapter):
    sp = FrameSplitter([adapter])
    frames = sp.feed(_load("multi.hex"))
    assert len(frames) == 2
    assert all(f["complete"] for f in frames)
    assert sp.pending() == b""


def test_truncated_split(adapter):
    sp = FrameSplitter([adapter])
    frames = sp.feed(_load("truncated.hex"))
    assert len(frames) == 1
    assert len(sp.pending()) > 0          # 半包残留，等待续接


def test_escape_decode(adapter):
    fr = adapter.decode(_load("escape.hex"))
    assert fr.items[0].name == "(当前)正向有功总电能"
    # 数据域 +33H 加密后含 0x68，经传输转义 1B 68，反转义还原；-33H 后 BCD 小端 35000000/100 = 350000.00
    assert abs(fr.items[0].value - 350000.00) < 1e-6


def test_confidence(adapter):
    assert adapter.confidence(_load("single.hex")) == 1.0
    assert adapter.confidence(b"\x00\x00deadbeef") == 0.0


def test_unknown_di(adapter):
    raw = build_frame(bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0x12]), 0x91,
                      bytes([0x99, 0x99, 0x99, 0x99]) + bytes([0, 0, 0, 0]))
    fr = adapter.decode(raw)
    assert "未知" in fr.items[0].name
    assert any("未知数据标识" in w for w in fr.warnings)


def test_chunked_feed(adapter):
    """模拟大文件按小块(5字节)喂入，验证跨块半包缓冲与续接。"""
    data = _load("multi.hex")
    sp = FrameSplitter([adapter])
    got = []
    for i in range(0, len(data), 5):
        got += sp.feed(data[i:i + 5])
    assert len(got) == 2
    assert sp.pending() == b""
