"""10H-F4 上行「路由运行状态」长度门限回归（REQ-0041）。

缺陷：`_app_items` 的 10H-F4 分支曾写成 `len(data) >= 17`，而该分支实际只读
data[0]..data[15] 共 16 字节：

    运行状态字 1B + 从节点总数量 2B + 已抄从节点数量 2B + 中继抄到从节点数量 2B
    + 工作开关 1B + 通信速率 2B + 第1~3相中继级别 3B + 第1~3相工作步骤 3B = 16B

协议元数据 metadata/afn_fn.json 中 10H/F4 resp.fields 逐字段 b 求和同样是 16，
故真机 16B 上行应答会被静默判成「无应用数据」，`nwk.route_status` 永远得不出结论。

本文件只锁 10H-F4 上行长度口径：16B 必须完整解出「AFN + 12 个应用字段」，
15B 必须一个应用字段都不出（门限不得退化为 >= 15）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from parser_lib import facade
from parser_lib.adapters.adapter_10376 import build_13762_frame

# 16B 上行应用数据（按标准字段顺序拼装）：
#   1D                   运行状态字
#   0400                 从节点总数量 = 4
#   0300                 已抄从节点数量 = 3
#   0200                 中继抄到从节点数量 = 2
#   4A                   工作开关（D7~D6=01 搜表 / D1=1 学习 / D3=1 上报事件）
#   8025                 通信速率 0x2580 = 9600 bps
#   010203               第1~3相中继级别
#   030405               第1~3相工作步骤（3 中继 / 4 监控 / 5 广播）
APPDATA_16B = bytes.fromhex("1D" "0400" "0300" "0200" "4A" "8025" "010203" "030405")

# AFN 条目之外应解出的 12 个应用字段（顺序即解析顺序）
APP_FIELD_NAMES = [
    "运行状态字",
    "从节点总数量",
    "已抄从节点数量",
    "中继抄到从节点数量",
    "工作开关",
    "通信速率",
    "第1相中继级别",
    "第2相中继级别",
    "第3相中继级别",
    "第1相工作步骤",
    "第2相工作步骤",
    "第3相工作步骤",
]


def _decode_10f4_up(appdata: bytes) -> list:
    """构 1376.2 上行帧 -> facade 指定协议宽容解码 -> 返回 items（dict 列表）。"""
    raw = build_13762_frame(afn=0x10, fn=4, appdata=appdata, direction="up")
    out = facade.decode(raw, protocol="1376.2")
    assert out["ok"], out
    assert out["warnings"] == [], out["warnings"]
    return out["frame"]["items"]


def _items_by_name(items: list) -> dict:
    return {it["name"]: it for it in items}


def _replace(appdata: bytes, offset: int, chunk: bytes) -> bytes:
    buf = bytearray(appdata)
    buf[offset:offset + len(chunk)] = chunk
    return bytes(buf)


# ------------------------------------------------------------ 长度门限本体 ----

def test_16b_uplink_yields_afn_plus_12_fields():
    """16B 上行必须解出 13 项：AFN + 12 个应用字段（回归 off-by-one 的核心断言）。"""
    items = _decode_10f4_up(APPDATA_16B)
    assert len(items) == 13, [it["name"] for it in items]
    assert [it["name"] for it in items] == ["AFN"] + APP_FIELD_NAMES
    assert items[0]["value"] == "0x10 (路由查询)"


def test_16b_field_values_match_constructed():
    """16B 每个字段的值/hex 与构造值一致（防止门限放开了却解析错位）。"""
    got = _items_by_name(_decode_10f4_up(APPDATA_16B))
    expected = {
        "运行状态字": ("0x1D", "1D"),
        "从节点总数量": ("4", "0400"),
        "已抄从节点数量": ("3", "0300"),
        "中继抄到从节点数量": ("2", "0200"),
        "工作开关": ("0x4A", "4A"),
        "通信速率": ("9600 (bps)", "8025"),
        "第1相中继级别": ("1", "01"),
        "第2相中继级别": ("2", "02"),
        "第3相中继级别": ("3", "03"),
        "第1相工作步骤": ("3 (中继)", "03"),
        "第2相工作步骤": ("4 (监控)", "04"),
        "第3相工作步骤": ("5 (广播)", "05"),
    }
    for name, (value, hex_str) in expected.items():
        assert got[name]["value"] == value, name
        assert got[name]["hex"] == hex_str, name
    # 运行状态字位域：D7~D4 纠错编码、D3 上报事件、D2 工作、D1 路由完成
    assert got["运行状态字"]["desc"] == "纠错编码=1 上报事件=1 工作=1 路由完成=0"


def test_15b_uplink_yields_no_app_fields():
    """15B（少 1 字节）必须不产生任何应用字段：门限守得住，不得退化为 >= 15。"""
    items = _decode_10f4_up(APPDATA_16B[:-1])
    assert [it["name"] for it in items] == ["AFN"]
    present = set(_items_by_name(items))
    assert not (present & set(APP_FIELD_NAMES)), present & set(APP_FIELD_NAMES)


def test_17b_uplink_still_decodes_full():
    """多 1 字节尾部（17B）仍应解出全部 12 个应用字段：门限是下限而非等长。"""
    items = _decode_10f4_up(APPDATA_16B + b"\x00")
    assert len(items) == 13
    assert [it["name"] for it in items] == ["AFN"] + APP_FIELD_NAMES


# ------------------------------------------------------- 三个计数字段小端口径 ----

@pytest.mark.parametrize("name,offset,raw,expected", [
    ("从节点总数量", 1, "0102", "513"),        # 0x0201，大端误读会得 258
    ("已抄从节点数量", 3, "0001", "256"),      # 0x0100，大端误读会得 1
    ("中继抄到从节点数量", 5, "0201", "258"),  # 0x0102，大端误读会得 513
])
def test_counts_are_little_endian(name, offset, raw, expected):
    """从节点总数量 / 已抄从节点数量 / 中继抄到从节点数量 均按低字节在前解析。"""
    items = _items_by_name(_decode_10f4_up(_replace(APPDATA_16B, offset, bytes.fromhex(raw))))
    assert items[name]["value"] == expected
    assert items[name]["hex"] == raw.lower()


def test_counts_hex_reflects_wire_order():
    """计数字段 hex 保留线上字节序（0400 -> 4），与 value 的小端解释相互印证。"""
    items = _items_by_name(_decode_10f4_up(APPDATA_16B))
    for name, wire, value in (("从节点总数量", "0400", "4"),
                              ("已抄从节点数量", "0300", "3"),
                              ("中继抄到从节点数量", "0200", "2")):
        assert items[name]["hex"] == wire
        assert items[name]["value"] == value


# ------------------------------------------------------------ 工作开关位域 ----

@pytest.mark.parametrize("byte,cur_state,work_state", [
    (0x4A, "搜表", "学习"),   # D7~D6=01 搜表 / D1=1 学习
    (0x00, "抄表", "抄表"),   # D7~D6=00 抄表 / D1=0 抄表
    (0x80, "升级", "抄表"),   # D7~D6=10 升级 / D1=0 抄表
    (0xC2, "其他", "学习"),   # D7~D6=11 其他 / D1=1 学习
])
def test_work_switch_desc_text(byte, cur_state, work_state):
    """工作开关 desc 的「当前状态」与「工作状态」文本取自高位/次低位。"""
    items = _items_by_name(_decode_10f4_up(_replace(APPDATA_16B, 7, bytes([byte]))))
    desc = items["工作开关"]["desc"]
    assert f"当前状态={cur_state}" in desc, desc
    assert f"工作状态={work_state}" in desc, desc
    assert items["工作开关"]["value"] == f"0x{byte:02X}"
    assert items["工作开关"]["hex"] == f"{byte:02X}"


# ------------------------------------------------- 与协议元数据字段长度互证 ----

def test_metadata_resp_field_bytes_sum_to_16():
    """metadata/afn_fn.json 的 10H/F4 resp.fields 逐字段 b 求和 == 16 == 分支消费长度。"""
    meta_path = Path(__file__).resolve().parents[1] / "metadata" / "afn_fn.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    afn10 = [a for a in meta["afn"] if a["code"] == "10H"]
    assert len(afn10) == 1
    f4 = [fn for fn in afn10[0]["fns"] if fn["no"] == "F4"]
    assert len(f4) == 1
    fields = f4[0]["resp"]["fields"]
    total = sum(int(f["b"]) for f in fields)
    assert total == 16, total
    # 字段名与解析器产出一一对应（数量口径互证）
    assert [f["n"] for f in fields] == APP_FIELD_NAMES
    # 16B 恰好等于元数据总长度时能完整解出，15B 不能
    assert len(_decode_10f4_up(APPDATA_16B)) == len(fields) + 1
    assert len(_decode_10f4_up(APPDATA_16B[:total - 1])) == 1
