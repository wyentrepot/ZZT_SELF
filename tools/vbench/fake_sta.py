"""模拟 STA：绑表扫掠 / 抄表 / 改速率节奏（底座口 645 从站交互侧）。

只持有自己的虚拟串口端点（VirtualPair 主端），全部收发走字节流，
不 import 任何被测件。节奏对照实机（docs/19 §3.2/3.3、H_STA
aps_mclt_state.c 的 remap 轮询）：

- 绑表扫掠：99×6 与 AA×6 广播 13H 逐轮交替（FE×4 唤醒前导）；
- 命中 93H 后按读到的表地址单读 13H 校验，再读 DI 00010000
  （正向有功总电能）判定绑定成功；
- 绑定后按需 17H 改速率（实机绑定后记忆波特率 REMAP_BPS_DEF）。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional, Tuple

from .frames645 import (bcd_int, build_645, di_wire, extract_645, parse_645,
                        un33)

BCAST_99 = b"\x99" * 6
BCAST_AA = b"\xAA" * 6
BCAST_FE = b"\xFE" * 6

RATE_2400 = 0x04                  # 645 速率特征字（bit 单 1 有效）
DI_ENERGY = "00010000"            # 正向有功总电能（显示序 DI3..DI0）


class FakeSTA:
    """在 VirtualPair 主端扮演 STA（底座口 485 主站侧）。"""

    def __init__(self, pair, *, quiet: float = 0.15):
        self.pair = pair
        self.quiet = quiet
        self._buf = b""
        self.tx_log: List[bytes] = []
        self.rx_log: List[bytes] = []
        self.bound_addr: Optional[bytes] = None

    # -- 收发 -------------------------------------------------------------
    def _read_frames(self, timeout: float) -> List[bytes]:
        out: List[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.pair.read_once(timeout=max(0.02, self.quiet))
            if chunk:
                self._buf += chunk
            while True:
                frame, consumed = extract_645(self._buf)
                if frame is None:
                    if consumed:
                        self._buf = self._buf[consumed:]
                    break
                self._buf = self._buf[consumed:]
                out.append(frame)
                self.rx_log.append(frame)
            if not chunk and out:
                break
        return out

    def _send(self, frame: bytes) -> None:
        self.tx_log.append(frame)
        self.pair.write(frame)

    def send_raw(self, data: bytes) -> None:
        """直接注入原始字节（透传/脏数据用例）。"""
        self.tx_log.append(data)
        self.pair.write(data)

    # -- 绑表节奏 ---------------------------------------------------------
    def sweep_until_bind(self, *, max_turns: int = 12,
                         wait: float = 0.6) -> Optional[bytes]:
        """逐轮交替 99×6 / AA×6 广播 13H；93H 命中后单表校验，成功记绑定。"""
        for turn in range(max_turns):
            bcast = BCAST_99 if turn % 2 == 0 else BCAST_AA
            self._send(build_645(bcast, 0x13, preamble_fe=4))
            for raw in self._read_frames(wait):
                addr, ctrl, data = parse_645(raw)
                if ctrl != 0x93 or len(data) < 6:
                    continue
                # 93H 数据域为表地址原文（不加 33H）
                meter_addr = data[:6]
                self._send(build_645(meter_addr, 0x13))       # 单表校验读
                for raw2 in self._read_frames(wait):
                    a2, c2, d2 = parse_645(raw2)
                    if c2 == 0x93 and d2[:6] == meter_addr:
                        self.bound_addr = meter_addr
                        return meter_addr
        return None

    # -- 抄表 -------------------------------------------------------------
    def read_di(self, meter_addr: bytes, di_display: str,
                wait: float = 0.8) -> Optional[bytes]:
        """读数据标识，返回应答数据域原文（DI+值，已 -33 还原），失败 None。"""
        self._send(build_645(meter_addr, 0x11, di_wire(di_display)))
        for raw in self._read_frames(wait):
            _, ctrl, data = parse_645(raw)
            if ctrl == 0x91:
                return un33(data)
        return None

    def read_energy(self, meter_addr: bytes, wait: float = 0.8) -> Optional[int]:
        """读 DI 00010000 的 BCD 原值（0.01 kWh 口径），失败 None。"""
        data = self.read_di(meter_addr, DI_ENERGY, wait=wait)
        if data is None or len(data) < 12:
            return None
        return bcd_int(data[4:12])

    # -- 改速率 -----------------------------------------------------------
    def set_baud(self, meter_addr: bytes, rate_word: int = RATE_2400,
                 wait: float = 0.8) -> bool:
        """17H 更改通信速率（数据域单字节特征字，+33H 传输）。"""
        self._send(build_645(meter_addr, 0x17, bytes([rate_word])))
        for raw in self._read_frames(wait):
            _, ctrl, _ = parse_645(raw)
            if ctrl == 0x97:
                return True
        return False

    # -- 服务循环（E2E 用：把 HPLC 口来的 645 转到底座口并回程） ----------
    def serve_carrier(self, carrier, stop: threading.Event,
                      *, on_tx: Optional[Callable[[bytes], None]] = None,
                      poll: float = 0.02) -> None:
        """转发线程：carrier 端口来的 645 字节写到底座口，底座口应答回 carrier。

        carrier 为另一个 VirtualPair（CCO 侧的 HPLC 口）。双向搬运的只是
        字节流——本方法不理解、不改写任何帧内容。
        """
        cbuf = b""
        while not stop.is_set():
            chunk = carrier.read_once(timeout=poll)
            if chunk:
                cbuf += chunk
                while True:
                    frame, consumed = extract_645(cbuf)
                    if frame is None:
                        cbuf = cbuf[consumed:]
                        break
                    cbuf = cbuf[consumed:]
                    self._send(frame)                  # 底座口下行
                    self._await_reply(carrier, stop, on_tx=on_tx)

    def _await_reply(self, carrier, stop: threading.Event,
                     *, on_tx=None, wait: float = 2.0) -> None:
        """等底座口应答并原样回送 carrier（超时静默——主站侧自行重试）。"""
        deadline = time.monotonic() + wait
        acc = b""
        while time.monotonic() < deadline and not stop.is_set():
            chunk = self.pair.read_once(timeout=0.05)
            if not chunk:
                continue
            acc += chunk
            while True:
                frame, consumed = extract_645(acc)
                if frame is None:
                    acc = acc[consumed:]
                    break
                acc = acc[consumed:]
                self.rx_log.append(frame)
                if on_tx is not None:
                    on_tx(frame)
                carrier.write(frame)
                return
