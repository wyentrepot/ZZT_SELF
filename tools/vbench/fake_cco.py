"""模拟 CCO：本地总线节奏（bpsCheck/档案/路由抄读中继）。

只持有两个虚拟串口端点，全部收发走字节流：
- local 端（连模拟集中器）：CCO 本地协议节奏——周期 14H-F2 时钟询问直到
  收到上行 14-F2（bpsCheck 判定，实机口径 com_port.c local_rx_ok）；
  应答 10H-F2 档案查询、11H-F1 档案操作确认；
- carrier 端（连 STA 的 HPLC 跳）：13H-F1 下行载荷（645 帧）原样发出，
  收到 645 应答即以 13H-F1 上行回送模拟集中器。

帧编解码复用 sim_concentrator.frame_codec（与被测库同源，字节口径一致，
仅 import 依赖，无共享内存/直接调用）。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from .frames13762 import (app_data, build_13762_frame, build_13f1_up,
                          build_local_13762_frame, decode_frame,
                          node_list_reply, parse_13f1_down, scan_frame)
from .frames645 import extract_645

BPS_CHECK_INTERVAL = 1.0          # bpsCheck 14-F2 周期（秒）


class FakeCCO:
    """在 VirtualPair 主端扮演 CCO（本地侧 + HPLC 载波侧）。"""

    def __init__(self, local, carrier, *, archive_nodes: Optional[List[dict]] = None,
                 bps_interval: float = BPS_CHECK_INTERVAL):
        self.local = local            # 连模拟集中器的本地口
        self.carrier = carrier        # 连 STA 的 HPLC 跳口
        self.archive_nodes = archive_nodes or []
        self.bps_interval = bps_interval
        self.bus_ok = False           # 收到上行 14-F2 即 True（local_rx_ok 口径）
        self.tx_log: List[bytes] = []
        self.rx_log: List[bytes] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending: List[dict] = []   # 待回的 13-F1 下行 {protocol}
        self._cbuf = b""
        self.errors: List[str] = []      # 服务循环异常（不静默死亡）

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._serve, daemon=True,
                                            name="vbench-fakecco")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    # -- 服务循环 ---------------------------------------------------------
    def _serve(self) -> None:
        lbuf = b""
        last_bps = 0.0
        while not self._stop.is_set():
            # HPLC 载波侧：645 应答到达 → 回 13-F1 上行
            chunk = self.carrier.read_once(timeout=0.05)
            if chunk:
                self._cbuf += chunk
                while True:
                    frame, consumed = extract_645(self._cbuf)
                    if frame is None:
                        self._cbuf = self._cbuf[consumed:]
                        break
                    self._cbuf = self._cbuf[consumed:]
                    self.rx_log.append(frame)
                    self._on_meter_reply(frame)
            # 本地侧：模拟集中器来的 1376.2 帧
            chunk = self.local.read_once(timeout=0.02)
            if chunk:
                lbuf += chunk
                while True:
                    frame, consumed = scan_frame(lbuf)
                    if frame is None:
                        lbuf = lbuf[consumed:]
                        break
                    lbuf = lbuf[consumed:]
                    self.rx_log.append(frame)
                    try:
                        self._on_local_frame(frame)
                    except Exception as exc:  # noqa: BLE001 - 记录并继续服务
                        self.errors.append(f"{type(exc).__name__}: {exc}")
            # bpsCheck 节奏：总线未建立前周期询问
            if not self.bus_ok and time.monotonic() - last_bps >= self.bps_interval:
                last_bps = time.monotonic()
                self._send(build_local_13762_frame(afn=0x14, fn=2, buff=b"\x02\x00"))

    def _send(self, frame: bytes) -> None:
        self.tx_log.append(frame)
        try:
            self.local.write(frame)
        except OSError:
            pass

    # -- 本地帧分发 -------------------------------------------------------
    def _on_local_frame(self, frame: bytes) -> None:
        try:
            decoded = decode_frame(frame)
            afn, fn = (int(decoded["fields"]["AFN"]["raw"]),
                       int(decoded["fields"]["FN"]["raw"]))
        except Exception:
            return
        if afn == 0x14 and fn == 2:
            # 上行 14-F2：bpsCheck 通过（实机判据 local_rx_ok=1）
            self.bus_ok = True
            return
        if afn == 0x10 and fn == 2:
            # 档案查询：上行 10-F2（total + nodes）
            self._send(build_13762_frame(
                0x10, 2, appdata=node_list_reply(self.archive_nodes),
                direction="up"))
            return
        if afn == 0x11 and fn == 1:
            # 档案操作：上行确认（空数据）
            self._send(build_13762_frame(0x11, 1, appdata=b"",
                                         direction="up"))
            return
        if afn == 0x13 and fn == 1:
            # 路由数据转发下行：载荷（645）发往 HPLC 口，等 STA/电表应答
            info = parse_13f1_down(app_data(decoded))
            self._pending.append(info)
            try:
                self.carrier.write(bytes.fromhex(info["payload"])
                                   if isinstance(info["payload"], str)
                                   else info["payload"])
            except OSError:
                pass
            return
        # 其余帧：上行空确认（保持链路活跃，不误伤用例断言）
        self._send(build_13762_frame(afn, fn, appdata=b"", direction="up"))

    # -- 载波侧应答 -------------------------------------------------------
    def _on_meter_reply(self, frame: bytes) -> None:
        info = self._pending.pop(0) if self._pending else {"protocol": 2}
        self._send(build_13f1_up(info.get("protocol", 2), frame))
