"""虚拟串口对：os.openpty 伪终端 + 主端裸读写 + 双端字节泵。

从端路径交给被测件按真实代码打开（pyserial / serial_for_url 均可），
主端由模拟角色裸 os.read/os.write 收发字节流——两侧之间没有任何
Python 层数据通道，所有交换都走字节流。

环境注记：WSL 内核拒绝在 pts 上设置 PARENB（parity="E" 打开即 EINVAL，
2026-09-29 实测 6.18.33.2-microsoft-standard-WSL2）。本模块在创建时把
从端 tty 设为 raw（不含 PARENB），使用方开口一律 parity="N"；pts 是
虚拟管道，线路奇偶不影响字节内容，与实机 8E1 口径无关。
"""
from __future__ import annotations

import os
import pty
import select
import threading
import time
import tty
from typing import Optional


class VirtualPair:
    """一对伪终端：master 端给模拟角色，slave 端路径给被测件。"""

    def __init__(self, *, raw: bool = True):
        self.master_fd, self._slave_fd = pty.openpty()
        self.slave_path = os.ttyname(self._slave_fd)
        if raw:
            # 关闭回显/行处理：主端写入不会被回读污染，字节透明透传。
            # setraw 不引入 PARENB，WSL 内核可接受。
            tty.setraw(self._slave_fd)
        # slave_fd 保留打开以维持 pts 存在（关闭最后一个从端 fd 即销毁设备）。
        self._closed = False

    @property
    def slave_fd(self) -> int:
        """从端 fd（本对 pts 的另一头，供桥接泵使用）。"""
        return self._slave_fd

    # -- 主端读写 ---------------------------------------------------------
    def write(self, data: bytes) -> int:
        return os.write(self.master_fd, data)

    def read_once(self, timeout: float = 0.2) -> bytes:
        """读一段已到达字节（无数据等 timeout 秒后返回空）。"""
        r, _, _ = select.select([self.master_fd], [], [], timeout)
        if not r:
            return b""
        try:
            return os.read(self.master_fd, 4096)
        except OSError:
            return b""

    def drain(self, *, quiet: float = 0.2, max_wait: float = 3.0) -> bytes:
        """累积读取直至静默 quiet 秒或超时（等一帧完整到达的简便方法）。"""
        out, last = b"", time.monotonic()
        deadline = last + max_wait
        while time.monotonic() < deadline:
            chunk = self.read_once(timeout=quiet)
            if chunk:
                out += chunk
                last = time.monotonic()
            elif out and time.monotonic() - last >= quiet:
                break
        return out

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in (self.master_fd, self._slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def link_star(hub: VirtualPair, spokes: List["VirtualPair"], *,
              poll: float = 0.02) -> threading.Event:
    """星型字节泵：hub（中心角色）↔ N 个 spoke（外围角色）。

    语义等同共享无线媒介（如 HPLC）：
    - 下行广播：hub 角色发出的字节，所有 spoke 角色都收到；
    - 上行汇聚：任一 spoke 角色发出的字节，hub 角色收到。
    各角色仍只读写自己的虚拟串口，中间是纯字节搬运。多 spoke 同时上行
    会碰撞（与真实共享媒介一致），台架场景应保证同一时刻至多一路应答。
    返回 stop Event（附 moved 计数供诊断）。
    """
    stop = threading.Event()
    stop.moved = {"down": 0, "up": 0}  # type: ignore[attr-defined]

    def _write_all(fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                n = os.write(fd, view)
            except OSError:
                return
            view = view[n:]

    def _downlink() -> None:
        while not stop.is_set():
            r, _, _ = select.select([hub.slave_fd], [], [], poll)
            if not r:
                continue
            try:
                data = os.read(hub.slave_fd, 4096)
            except OSError:
                return
            if data:
                stop.moved["down"] += len(data)  # type: ignore[attr-defined]
                for sp in spokes:
                    _write_all(sp.slave_fd, data)

    def _uplink(sp: "VirtualPair") -> None:
        while not stop.is_set():
            r, _, _ = select.select([sp.slave_fd], [], [], poll)
            if not r:
                continue
            try:
                data = os.read(sp.slave_fd, 4096)
            except OSError:
                return
            if data:
                stop.moved["up"] += len(data)  # type: ignore[attr-defined]
                _write_all(hub.slave_fd, data)

    threads = [threading.Thread(target=_downlink, daemon=True,
                                name="vbench-star-down")]
    threads += [threading.Thread(target=_uplink, args=(sp,), daemon=True,
                                 name=f"vbench-star-up-{i}")
                for i, sp in enumerate(spokes)]
    for t in threads:
        t.start()
    stop._threads = tuple(threads)  # type: ignore[attr-defined]
    return stop


def link_pairs(a: VirtualPair, b: VirtualPair, *,
               poll: float = 0.02) -> threading.Event:
    """把两对虚拟串口双向对接（纯字节泵线程，1:1 点对点）。

    pty 方向性：写主端 → 从端可读；写从端 → 主端可读。角色都在「主端」
    收发自己的口，故桥接走两对的**从端** fd：
        角色A 写 a.master → a.slave 可读 → 泵 → 写 b.slave → 角色B 读 b.master。
    用途：模拟角色相互之间的链路（如 CCO↔STA 的 HPLC 跳）也以虚拟串口
    为唯一交换通道——各自只看到自己那对串口，中间是本泵搬运的字节流。
    返回 stop Event；set() 即停止泵线程。
    """
    stop = threading.Event()
    stop.moved = {"a2b": 0, "b2a": 0}  # type: ignore[attr-defined]  诊断计数

    def _pump(direction: str, src: int, dst: int) -> None:
        while not stop.is_set():
            r, _, _ = select.select([src], [], [], poll)
            if not r:
                continue
            try:
                data = os.read(src, 4096)
            except OSError:
                return
            if not data:
                return
            stop.moved[direction] += len(data)  # type: ignore[attr-defined]
            view = memoryview(data)
            while view and not stop.is_set():
                try:
                    n = os.write(dst, view)
                except OSError:
                    return
                view = view[n:]

    t1 = threading.Thread(target=_pump, args=("a2b", a.slave_fd, b.slave_fd),
                          daemon=True, name="vbench-pump-a2b")
    t2 = threading.Thread(target=_pump, args=("b2a", b.slave_fd, a.slave_fd),
                          daemon=True, name="vbench-pump-b2a")
    t1.start()
    t2.start()
    stop._threads = (t1, t2)  # type: ignore[attr-defined]
    return stop


def stop_link(stop: threading.Event) -> None:
    stop.set()
    for t in getattr(stop, "_threads", ()):  # type: ignore[attr-defined]
        t.join(timeout=2.0)
