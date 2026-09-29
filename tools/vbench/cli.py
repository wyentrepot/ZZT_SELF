"""vbench 命令行：日常开发平替实机的场景入口。

用法：
    python3 -m tools.vbench scenarios           # 列出可用场景
    python3 -m tools.vbench bind [--meters N]   # 绑表扫掠场景
    python3 -m tools.vbench e2e   [--meters N]  # 集中器→CCO→STA→电表 抄读上报
    python3 -m tools.vbench blackbox [--meters N]

场景输出 JSON 结论（ok / 明细），退出码 0/1，便于脚本与 CI 使用。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

from .bench import VirtualBench
from .frames645 import parse_645


def _open_simcon(bench: VirtualBench):
    """打开模拟集中器侧 SerialIO（真实被测代码，连在台架本地口 slave 上）。"""
    from shared.serial_resources import SerialResourceRegistry
    from sim_concentrator.serial_io import SerialIO

    io = SerialIO(port=bench.local_pair.slave_path, baudrate=115200,
                  parity="N", resource_registry=SerialResourceRegistry(),
                  journal=None)
    io.open()
    return io


def _open_meters(bench: VirtualBench, meter_addrs: List[str]):
    """在每路底座口 slave 上打开 sim_meter 通道（真实被测代码）。"""
    from sim_meter.journal import MeterJournal
    from sim_meter.meter import MeterBrain
    from sim_meter.serial_channel import MeterSerialChannel

    channels = []
    for path, addr in zip(bench.meter_paths(), meter_addrs):
        brain = MeterBrain()
        brain.set_address(addr)
        brain.set_register("00010000", "12345.67", scale=2, length=8)
        ch = MeterSerialChannel(brain, port=path, baudrate=115200,
                                parity="N",
                                journal=MeterJournal(port=path, log_dir=None))
        ch.open()
        channels.append(ch)
    return channels


def scenario_bind(meters: int, meter_addrs: Optional[List[str]]) -> dict:
    """绑表场景：每路 STA 扫掠 99×6/AA×6 广播 13H 直到电表应答绑定。"""
    addrs = meter_addrs or [f"0133000000{i + 1:02d}" for i in range(meters)]
    with VirtualBench.create(meters=len(addrs), meter_addrs=addrs) as bench:
        channels = _open_meters(bench, addrs)
        try:
            bound = bench.bind_all()
            return {
                "ok": all(b is not None for b in bound),
                "scenario": "bind",
                "meters": [
                    {"addr": a, "bound": b is not None,
                     "bound_addr_wire": b.hex().upper() if b else None}
                    for a, b in zip(addrs, bound)
                ],
            }
        finally:
            for ch in channels:
                ch.close()


def scenario_e2e(meters: int, meter_addrs: Optional[List[str]]) -> dict:
    """端到端场景：simcon 13-F1 路由抄读 → CCO → HPLC → STA → 电表 → 上报。"""
    from sim_concentrator.runner import execute_task
    from .frames13762 import decode_frame
    from .frames645 import bcd_int, un33

    addrs = meter_addrs or [f"0133000000{i + 1:02d}" for i in range(meters)]
    with VirtualBench.create(meters=len(addrs), meter_addrs=addrs) as bench:
        io = _open_simcon(bench)
        channels = _open_meters(bench, addrs)
        try:
            warm = bench.bus_warmup(io)
            bound = bench.bind_all()
            bench.start_relay()
            readings = []
            for addr, leg in zip(addrs, bench.legs):
                task = {
                    "id": f"vbench.read.{addr}",
                    "enable_responder": True,
                    "responders": [{"id": "vbench.14f2",
                                    "match": {"afn": 0x14, "fn": 2},
                                    "reply": {"afn": 0x14, "fn": 2,
                                              "format": "local",
                                              "userdata_builder": "echo"}}],
                    "steps": [{"name": f"抄读 {addr}",
                               "send": {"afn": "13", "fn": "F1",
                                        "params": {"protocol": 2, "subs": [],
                                                   "payload": bench.read_energy_payload(addr)}},
                               "expect": {"afn": 0x13, "fn": 1},
                               "expect_timeout": 6.0}],
                }
                result = execute_task(task, io=io)
                value = None
                for fr in result.get("frames", []):
                    decoded = fr.get("decoded") or decode_frame(bytes.fromhex(fr["raw_hex"]))
                    if decoded.get("nested"):
                        n645 = decoded["nested"][0]
                        raw645 = bytes.fromhex(n645.get("raw_hex")
                                               or n645.get("items", [{}])[0].get("hex", ""))
                        if raw645:
                            _, ctrl, data = parse_645(raw645)
                            if ctrl == 0x91 and len(data) >= 12:
                                value = bcd_int(un33(data[4:12]))
                readings.append({"addr": addr, "step_ok": result.get("ok"),
                                 "energy_bcd": value})
            return {
                "ok": all(r["step_ok"] and r["energy_bcd"] == 1234567
                          for r in readings),
                "scenario": "e2e",
                "bus_ok": bench.cco.bus_ok,
                "warmup_ok": warm.get("ok"),
                "readings": readings,
            }
        finally:
            for ch in channels:
                ch.close()
            io.close()


def scenario_blackbox(meters: int, meter_addrs: Optional[List[str]]) -> dict:
    """黑盒场景：脏字节+698+合法 645 混流，电表只应答合法帧。"""
    from .fake_sta import BCAST_99
    from .frames645 import build_645

    addrs = meter_addrs or [f"0133000000{i + 1:02d}" for i in range(meters)]
    results = []
    with VirtualBench.create(meters=len(addrs), meter_addrs=addrs) as bench:
        channels = _open_meters(bench, addrs)
        try:
            for leg in bench.legs:
                junk = bytes(range(64)) + b"\x00\xFF" * 8
                frame_698 = bytes.fromhex("6817004345AAAAAAAAAAAA10DA1A16")
                leg.sta.send_raw(junk + frame_698
                                 + build_645(BCAST_99, 0x13, preamble_fe=4))
                time.sleep(0.5)
                reply = leg.pair.drain(quiet=0.3)
                results.append({"addr": None, "reply_frames": len(reply) // 12,
                                "reply_bytes": len(reply)})
            return {"ok": all(r["reply_bytes"] > 0 for r in results),
                    "scenario": "blackbox", "results": results}
        finally:
            for ch in channels:
                ch.close()


SCENARIOS = {
    "bind": scenario_bind,
    "e2e": scenario_e2e,
    "blackbox": scenario_blackbox,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.vbench",
                                     description="虚拟串口台架（实机平替）")
    parser.add_argument("scenario", choices=sorted(SCENARIOS) + ["scenarios"])
    parser.add_argument("--meters", type=int, default=1)
    parser.add_argument("--addrs", type=str, default="",
                        help="逗号分隔的电表地址（12位BCD显示序）")
    args = parser.parse_args(argv)
    if args.scenario == "scenarios":
        print(json.dumps({"scenarios": sorted(SCENARIOS)}, ensure_ascii=False))
        return 0
    addrs = [a.strip() for a in args.addrs.split(",") if a.strip()] or None
    result = SCENARIOS[args.scenario](args.meters, addrs)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
