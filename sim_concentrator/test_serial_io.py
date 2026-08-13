"""serial_io 单元测试：用注入假串口验证切帧入队 / 发送 / 接收。

真实 pyserial 不可用时跳过；这里通过构造假串口对象驱动 SerialIO 的
读线程与发送逻辑，验证帧切分与队列语义。
"""
import threading
import time

import pytest

from sim_concentrator.frame_codec import build_13762_frame
from sim_concentrator.serial_io import SerialIO, list_serial_ports

RTSA = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])


class FakeSerial:
    """模拟 pyserial：read 从 buffer 取数据，write 记录发出的字节。"""
    EIGHTBITS = 8
    PARITY_EVEN = "E"
    STOPBITS_ONE = 1

    def __init__(self, buffer=None):
        self.buffer = buffer if buffer is not None else b""
        self.written = b""
        self.is_open = True
        self.port = "COM_TEST"

    def read(self, n):
        if not self.buffer:
            return b""
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def write(self, data):
        self.written += bytes(data)
        return len(data)

    def close(self):
        self.is_open = False


def _frame(afn=0x01, seq=0x01):
    return build_13762_frame(afn=afn, seq=seq, rtsa=RTSA, msaa=0x01,
                             pw=0x0000, userdata=b"\x00")


def test_serial_io_read_thread_splits_frames():
    fake = FakeSerial()
    io = SerialIO(port="COM_TEST")
    io._ser = fake
    io._open = True
    io._read_stop.clear()
    io._read_thread = threading.Thread(target=io._run, daemon=True)
    io._read_thread.start()
    # 喂两帧 + 脏字节 + 半包
    f1, f2 = _frame(0x01), _frame(0x02)
    fake.buffer = b"\xaa\xbb" + f1 + f2 + f1[:5]
    time.sleep(0.3)
    frames = []
    while io.pending_frames():
        frames.append(io.recv_frame(timeout=0.1))
    io.close()
    assert frames == [f1, f2]


def test_serial_io_send_frame():
    fake = FakeSerial()
    io = SerialIO(port="COM_TEST")
    io._ser = fake
    io._open = True
    try:
        n = io.send_frame(b"\x68\x00")
        assert n == 2
        assert fake.written == b"\x68\x00"
    finally:
        io._open = False


def test_serial_io_send_requires_open():
    io = SerialIO(port="COM_TEST")
    io._open = False
    io._ser = None
    with pytest.raises(RuntimeError):
        io.send_frame(b"\x68")


def test_serial_io_recv_timeout():
    io = SerialIO(port="COM_TEST")
    assert io.recv_frame(timeout=0.1) is None


def test_list_ports_returns_list():
    ports = list_serial_ports()
    assert isinstance(ports, list)
