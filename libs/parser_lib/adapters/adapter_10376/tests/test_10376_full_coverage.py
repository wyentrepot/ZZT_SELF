"""Q/GDW 10376.2 全量 AFN/Fn 覆盖回归（对照蒸馏文档 §3/§4/§5）。

对标准定义的全部 73 个 Fn（F0H 厂家自定义除外）逐条验证：
encode_app_data(params, direction) → build_frame → adapter.decode 闭环，
AFN/FN 正确且无 CS 告警；另附关键 Fn 的取值级抽查。
蒸馏文档未给出上行格式的 10H-F104 单独断言 UnsupportedFn。
"""
from __future__ import annotations

import pytest

from parser_lib.adapters.adapter_10376 import (
    QGDW103762Adapter,
    UnsupportedFn,
    build_frame,
    encode_app_data,
)

# 标准 §3 全帧类型清单（F0H 厂家自定义不计入）
STANDARD_FN_TABLE = {
    0x00: [1, 2],
    0x01: [1, 2, 3],
    0x02: [1],
    0x03: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 100],
    0x04: [1, 2, 3],
    0x05: [1, 2, 3, 4, 5, 6, 16, 100, 101, 200],
    0x06: [1, 2, 3, 4, 5],
    0x10: [1, 2, 3, 4, 5, 6, 7, 9, 21, 31, 40, 100, 101, 104, 111, 112],
    0x11: [1, 2, 3, 4, 5, 6, 100, 101, 102],
    0x12: [1, 2, 3],
    0x13: [1],
    0x14: [1, 2, 3, 4],
    0x15: [1],
    0xF1: [1],
}

Q10 = {"start": 0, "count": 16}

# (afn, fn, direction, params) — 双向格式不同的 Fn 两个方向都进矩阵
CASES = [
    # 00H 确认/否认（上行）
    (0x00, 1, "up", {}),
    (0x00, 1, "up", {"channels": 1, "wait": 513, "processed": 1}),
    (0x00, 1, "up", {"status": "deny"}),
    (0x00, 2, "up", {"err": 4}),
    # 01H 初始化（下行，无数据单元）
    (0x01, 1, "down", {}),
    (0x01, 2, "down", {}),
    (0x01, 3, "down", {}),
    # 02H 数据转发（上下行同构）
    (0x02, 1, "down", {"protocol": 2, "payload": "AABBCC"}),
    (0x02, 1, "up", {"protocol": 2, "payload": "AABBCC"}),
    # 03H 查询数据（下行查询 / 上行应答）
    (0x03, 1, "down", {}),
    (0x03, 1, "up", {"vendor": "AB", "chip": "CD", "day": 1, "month": 2,
                     "year": 26, "version": "0102"}),
    (0x03, 2, "down", {}),
    (0x03, 2, "up", {"noise": 9}),
    (0x03, 3, "down", {"start": 0, "count": 16}),
    (0x03, 3, "up", {"total": 2, "nodes": [
        {"addr": "013300000001", "quality": 12, "relay": 1, "listen": 5},
        {"addr": "013300000002", "quality": 9, "relay": 0, "listen": 2}]}),
    (0x03, 4, "down", {}),
    (0x03, 4, "up", {"addr": "020103040506"}),
    (0x03, 5, "down", {}),
    (0x03, 5, "up", {"mode": 1, "channel": 2,
                     "rates": [{"unit": 1, "rate": 9600}]}),
    (0x03, 6, "down", {"duration": 10}),
    (0x03, 6, "up", {"status": 1}),
    (0x03, 7, "down", {}),
    (0x03, 7, "up", {"timeout": 90}),
    (0x03, 8, "down", {}),
    (0x03, 8, "up", {"channel": 1, "power": 0}),
    (0x03, 9, "down", {"protocol": 2, "payload": "AABB"}),
    (0x03, 9, "up", {"delay": 30, "protocol": 2, "payload": "AABB"}),
    (0x03, 10, "down", {}),
    (0x03, 10, "up", {"mode": "000000000001", "monitor_timeout": 90,
                      "broadcast_timeout": 30, "max_frame": 255,
                      "max_file_pkt": 512, "upgrade_wait": 10,
                      "addr": "020103040506", "max_nodes": 1000,
                      "cur_nodes": 500, "pub_date": "191231",
                      "rec_date": "200601",
                      "vendor_ver": "010203040506070809",
                      "rates": [{"unit": 1, "rate": 9600}]}),
    (0x03, 11, "down", {"afn": 3}),
    (0x03, 11, "up", {"afn": 3, "support": [1, 2, 10]}),
    (0x03, 12, "down", {}),
    (0x03, 12, "up", {"vendor": "AB", "id_format": 2, "id": "000102030405"}),
    (0x03, 16, "down", {}),
    (0x03, 16, "up", {"band": 2}),
    (0x03, 100, "down", {}),
    (0x03, 100, "up", {"threshold": 96}),
    # 04H 链路接口检测（下行；上行=00H 确认帧）
    (0x04, 1, "down", {"duration": 5}),
    (0x04, 2, "down", {}),
    (0x04, 3, "down", {"rate": 1, "addr": "020103040506", "protocol": 2,
                       "payload": "AABB"}),
    # 05H 控制命令（下行；上行=00H 确认帧）
    (0x05, 1, "down", {"addr": "020103040506"}),
    (0x05, 2, "down", {"enable": 1}),
    (0x05, 3, "down", {"ctrl": 3, "payload": "AABB"}),
    (0x05, 4, "down", {"timeout": 60}),
    (0x05, 5, "down", {"channel": 254, "power": 0}),
    (0x05, 6, "down", {"enable": 1}),
    (0x05, 16, "down", {"band": 1}),
    (0x05, 100, "down", {"threshold": 96}),
    (0x05, 101, "down", {"sec": 0, "min": 30, "hour": 12, "day": 29,
                         "mon": 8, "year": 26}),
    (0x05, 200, "down", {"enable": 1}),
    # 06H 主动上报（上行）
    (0x06, 1, "up", {"nodes": [{"addr": "123456789012", "protocol": 2, "seq": 1}]}),
    (0x06, 2, "up", {"seq": 1, "protocol": 2, "up_len": 5, "payload": "AABBCC"}),
    (0x06, 3, "up", {"type": 2}),
    (0x06, 4, "up", {"nodes": [{"addr": "123456789012", "protocol": 2, "seq": 1,
                                "dev_type": 1,
                                "subs": [{"addr": "013300000001", "protocol": 2}]}]}),
    (0x06, 5, "up", {"dev_type": 2, "protocol": 4, "event": 1,
                     "addrs": ["013300000001", "013300000002"]}),
    (0x06, 5, "up", {"dev_type": 0, "protocol": 4,
                     "meters": [{"addr": "010203040506", "power": 1}]}),
    (0x06, 5, "up", {"dev_type": 2, "protocol": 5,
                     "rejected": [{"addr": "010203040506", "dev_type": 1}]}),
    # 10H 路由查询（下行查询 / 上行应答）
    (0x10, 1, "down", {}),
    (0x10, 1, "up", {"total": 100, "max": 2000}),
    (0x10, 2, "down", Q10),
    (0x10, 2, "up", {"total": 100, "nodes": [
        {"addr": "013300000001", "info": {"quality": 11, "relay": 2, "phase": 1}}]}),
    (0x10, 3, "down", {"addr": "020103040506"}),
    (0x10, 3, "up", {"nodes": [{"addr": "013300000001", "info": 4370}]}),
    (0x10, 4, "down", {}),
    (0x10, 4, "up", {"status": 0x18, "total": 100, "read": 90,
                     "relay_read": 10, "switch": 0x80, "rate": 9600,
                     "relay_level": [1, 2, 3], "steps": [2, 2, 2]}),
    (0x10, 5, "down", Q10),
    (0x10, 5, "up", {"total": 100, "nodes": [{"addr": "013300000001", "info": 0}]}),
    (0x10, 6, "down", Q10),
    (0x10, 6, "up", {"total": 100, "nodes": [{"addr": "013300000001", "info": 1}]}),
    (0x10, 7, "down", Q10),
    (0x10, 7, "up", {"total": 10, "nodes": [
        {"addr": "010203040506", "node_type": 1, "vendor": "AB",
         "id": "000102030405", "id_format": 2}]}),
    (0x10, 9, "down", {}),
    (0x10, 9, "up", {"scale": 500}),
    (0x10, 21, "down", {"start": 0, "count": 4}),
    (0x10, 21, "up", {"total": 10, "start": 0, "nodes": [
        {"addr": "010203040506", "tei": 1, "proxy": 0, "role": 4, "level": 0}]}),
    (0x10, 31, "down", {"start": 0, "count": 4}),
    (0x10, 31, "up", {"total": 10, "start": 0,
                      "nodes": [{"addr": "010203040506", "info": 1}]}),
    (0x10, 40, "down", {"dev_type": 2, "addr": "010203040506", "id_type": 1}),
    (0x10, 40, "up", {"dev_type": 2, "addr": "010203040506", "id_type": 1,
                      "id": "00112233"}),
    (0x10, 100, "down", {}),
    (0x10, 100, "up", {"scale": 50}),
    (0x10, 101, "down", {"start": 0, "count": 4}),
    (0x10, 101, "up", {"total": 5, "nodes": [
        {"addr": "013300000001", "info": 1, "ver": "010203"}]}),
    (0x10, 104, "down", {}),
    (0x10, 111, "down", {}),
    (0x10, 111, "up", {"self_nid": 1, "self_master": "010203040506",
                       "neighbors": [2, 3]}),
    (0x10, 112, "down", {"start": 0, "count": 2}),
    (0x10, 112, "up", {"total": 5, "start": 0, "nodes": [
        {"addr": "010203040506", "dev_type": 2, "chip_id": "01" * 24,
         "ver": "0102"}]}),
    # 11H 路由设置（下行；上行=00H 确认帧）
    (0x11, 1, "down", {"action": "add", "addr": "080000000000", "protocol": 3}),
    (0x11, 1, "down", {"nodes": [{"addr": "020103040506", "protocol": 2}]}),
    (0x11, 2, "down", {"meters": ["013300000001", "013300000002"]}),
    (0x11, 3, "down", {"addr": "013300000001", "relays": ["020103040506"]}),
    (0x11, 4, "down", {"mode": 3, "rate": 9600, "rate_unit": 1}),
    (0x11, 5, "down", {"sec": 0, "min": 0, "hour": 1, "day": 1, "mon": 9,
                       "year": 26, "duration": 30, "retry": 3, "slices": 4}),
    (0x11, 6, "down", {}),
    (0x11, 100, "down", {"scale": 500}),
    (0x11, 101, "down", {}),
    (0x11, 102, "down", {}),
    # 12H 路由控制（下行，无数据单元）
    (0x12, 1, "down", {}),
    (0x12, 2, "down", {}),
    (0x12, 3, "down", {}),
    # 13H 路由数据转发（双向异构）
    (0x13, 1, "down", {"protocol": 2, "delay_flag": 1,
                       "subs": ["020103040506"], "payload": "AABB"}),
    (0x13, 1, "up", {"up_len": 3, "protocol": 2, "payload": "AABBCC"}),
    # 14H 路由数据抄读（上行请求 / 下行应答）
    (0x14, 1, "down", {"flag": 2, "delay_flag": 1, "payload": "AABB",
                       "subs": ["020103040506"]}),
    (0x14, 1, "up", {"phase": 1, "addr": "013300000001", "seq": 5}),
    (0x14, 2, "down", {"sec": 0, "min": 0, "hour": 0, "day": 1, "mon": 9,
                       "year": 26}),
    (0x14, 2, "up", {}),
    (0x14, 3, "down", {"payload": "AABB"}),
    (0x14, 3, "up", {"addr": "013300000001", "delay": 15, "payload": "AABB"}),
    (0x14, 4, "down", {"type": 1, "item": "02010100", "content": "AABB"}),
    (0x14, 4, "up", {"type": 2, "item": "04000201"}),
    # 15H 文件传输（下行 / 上行段确认）
    (0x15, 1, "down", {"file_id": 3, "attr": 0, "cmd": 0, "total_segs": 2,
                       "seg_id": "00000001", "data": "AABBCC"}),
    (0x15, 1, "up", {"seg_id": "00000001"}),
    # F1H 并发抄表（双向异构）
    (0xF1, 1, "down", {"protocol": 2, "payload": "AABB"}),
    (0xF1, 1, "up", {"protocol": 2, "payload": "AABB"}),
]


@pytest.fixture
def adapter():
    return QGDW103762Adapter()


def _item(frame, name):
    for it in frame.items:
        if it.name == name:
            return it
    return None


@pytest.mark.parametrize("afn,fn,direction,params", CASES)
def test_full_fn_matrix_roundtrip(adapter, afn, fn, direction, params):
    """每个标准 (AFN, Fn)：参数编码 → 构帧 → 解析回读，AFN/FN 一致且 CS 通过。"""
    appdata = encode_app_data(afn, fn, params, direction=direction)
    frame = build_frame(afn=afn, fn=fn, direction=direction, appdata=appdata)
    fr = adapter.decode(frame)
    assert fr.structure == "1376.2"
    fields = {f.name: f for f in fr.fields}
    assert fields["AFN"].raw == afn
    assert fields["FN"].raw == fn
    assert fr.warnings == []  # CS 必须通过


def test_matrix_covers_standard_table():
    """矩阵用例必须完整覆盖标准 §3 的 73 个 Fn（F0H 厂家自定义除外）。"""
    assert sum(len(v) for v in STANDARD_FN_TABLE.values()) == 73
    seen = {}
    for afn, fn, _direction, _params in CASES:
        seen.setdefault(afn, set()).add(fn)
    for afn, fns in STANDARD_FN_TABLE.items():
        missing = set(fns) - seen.get(afn, set())
        assert not missing, f"AFN 0x{afn:02X} 缺矩阵用例: F{sorted(missing)}"


# ------------------------- 取值级抽查 -------------------------

def test_10f21_topology_up_values(adapter):
    """10H-F21 上行拓扑：TEI/代理/角色/层级逐字段还原。"""
    appdata = encode_app_data(0x10, 21, {"total": 10, "start": 0, "nodes": [
        {"addr": "010203040506", "tei": 257, "proxy": 1, "role": 4, "level": 0}]},
        direction="up")
    fr = adapter.decode(build_frame(afn=0x10, fn=21, direction="up",
                                    appdata=appdata))
    assert fr.warnings == []
    assert int(_item(fr, "节点1TEI").raw) == 257
    assert int(_item(fr, "节点1代理节点标识").raw) == 1
    assert "主节点CCO" in _item(fr, "节点1网络拓扑").value
    assert "层级=0" in _item(fr, "节点1网络拓扑").value


def test_06f5_power_event_values(adapter):
    """06H-F5 停复电事件（协议类型=04H）：事件类型 + 地址序列。"""
    appdata = encode_app_data(0x06, 5, {"dev_type": 2, "protocol": 4,
                                        "event": 1,
                                        "addrs": ["013300000001"]},
                              direction="up")
    fr = adapter.decode(build_frame(afn=0x06, fn=5, direction="up",
                                    appdata=appdata))
    assert "停电事件" in _item(fr, "事件类型").value
    assert _item(fr, "通信单元1地址").value == "013300000001"


def test_06f5_rejected_nodes_values(adapter):
    """06H-F5 台区改切拒绝节点（协议类型=05H）。"""
    appdata = encode_app_data(0x06, 5, {"dev_type": 2, "protocol": 5,
                                        "rejected": [{"addr": "010203040506",
                                                      "dev_type": 1}]},
                              direction="up")
    fr = adapter.decode(build_frame(afn=0x06, fn=5, direction="up",
                                    appdata=appdata))
    assert int(_item(fr, "本次上报个数").raw) == 1
    assert _item(fr, "被拒节点1地址").value == "010203040506"


def test_05f3_broadcast_ctrl_phase_recognized(adapter):
    """05H-F3 控制字 03H=相位识别功能（非通用协议类型表）。"""
    appdata = encode_app_data(0x05, 3, {"ctrl": 3, "payload": "AABB"})
    fr = adapter.decode(build_frame(afn=0x05, fn=3, direction="down",
                                    appdata=appdata))
    assert "相位识别功能" in _item(fr, "控制字").value


def test_00f1_wait_little_endian(adapter):
    """00H-F1 等待时间 2B 小端（标准备注1）。"""
    appdata = encode_app_data(0x00, 1, {"channels": 1, "wait": 513},
                              direction="up")
    assert appdata[4:6] == bytes([0x01, 0x02])
    fr = adapter.decode(build_frame(afn=0x00, fn=1, direction="up",
                                    appdata=appdata))
    assert _item(fr, "等待时间").value == "513s"


def test_03f10_full_struct_roundtrip(adapter):
    """03H-F10 上行完整结构：39B 固定段 + 速率列表逐字段还原。"""
    appdata = encode_app_data(0x03, 10, {
        "mode": "000000000001", "monitor_timeout": 90, "broadcast_timeout": 30,
        "max_frame": 255, "max_file_pkt": 512, "upgrade_wait": 10,
        "addr": "020103040506", "max_nodes": 1000, "cur_nodes": 500,
        "pub_date": "191231", "rec_date": "200601",
        "vendor_ver": "010203040506070809",
        "rates": [{"unit": 1, "rate": 9600}]}, direction="up")
    fr = adapter.decode(build_frame(afn=0x03, fn=10, direction="up",
                                    appdata=appdata))
    assert fr.warnings == []
    assert _item(fr, "从节点监控最大超时时间").value == "90s"
    assert _item(fr, "广播命令最大超时时间").value == "30s"
    assert _item(fr, "文件传输最大单包长度").value == "512B"
    assert _item(fr, "支持的最大从节点数量").value == "1000"
    assert "9600" in _item(fr, "通信速率1").value


def test_03f11_bitmap_roundtrip(adapter):
    """03H-F11 AFN 索引位图：支持清单 ↔ 32B 位图双向一致。"""
    appdata = encode_app_data(0x03, 11, {"afn": 3, "support": [1, 2, 10]},
                              direction="up")
    fr = adapter.decode(build_frame(afn=0x03, fn=11, direction="up",
                                    appdata=appdata))
    sup = _item(fr, "支持的数据单元").value
    assert "F1" in sup and "F2" in sup and "F10" in sup


def test_15f1_file_transfer_values(adapter):
    """15H-F1 文件传输下行：标识/属性/段信息还原。"""
    appdata = encode_app_data(0x15, 1, {"file_id": 3, "attr": 0, "cmd": 0,
                                        "total_segs": 2, "seg_id": "00000001",
                                        "data": "AABBCC"})
    fr = adapter.decode(build_frame(afn=0x15, fn=1, direction="down",
                                    appdata=appdata))
    assert "本地通信模块升级文件" in _item(fr, "文件标识").value
    assert int(_item(fr, "总段数").raw) == 2
    assert _item(fr, "段数据长度").value == "3B"


# ------------------------- 边界与显式报错 -------------------------

def test_10f104_up_unsupported():
    """10H-F104 上行格式文档未定义 → 明确报错（下行查询正常）。"""
    with pytest.raises(UnsupportedFn):
        encode_app_data(0x10, 104, {"total": 1, "nodes": []}, direction="up")
    assert encode_app_data(0x10, 104, {}, direction="down") == b""


def test_command_afns_up_hint():
    """04H/05H/11H/12H 上行应答标准规定为 00H 确认帧 → 明确报错并提示。"""
    with pytest.raises(UnsupportedFn, match="00H"):
        encode_app_data(0x05, 1, {"addr": "020103040506"}, direction="up")
    with pytest.raises(UnsupportedFn, match="00H"):
        encode_app_data(0x12, 2, {}, direction="up")
