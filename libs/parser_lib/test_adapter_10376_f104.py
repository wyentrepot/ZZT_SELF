# -*- coding: utf-8 -*-
"""10H-F104 下行请求数据单元编码回归(2026-11-04)。

背景:F104 曾被误注册进 _NO_UNIT_DOWN(下行无数据单元白名单),导致 params 被静默
忽略、只能发出空数据单元帧,实机 CCO 回否认 0x01(无效数据单元)。
修复:移出白名单,并入 10-F21 家族的「起始序号 2B 小端 + 数量 1B」分支。
台账口径(用例库/CCO/台账/146 行92):起始序号=0,节点数量=3。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser_lib.adapters.adapter_10376 import encode_app_data  # noqa: E402


def test_f104_down_encodes_start_count():
    data = encode_app_data(0x10, 104, {"start": 0, "count": 3})
    assert data == bytes.fromhex("000003")


def test_f104_start_is_little_endian_u16():
    data = encode_app_data(0x10, 104, {"start": 1, "count": 2})
    assert data == bytes.fromhex("010002")


def test_f104_defaults_match_f21_family():
    # 缺省 start=0/count=0,与 10-F21 家族分支行为一致
    data = encode_app_data(0x10, 104, {})
    assert data == bytes.fromhex("000000")


def test_f104_not_in_no_unit_down():
    from parser_lib.adapters.adapter_10376 import _NO_UNIT_DOWN
    assert (0x10, 104) not in _NO_UNIT_DOWN
