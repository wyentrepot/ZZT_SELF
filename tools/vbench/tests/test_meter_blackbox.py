"""sim_meter 黑盒/白盒测试（vbench 虚拟串口台架）。

黑盒：FakeSTA 经虚拟串口与 sim_meter 通道交互，只断言字节流行为。
白盒：channel/brain/journal/服务装配的内部状态断言。
"""
from __future__ import annotations

import json
import time

import pytest

from tools.vbench.fake_sta import BCAST_99, BCAST_AA, BCAST_FE, FakeSTA
from tools.vbench.frames645 import (bcd_int, build_645, parse_645, un33)
from tools.vbench.pair import VirtualPair

METER_ADDR = "999999999999"
METER_ADDR_WIRE = b"\x99" * 6


def open_meter(pair: VirtualPair, addr: str = METER_ADDR, **kw):
    """在台架 slave 路径上按真实代码打开 sim_meter 通道。"""
    from sim_meter.journal import MeterJournal
    from sim_meter.serial_channel import MeterSerialChannel
    from sim_meter.meter import MeterBrain

    brain = MeterBrain()
    brain.set_address(addr)
    brain.set_register("00010000", "12345.67", scale=2, length=8)
    ch = MeterSerialChannel(
        brain, port=pair.slave_path, baudrate=kw.pop("baudrate", 115200),
        parity="N",   # WSL pts 不接受 PARENB；线路奇偶与字节内容无关
        journal=MeterJournal(port=pair.slave_path, log_dir=kw.pop("log_dir", None))
        if "log_dir" in kw else None, **kw)
    ch.open()
    return ch


@pytest.fixture()
def pair():
    with VirtualPair() as p:
        yield p


# ---------------------------------------------------------------------------
# 黑盒：绑表节奏与抄表
# ---------------------------------------------------------------------------
class TestBlackbox:
    def test_multiport_bind_and_read(self, tmp_path):
        """多路虚拟串口各挂一块表：扫掠绑定 + 抄读电能与预设一致。"""
        from tools.vbench.pair import VirtualPair as VP
        pa, pb = VP(), VP()
        try:
            ch_a = open_meter(pa, "999999999999", log_dir=tmp_path)
            ch_b = open_meter(pb, "013300000001", log_dir=tmp_path)
            sta_a, sta_b = FakeSTA(pa), FakeSTA(pb)
            addr_a = sta_a.sweep_until_bind()
            addr_b = sta_b.sweep_until_bind()
            assert addr_a == METER_ADDR_WIRE
            assert addr_b is not None
            assert sta_a.read_energy(addr_a) == 1234567   # 12345.67 kWh
            assert sta_b.read_energy(addr_b) is not None
            assert ch_a.brain.tx_count >= 3
        finally:
            for p in (pa, pb):
                p.close()

    def test_three_broadcast_kinds_answered(self, pair):
        """99×6 / AA×6 / FE×6 三种广播 13H 均应答 93H（实机 STA 三种轮换）。"""
        open_meter(pair)
        sta = FakeSTA(pair)
        for bcast in (BCAST_99, BCAST_AA, BCAST_FE):
            pair.write(build_645(bcast, 0x13, preamble_fe=4))
            frames = sta._read_frames(0.8)
            assert frames, f"{bcast[:1].hex()}×6 广播未应答"
            _, ctrl, data = parse_645(frames[-1])
            assert ctrl == 0x93
            assert data[:6] == METER_ADDR_WIRE    # 93H 数据域为地址原文（不加 33）

    def test_dirty_stream_answers_exactly_one_frame(self, pair):
        """脏字节 + 698 帧 + 合法 645 混流：只应答合法帧。"""
        open_meter(pair)
        sta = FakeSTA(pair)
        junk = bytes(range(64)) + b"\x00\xFF" * 8
        frame_698 = bytes.fromhex("6817004345AAAAAAAAAAAA10DA1A16")  # 实测 698 读地址
        pair.write(junk + frame_698 + build_645(BCAST_99, 0x13, preamble_fe=4))
        frames = sta._read_frames(1.0)
        assert len(frames) == 1
        assert parse_645(frames[0])[1] == 0x93

    def test_wrong_address_not_answered(self, pair):
        """单表帧发给别的地址：不应答（非本表地址）。"""
        open_meter(pair, "999999999999")
        sta = FakeSTA(pair)
        pair.write(build_645(b"\x01\x00\x00\x00\x33\x01", 0x13))
        assert sta._read_frames(0.6) == []


# ---------------------------------------------------------------------------
# 白盒：通道 / 波特率 / journal / 服务装配
# ---------------------------------------------------------------------------
class TestWhitebox:
    def test_channel_lifecycle_and_status(self, pair):
        ch = open_meter(pair)
        assert ch.open() is True and ch.status()["open"] is True
        assert ch.status()["port"] == pair.slave_path
        ch.close()
        assert ch.status()["open"] is False
        ch.close()

    def test_baud_hot_switch_via_17h(self, pair):
        """17H 改速率：97H 先回，通道重开，读线程存活、链路可用。"""
        ch = open_meter(pair)
        sta = FakeSTA(pair)
        assert sta.set_baud(METER_ADDR_WIRE, 0x04) is True   # 0x04=2400
        deadline = time.monotonic() + 2.0
        while ch.status()["baudrate"] == 115200 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ch.status()["baudrate"] == 2400
        pair.write(build_645(BCAST_99, 0x13, preamble_fe=4))
        frames = sta._read_frames(1.0)
        assert frames and parse_645(frames[-1])[1] == 0x93
        ch.close()

    def test_journal_records_rx_tx(self, pair, tmp_path):
        ch = open_meter(pair, log_dir=tmp_path)
        sta = FakeSTA(pair)
        pair.write(build_645(BCAST_99, 0x13))
        sta._read_frames(0.8)
        time.sleep(0.2)
        files = list(tmp_path.glob("sm-*.jsonl"))
        assert files
        entries = [json.loads(x) for x in
                   files[0].read_text(encoding="utf-8").splitlines() if x.strip()]
        dirs = {e.get("dir") for e in entries}
        assert {"rx", "tx"} <= dirs
        ch.close()

    def test_service_registry_blocks_double_owner(self, pair):
        """工作台装配（SimMeterService）：他方占用报冲突，释放后可开、关闭即还。"""
        from shared.serial_resources import SerialResourceRegistry
        from sim_meter.api import OpenSpec, SimMeterService

        registry = SerialResourceRegistry()
        registry.reserve("module-serial:x", label="模块日志",
                         aliases=[pair.slave_path])
        svc = SimMeterService(resource_registry=registry)
        with pytest.raises(Exception) as exc:
            svc.open(OpenSpec(port=pair.slave_path, parity="N"))
        assert "占用" in str(exc.value)
        registry.release("module-serial:x")
        svc.open(OpenSpec(port=pair.slave_path, parity="N"))
        assert "simeter" in {r["owner_id"] for r in registry.snapshot()}
        svc.close()
        assert "simeter" not in {r["owner_id"] for r in registry.snapshot()}
