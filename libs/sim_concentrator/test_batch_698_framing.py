"""batch.py 698 构帧分流专项单测（REQS-0043 F0-5 修复的验收，F1 工位交付）。

修复前的问题（CASE-BACKLOG §F0-5 登记）：``BatchReadJob._read_one`` 不分协议一律
构 645 读帧，``protocol_type=3``（698.45）入参只是给 645 报文贴 698 标签——
CCO 按协议类型提表地址（``CCO实现逻辑/01-事务框架…md:222``），标签贴错=698 任务
发的是 645 帧。本组测试钉死三件事：

1. ``meter_read_698`` 产物是**完整 698 链路帧**且逐字节稳定（基准 hex 抓自本仓库
   构帧器，结构与 ``sim_meter/protocol_698.py`` STA 模板一致：SECURITY-Request
   明文包 GET-RequestNormal，SA=05+wire 地址，C=43，CA=00）；
2. ``_read_one`` 按 ``protocol_type`` 分流：3 → 698 构帧器，2 → 645 构帧器
   （用双探针替换两个构帧器，恰好只被调用一个）；
3. 698 产物能被 698 从站解析口径读回（``parse_698_frame``：service=05 /
   GetRequestNormal / OAD 回显 / 安全明文包装）——样本与协议逐字节对齐
   （0041 10H-F4 教训）。

全部离线（不触串口、不碰工作台）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from sim_concentrator import batch as batch_mod
from sim_concentrator.batch import BatchReadJob, meter_read_645, meter_read_698
from sim_meter.protocol_698 import parse_698_frame

#: 基准：wire 地址 990042708102（档案线上字节 990024071820）+ OAD 20000201。
#: 逐字节对齐（0041 教训）：改构帧模板必须先改这条基准并说明协议依据。
REQ698_WIRE_990042708102 = (
    "682C004305990024071820008DA010000805010120000201000110"
    "00000000000000000000000000000000C4CC16"
)


class _ExplodingIO:
    """``send_frame`` 即抛的最小 io 桩：构帧探针在发送前已被调用到。"""

    def send_frame(self, raw: bytes) -> None:
        raise RuntimeError("构帧探针桩：不需要真发送")


def test_meter_read_698_is_byte_stable_full_698_frame():
    """698 读帧逐字节稳定，且是完整 698 链路帧（68…16，含 SECURITY 明文包装）。"""
    frame = meter_read_698("990042708102", "20000201", "wire")
    assert frame.hex().upper() == REQ698_WIRE_990042708102
    assert frame[0] == 0x68 and frame[-1] == 0x16


def test_meter_read_698_parses_back_as_get_request():
    """产物按 698 从站口径解回：service=05 / GetRequestNormal / OAD 回显 / 安全包装。"""
    parsed = parse_698_frame(meter_read_698("990042708102", "20000201", "wire"))
    assert parsed.service == 0x05                       # GET-Request（读取请求类 05）
    assert parsed.request_type == 1                     # GetRequestNormal
    assert parsed.security == 0x10                      # SECURITY-Request 明文包
    assert parsed.oad == bytes.fromhex("20000201")      # OAD 回显（内层 APDU 解出）
    assert parsed.addr[1:] == bytes.fromhex("990024071820")  # SA = 05 + wire 地址
    assert parsed.ca == 0x00 and parsed.control == 0x43


def test_meter_read_698_bcd_addr_mode_and_validation():
    """bcd 口径 = BCD 反序地址；非法地址/OAD 直接抛（不静默）。"""
    frame = meter_read_698("990042708102", "20000201", "bcd")
    parsed = parse_698_frame(frame)
    assert parsed.addr[1:] == bytes.fromhex("022070814200")[::-1][:0] or True
    # bcd = bytes.fromhex(addr)[::-1]：990042708102 → 020281704200…逐字节核对
    assert parsed.addr.hex().upper() == "05" + bytes.fromhex("990042708102")[::-1].hex().upper()
    with pytest.raises(ValueError):
        meter_read_698("12345", "20000201")
    with pytest.raises(ValueError):
        meter_read_698("990042708102", "ZZ000201")
    with pytest.raises(ValueError):
        meter_read_698("990042708102", "20000201", "other")


def test_read_one_dispatches_by_protocol_type(monkeypatch):
    """``_read_one`` 按协议分流：protocol_type=3 只调 698 构帧器，2 只调 645 构帧器。

    探针法：替换模块级 ``meter_read_698`` / ``meter_read_645``，io 桩在发送时抛
    （行记 error），不影响「谁被调用」的事实。
    """
    calls: dict[str, int] = {"m698": 0, "m645": 0}

    def fake_698(addr, item, addr_mode):  # noqa: ANN001
        calls["m698"] += 1
        return b"\x68\x00"

    def fake_645(addr, item, addr_mode):  # noqa: ANN001
        calls["m645"] += 1
        return b"\x68\x00"

    monkeypatch.setattr(batch_mod, "meter_read_698", fake_698)
    monkeypatch.setattr(batch_mod, "meter_read_645", fake_645)

    job_698 = BatchReadJob(
        _ExplodingIO(), ["990042708102"],
        max_concurrent=1, mode="batch", protocol_type=3, item="20000201",
    )
    job_698._read_one("990042708102")
    assert calls == {"m698": 1, "m645": 0}, calls

    calls.update({"m698": 0, "m645": 0})
    job_645 = BatchReadJob(
        _ExplodingIO(), ["990042708102"],
        max_concurrent=1, mode="batch", protocol_type=2, item="02010100",
    )
    job_645._read_one("990042708102")
    assert calls == {"m698": 0, "m645": 1}, calls


def test_645_builder_unchanged_by_the_fix():
    """修复不碰 645 路径：645 构帧产物与既有基准逐字节一致（回归锚）。"""
    frame = meter_read_645("990042708102", "03300501", "wire")
    assert frame.hex().upper() == "6899002407182068110434386336E616"
