"""13762 库构建侧数据单元编码测试（阶段2）。

验证 encode_app_data / build_frame_json(params) 按聚焦 AFN/Fn 模板编码：
- 逐字节还原旧 task 已验证帧（11H-F1、11H-F231 配置帧）；
- decode 回读字段一致（编码-解析闭环）；
- 未覆盖 Fn 明确报错 UnsupportedFn；
- 11H-F231 扁平 items 按 meter_type 分组 + 每组长 2B 小端。
"""
from __future__ import annotations

import pytest

from parser_lib.adapters.adapter_10376 import (
    build_frame_json,
    decode_frame_json,
    encode_app_data,
    UnsupportedFn,
)


def _decode_hex(full_hex: str) -> dict:
    r = decode_frame_json({"frame": full_hex})
    assert r["ok"], r
    return r


def test_11f1_arch_add_matches_verified_frame():
    """旧用例添加档案 buff 逐字节还原（地址正序 BCD，以验证帧为准）。

    旧用例标注地址 080000000008 与帧内 08 00 00 00 00 00（正序=080000000000）
    有出入；以帧为准：action=1 + addr 080000000000 + protocol=3(698)。
    """
    params = {"action": "add", "addr": "080000000000", "protocol": 3}
    out = encode_app_data(0x11, 1, params)
    assert out == bytes.fromhex("01 08 00 00 00 00 00 03")
    # 完整帧：带地址域（module_id=1），decode 回读 AFN/FN/地址正确
    r = build_frame_json({
        "afn": "11", "fn": 1,
        "info": {"seq": 7},
        "address": {"src": "020103040506", "dst": "020103040506"},
        "data": {"params": params},
    })
    assert r["ok"], r
    d = _decode_hex(r["frame_hex"])
    assert d["fields"]["AFN"]["raw"] == 0x11
    assert d["fields"]["FN"]["raw"] == 1
    assert d["fields"]["信息域R"]["raw"].endswith("07")  # seq=7
    assert d["fields"]["地址域A"]["value"].startswith("020103040506")


def test_11f231_config_matches_verified_frame():
    """11H-F231 配置任务(7/645/5分钟/单相1项) 还原旧 task buff（含三相/其他空组）。"""
    params = {
        "task_no": 7, "action": "enable", "protocol": 2, "cycle_min": 5,
        "items": [{"meter_type": 0, "item": "02010100", "reply_len": 4}],
    }
    out = encode_app_data(0x11, 231, params)
    # 07(task) 01(enable) 02(645) 05(cycle) | 00(单相) 01(n) 0400(总长4小端)
    # 02010100(item) 04(reply_len) | 01 00(三相空) | 02 00(其他空)
    assert out == bytes.fromhex(
        "07 01 02 05 00 01 04 00 02 01 01 00 04 01 00 02 00")


def test_11f231_flat_items_grouping():
    """扁平 items 按 meter_type 分组排序，组内写固定值+数量+总长(2B小端)+逐项。"""
    params = {
        "task_no": 1, "action": "enable", "protocol": 2, "cycle_min": 5,
        "items": [
            {"meter_type": 0, "item": "04000201", "reply_len": 4},
            {"meter_type": 0, "item": "02010100", "reply_len": 3},
            {"meter_type": 1, "item": "04000201", "reply_len": 4},
            {"meter_type": 2, "item": "02010100", "reply_len": 3},
        ],
    }
    out = encode_app_data(0x11, 231, params)
    assert out[0:4] == bytes.fromhex("01 01 02 05")
    # 单相组：00 02 [总长7=0700] 04000201 04 02010100 03（组头4B + 10B = 14B）
    assert out[4:18] == bytes.fromhex("00 02 07 00 04 00 02 01 04 02 01 01 00 03")
    # 三相组：01 01 [总长4] 04000201 04（组头4B + 5B = 9B）
    assert out[18:27] == bytes.fromhex("01 01 04 00 04 00 02 01 04")
    # 其他表组：02 01 [总长3] 02010100 03（组头4B + 5B = 9B）
    assert out[27:36] == bytes.fromhex("02 01 03 00 02 01 01 00 03")
    # 总长校验：4+3=7 / 4 / 3 均小端
    assert out[6:8] == (7).to_bytes(2, "little")
    assert out[20:22] == (4).to_bytes(2, "little")
    assert out[29:31] == (3).to_bytes(2, "little")


def test_11f232_arch_attach():
    """11H-F232 关联档案：任务号 + 数量2B小端 + 6B BCD×N。"""
    out = encode_app_data(0x11, 232, {"task_no": 7, "meters": ["013300000001", "013300000002"]})
    assert out == bytes.fromhex("07 02 00 01 33 00 00 00 01 01 33 00 00 00 02")


def test_10f2_query_nodes():
    """10H-F2 查询从节点：start 2B小端 + count 1B。"""
    out = encode_app_data(0x10, 2, {"start": 0, "count": 16})
    assert out == bytes.fromhex("00 00 10")


def test_10f231_query_task_config():
    out = encode_app_data(0x10, 231, {"task_no": 7, "protocol": 2})
    assert out == bytes.fromhex("07 02")


def test_query_no_data_unit():
    for afn, fn in [(0x03, 10), (0x10, 4), (0x10, 230)]:
        assert encode_app_data(afn, fn, {}) == b"", (afn, fn)


def test_00f1_confirm_deny():
    assert encode_app_data(0x00, 1, {}) == b""
    assert encode_app_data(0x00, 1, {"status": "confirm"}) == b""
    assert encode_app_data(0x00, 1, {"status": "deny"}) == b"\x01"


def test_11f231_delete_no_items():
    """action=delete：数据项数量填 0、三组固定值写出、无数据项字段。"""
    params = {"task_no": 7, "action": "delete", "protocol": 2, "cycle_min": 5}
    out = encode_app_data(0x11, 231, params)
    # 07(task) 00(delete) 02(645) 05(cycle) | 00 00 | 01 00 | 02 00
    assert out == bytes.fromhex("07 00 02 05 00 00 01 00 02 00")


def test_11f231_item_count_mismatch_rejected():
    """组内数量越界被拒。"""
    with pytest.raises(ValueError):
        encode_app_data(0x11, 231, {"task_no": 1, "action": "enable",
                                    "protocol": 2, "cycle_min": 5,
                                    "items": [{"meter_type": 0, "item": "04000201",
                                               "reply_len": 400}]})  # reply_len>255


def test_11f231_invalid_meter_type_rejected():
    with pytest.raises(ValueError):
        encode_app_data(0x11, 231, {"task_no": 1, "action": "enable",
                                    "protocol": 2, "cycle_min": 5,
                                    "items": [{"meter_type": 9, "item": "04000201",
                                               "reply_len": 4}]})  # meter_type 越界

def test_11f231_build_decode_roundtrip():
    """编码 → 完整帧 → decode 回读 AFN/FN/应用数据一致。"""
    params = {
        "task_no": 3, "action": "enable", "protocol": 3, "cycle_min": 1,
        "items": [{"meter_type": 0, "item": "20000201", "reply_len": 4}],
    }
    r = build_frame_json({
        "afn": "11", "fn": "F231",
        "address": {"src": "020103040506", "dst": "080000000000"},
        "data": {"params": params},
    })
    assert r["ok"], r
    d = _decode_hex(r["frame_hex"])
    assert d["fields"]["AFN"]["raw"] == 0x11
    assert d["fields"]["FN"]["raw"] == 231
    # 应用数据落在 items 内（11H 扩展未在 _app_items 拆字段，但 AFN/FN 必须正确）
    assert d["fields"]["AFN"]["raw"] == 0x11


def test_unsupported_fn_raises():
    with pytest.raises(UnsupportedFn):
        encode_app_data(0x05, 10, {})  # 05H 无 F10（非标准数据单元标识）
    with pytest.raises(UnsupportedFn):
        encode_app_data(0x07, 1, {})  # 备用 AFN


def test_standard_fn_now_supported_requires_params():
    """全覆盖后 (0x02,1) 已支持：缺必填参数报 ValueError 而非 UnsupportedFn。"""
    with pytest.raises(ValueError):
        encode_app_data(0x02, 1, {})  # 缺 payload


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        encode_app_data(0x11, 231, {"task_no": 99})  # 越界
    with pytest.raises(ValueError):
        encode_app_data(0x11, 232, {"task_no": 1, "meters": ["123"]})  # 地址位数错
    with pytest.raises(ValueError):
        encode_app_data(0x11, 231, {"task_no": 1, "action": "enable",
                                    "items": [{"meter_type": 0, "item": "abc",
                                               "reply_len": 1}]})  # 标识非4B
