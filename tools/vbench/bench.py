"""台架装配：把虚拟串口对接成「模拟集中器 ↔ 伪CCO ↔ HPLC跳 ↔ 伪STA ↔ 被测电表」。

装配原则（README 架构铁律）：
- 所有数据交换只经虚拟串口字节流：本模块只负责创建串口对并把伪 CCO 与
  各伪 STA 的 HPLC 口用纯字节泵对接；
- 被测件（simcon 的 SerialIO、sim_meter 的通道）由调用方在 slave 路径上
  按真实代码打开，台架不感知其内部实现；
- 台架提供的 bus_warmup / bind_all / read_energy 等场景动作全部通过串口
  字节流完成。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from .fake_cco import FakeCCO
from .fake_sta import DI_ENERGY, FakeSTA
from .frames645 import addr_wire, build_645, di_wire
from .pair import VirtualPair, link_star, stop_link


def wait_until(pred, timeout: float = 2.0, interval: float = 0.05) -> bool:
    """轮询等待异步事件（回帧经虚拟串口/泵线程有毫秒级在途时间）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()

DEFAULT_ARCHIVE_NODES = [
    {"addr": "013300000001", "info": {"quality": 12, "relay": 0}},
    {"addr": "013300000002", "info": {"quality": 10, "relay": 0}},
    {"addr": "999999999999", "info": {"quality": 14, "relay": 0}},
]


@dataclass
class MeterLeg:
    """一路底座口：电表串口对 + 扮演该路的 STA。"""
    pair: VirtualPair
    sta: FakeSTA


@dataclass
class VirtualBench:
    """N 路底座口 + 1 个本地口的虚拟台架。

    slave 路径：
    - bench.local_pair.slave_path → 模拟集中器（simcon SerialIO）侧
    - bench.legs[i].pair.slave_path → 第 i 块被测电表侧
    """

    local_pair: VirtualPair = field(default_factory=VirtualPair)
    carrier_cco: VirtualPair = field(default_factory=VirtualPair)
    legs: List[MeterLeg] = field(default_factory=list)
    carrier_sta: List[VirtualPair] = field(default_factory=list)
    cco: Optional[FakeCCO] = None
    _links: List = field(default_factory=list)

    @classmethod
    def create(cls, *, meters: int = 1,
               meter_addrs: Optional[List[str]] = None,
               archive_nodes: Optional[List[dict]] = None) -> "VirtualBench":
        addrs = meter_addrs or [f"0133000000{i + 1:02d}" for i in range(meters)]
        bench = cls()
        bench.cco = FakeCCO(bench.local_pair, bench.carrier_cco,
                            archive_nodes=archive_nodes
                            if archive_nodes is not None
                            else DEFAULT_ARCHIVE_NODES)
        for _ in addrs:
            mp, sp = VirtualPair(), VirtualPair()
            # STA 同时持有两个口：底座口 mp（连电表）与载波口 sp（连 CCO 跳）
            bench.legs.append(MeterLeg(pair=mp, sta=FakeSTA(mp)))
            bench.carrier_sta.append(sp)
        # 载波跳＝星型共享媒介：CCO 下行广播到各 STA，上行汇聚（纯字节泵）
        bench._links.append(link_star(bench.carrier_cco, bench.carrier_sta))
        return bench

    def meter_paths(self) -> List[str]:
        return [leg.pair.slave_path for leg in self.legs]

    def start(self) -> None:
        """启动 CCO 服务（bpsCheck 节奏 + 档案 + 抄读中继）。"""
        self.cco.start()

    def stop(self) -> None:
        self.stop_relay()
        self.cco.stop()
        for stop in self._links:
            stop_link(stop)

    def bus_warmup(self, simcon_io) -> dict:
        """本地总线预热：等 CCO 的 14-F2 询问并以 local+echo 应答（实机口径）。

        simcon_io 为调用方打开好的模拟集中器 SerialIO；任务经
        sim_concentrator.runner.execute_task 执行（此处 import 属用例装配，
        数据交换仍全在串口上）。
        """
        from sim_concentrator.runner import execute_task

        task = {
            "id": "vbench.bpscheck",
            "enable_responder": True,
            "responders": [{"id": "vbench.14f2", "match": {"afn": 0x14, "fn": 2},
                            "reply": {"afn": 0x14, "fn": 2,
                                      "format": "local", "userdata_builder": "echo"}}],
            "steps": [{"name": "bpsCheck 14-F2 应答", "recv_only": True,
                       "expect": {"afn": 0x14, "fn": 2},
                       "expect_timeout": 6.0}],
        }
        return execute_task(task, io=simcon_io)

    def bind_all(self, *, wait: float = 0.8) -> List[Optional[bytes]]:
        """所有路的 STA 执行绑表扫掠，返回各路绑定到的表地址（BCD 线序）。"""
        return [leg.sta.sweep_until_bind(wait=wait) for leg in self.legs]

    def start_relay(self) -> None:
        """启动各路 STA 的载波转发（绑定之后调用）。

        转发线程把 CCO 载波口来的 645 下行写到本路底座口、把电表应答原样
        送回载波口——纯字节搬运，不解帧改帧。与绑表扫掠共用底座口主端，
        故扫掠结束后再启动，避免并发读取。
        """
        import threading

        self._relay_stop = threading.Event()
        self._relay_threads = []
        for leg, sp in zip(self.legs, self.carrier_sta):
            t = threading.Thread(target=leg.sta.serve_carrier,
                                 args=(sp, self._relay_stop), daemon=True,
                                 name=f"vbench-sta-relay-{id(leg)}")
            t.start()
            self._relay_threads.append(t)

    def stop_relay(self) -> None:
        stop = getattr(self, "_relay_stop", None)
        if stop is not None:
            stop.set()
            for t in getattr(self, "_relay_threads", ()):
                t.join(timeout=2.0)
            self._relay_threads = []

    def read_energy_payload(self, meter_addr_display: str) -> str:
        """构造抄读 DI 00010000 的 645 请求帧 hex（供 simcon 13-F1 载荷）。"""
        req = build_645(addr_wire(meter_addr_display), 0x11,
                        di_wire(DI_ENERGY))
        return req.hex().upper()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
