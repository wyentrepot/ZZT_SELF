"""端到端节奏测试：模拟集中器 → 伪CCO → HPLC跳 → 伪STA → sim_meter。

复现 docs/19 的通过标准（软件仿真版）：simcon 以 13H-F1 路由转发下发
645 抄读，经 FakeCCO 中继（HPLC 跳＝两条虚拟串口＋纯字节泵）、FakeSTA
落到虚拟底座口的 sim_meter，应答原路返回，抄表数据上报集中器。

数据交换只有虚拟串口：FakeCCO 与 FakeSTA 之间无任何内存共享，只有
carrier 口字节泵搬运的字节流。
"""
from __future__ import annotations

import pytest

from tools.vbench.bench import VirtualBench, wait_until
from tools.vbench.frames13762 import app_data, decode_frame
from tools.vbench.frames645 import bcd_int, parse_645, un33

METERS = ["999999999999", "013300000001"]


@pytest.fixture()
def bench():
    with VirtualBench.create(meters=len(METERS), meter_addrs=METERS) as b:
        yield b


@pytest.fixture()
def bench_with_sut(bench):
    """台架 + 被测件装配：simcon SerialIO 与各路 sim_meter 通道。"""
    from shared.serial_resources import SerialResourceRegistry
    from sim_concentrator.serial_io import SerialIO
    from sim_meter.journal import MeterJournal
    from sim_meter.meter import MeterBrain
    from sim_meter.serial_channel import MeterSerialChannel

    io = SerialIO(port=bench.local_pair.slave_path, baudrate=115200,
                  parity="N", resource_registry=SerialResourceRegistry(),
                  journal=None)
    io.open()
    channels = []
    for path, addr in zip(bench.meter_paths(), METERS):
        brain = MeterBrain()
        brain.set_address(addr)
        brain.set_register("00010000", "12345.67", scale=2, length=8)
        ch = MeterSerialChannel(brain, port=path, baudrate=115200, parity="N",
                                journal=MeterJournal(port=path, log_dir=None))
        ch.open()
        channels.append(ch)
    bench.start()
    yield bench, io, channels
    io.close()
    for ch in channels:
        ch.close()


def _read_energy_via_simcon(bench, io, addr: str) -> dict:
    """以 simcon 语义步骤下发 13-F1 抄读，解析应答 645 的电能 BCD。"""
    from sim_concentrator.runner import execute_task

    task = {
        "id": f"vbench.e2e.{addr}",
        "enable_responder": True,
        "responders": [{"id": "vbench.14f2", "match": {"afn": 0x14, "fn": 2},
                        "reply": {"afn": 0x14, "fn": 2, "format": "local",
                                  "userdata_builder": "echo"}}],
        "steps": [{"name": f"路由抄读 {addr}",
                   "send": {"afn": "13", "fn": "F1",
                            "params": {"protocol": 2, "subs": [],
                                       "payload": bench.read_energy_payload(addr)}},
                   "expect": {"afn": 0x13, "fn": 1},
                   "expect_timeout": 6.0}],
    }
    result = execute_task(task, io=io)
    step = result["steps"][0]
    out = {"ok": step["result"] == "pass", "matched": step.get("matched"),
           "energy_bcd": None}
    if out["matched"]:
        decoded = decode_frame(bytes.fromhex(out["matched"]))
        payload = app_data(decoded)[4:]          # up_len(2)+protocol(1)+len(1) 之后
        raw645 = bytes.fromhex(payload) if isinstance(payload, str) else payload
        _, ctrl, data = parse_645(raw645)
        out["ctrl"] = hex(ctrl)
        if ctrl == 0x91 and len(data) >= 12:
            out["energy_bcd"] = bcd_int(un33(data[4:12]))
    return out


class TestEndToEnd:
    def test_bus_warmup_then_bind(self, bench_with_sut):
        """bpsCheck 应答建立本地总线 → 各路 STA 扫掠绑定到表。"""
        bench, io, channels = bench_with_sut
        warm = bench.bus_warmup(io)
        assert warm["summary"]["verdict"] == "pass"
        assert wait_until(lambda: bench.cco.bus_ok), "本地总线未建立"
        bound = bench.bind_all()
        assert all(b is not None for b in bound), bound

    def test_meter_reading_reported_to_concentrator(self, bench_with_sut):
        """通过标准（仿真）：抄表数据经全链路上报集中器，电能值与预设一致。"""
        bench, io, channels = bench_with_sut
        bench.bus_warmup(io)
        bench.bind_all()
        bench.start_relay()
        for addr in METERS:
            out = _read_energy_via_simcon(bench, io, addr)
            assert out["ok"], out
            assert out["ctrl"] == "0x91"
            assert out["energy_bcd"] == 1234567, addr   # 12345.67 kWh ×100

    def test_cco_relay_counters(self, bench_with_sut):
        """白盒交叉验证：FakeCCO 收到 645 应答并发出等量 13-F1 上行。"""
        bench, io, channels = bench_with_sut
        bench.bus_warmup(io)
        bench.bind_all()
        bench.start_relay()
        before_rx = len(bench.cco.rx_log)
        before_tx = len(bench.cco.tx_log)
        _read_energy_via_simcon(bench, io, METERS[0])
        assert len(bench.cco.rx_log) > before_rx        # 645 应答到达 CCO
        assert len(bench.cco.tx_log) > before_tx        # 13-F1 上行发出
