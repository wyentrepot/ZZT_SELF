"""scenario_codec 转换层测试（阶段3）。

验证 send 语义描述 + profile → 完整帧：
- 地址域 A1/A3 按方向装配（module_id=1 带地址域）；
- 广播 A3=999999999999H；
- seq 自动分配注入 info.seq；
- 扁平 items 经 encode_app_data 编码；
- 档案 ref 查 profile 解析；
- raw 直发报错、未覆盖 Fn 报错。
"""
from __future__ import annotations

import pytest

from sim_concentrator.frame_codec import decode_frame
from sim_concentrator.scenario_codec import (
    ScenarioCodecError,
    build_address,
    build_send,
    load_profile,
    resolve_arch_addr,
)
from parser_lib.adapters.adapter_10376 import UnsupportedFn

PROFILE = {
    "id": "anhui",
    "cco_addr": "020103040506",
    "comm_mode": 3,
    "seq_auto": True,
    "task_range": {"min": 1, "max": 15},
    "sta_archives": [
        {"id": "sta1", "addr": "013300000001", "protocol": 645, "phase": 0},
        {"id": "sta2", "addr": "013300000002", "protocol": 645, "phase": 1},
    ],
}


def _afn(d: dict) -> int:
    return d["fields"]["AFN"]["raw"]


def _fn(d: dict) -> int:
    return d["fields"]["FN"]["raw"]


def _addr(d: dict) -> str:
    return d["fields"]["地址域A"]["value"]


def _seq(d: dict) -> int:
    desc = d["fields"]["信息域R"]["desc"]  # 如 "relay_level=0 module_id=1 seq=3"
    return int(desc.split("seq=")[1])


def test_load_profile_default_dir():
    prof = load_profile("anhui")
    assert prof["id"] == "anhui"
    assert prof["cco_addr"] == "020103040506"


def test_load_profile_missing_raises():
    with pytest.raises(ScenarioCodecError):
        load_profile("no_such_profile")


def test_build_address_downstream():
    """下行：A1=cco_addr, A3=sta_addr。"""
    a = build_address(PROFILE, {}, "down", explicit_dst="013300000001")
    assert a == {"src": "020103040506", "dst": "013300000001"}


def test_build_address_upstream():
    """上行：A1=sta_addr, A3=cco_addr（应答回源）。"""
    a = build_address(PROFILE, {}, "up", explicit_dst="013300000001")
    assert a == {"src": "013300000001", "dst": "020103040506"}


def test_build_address_broadcast():
    """广播：A3=999999999999H。"""
    a = build_address(PROFILE, {"broadcast": True}, "down")
    assert a["src"] == "020103040506"
    assert a["dst"] == "999999999999"


def test_build_address_from_params_addr():
    """params 显式 addr 作为目标。"""
    a = build_address(PROFILE, {"addr": "080000000000"}, "down")
    assert a["dst"] == "080000000000"


def test_resolve_arch_ref():
    meters = resolve_arch_addr([{"ref": "sta1"}, {"ref": "sta2"}], PROFILE)
    assert meters == ["013300000001", "013300000002"]
    with pytest.raises(ScenarioCodecError):
        resolve_arch_addr([{"ref": "nope"}], PROFILE)


def test_build_send_downstream_full_frame():
    """send 语义 → 完整帧：地址域/seq/AFN/FN 正确（下行 A3=显式 dst）。"""
    raw = build_send(
        {"afn": "11", "fn": "F231",
         "params": {"task_no": 1, "action": "enable", "protocol": 2, "cycle_min": 5,
                    "dst": "013300000001",
                    "items": [{"meter_type": 0, "item": "04000201", "reply_len": 4}]}},
        PROFILE, seq=3)
    d = decode_frame(raw)
    assert _afn(d) == 0x11 and _fn(d) == 231
    assert _seq(d) == 3
    assert _addr(d).startswith("020103040506013300000001")


def test_build_send_seq_auto_injected():
    raw = build_send({"afn": "10", "fn": "F4"}, PROFILE, seq=7)
    d = decode_frame(raw)
    assert _seq(d) == 7


def test_build_send_broadcast():
    raw = build_send({"afn": "10", "fn": "F4", "params": {"broadcast": True}},
                     PROFILE, seq=1)
    d = decode_frame(raw)
    assert d["fields"]["地址域A"]["value"].endswith("999999999999")


def test_build_send_unsupported_fn_raises():
    with pytest.raises(UnsupportedFn):
        build_send({"afn": "05", "fn": "F10"}, PROFILE, seq=1)


def test_build_send_raw_removed():
    with pytest.raises(ScenarioCodecError):
        build_send({"afn": "10", "fn": "F4", "raw": "68 17 00 43 ... 16"},
                   PROFILE, seq=1)


def test_build_send_missing_cco_addr():
    """无 profile/cco_addr：降级为无地址域帧（module_id=0），等价旧 CCO 本地帧。"""
    raw = build_send({"afn": "10", "fn": "F4"}, {}, seq=1)
    d = decode_frame(raw)
    # 无地址域：地址域A value 为 "(无)"
    assert d["fields"]["地址域A"]["value"] == "(无)"


def test_build_send_flat_items_encode():
    """扁平 items → appdata 正确（11H-F231 分组）。"""
    raw = build_send(
        {"afn": "11", "fn": 231,
         "params": {"task_no": 7, "action": "enable", "protocol": 2, "cycle_min": 5,
                    "items": [
                        {"meter_type": 0, "item": "02010100", "reply_len": 4},
                        {"meter_type": 1, "item": "04000201", "reply_len": 4},
                    ]}},
        PROFILE, seq=1)
    d = decode_frame(raw)
    assert _afn(d) == 0x11 and _fn(d) == 231
    # 应用数据在帧内：AFN 之后；此处仅验证构帧成功 + AFN/FN 对
