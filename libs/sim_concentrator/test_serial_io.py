"""serial_io 单元测试：用注入假串口验证切帧入队 / 发送 / 接收。

真实 pyserial 不可用时跳过；这里通过构造假串口对象驱动 SerialIO 的
读线程与发送逻辑，验证帧切分与队列语义。
"""
import threading
import time
from types import SimpleNamespace

import pytest

from sim_concentrator.frame_codec import build_13762_frame
from sim_concentrator.serial_io import (
    SerialIO,
    available as _serial_available,
    list_serial_ports,
    resolve_serial_config,
)

_requires_serial = pytest.mark.skipif(
    not _serial_available(), reason="无 pyserial 环境")

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


def _frame(afn=0x01, fn=1):
    return build_13762_frame(afn=afn, fn=fn, info={"seq": 1})


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


def _fake_port(device: str, description: str = ""):
    return SimpleNamespace(device=device, description=description)


def _stub_catalog():
    """只含 listener 映射的最小目录（隔离仓库配置，保证断言确定性）。"""
    from shared.serial_mapping import SerialPortCatalog, SerialPortMapping
    return SerialPortCatalog(mappings=[
        SerialPortMapping(id="listener", linux_device="/dev/ttyUSB0",
                          windows_com="COM4", label="侦听台", usage="listener",
                          baudrate=115200, parity="E", enabled=True),
    ])


@_requires_serial
def test_resolve_without_port_auto_selects_available_port(monkeypatch):
    """不传端口：自动选择可用串口（默认值，不锁定），缺省参数 9600/E/8/1。"""
    import sim_concentrator.serial_io as mod

    monkeypatch.setattr(mod, "list_ports",
                        SimpleNamespace(comports=lambda: [
                            _fake_port("COM10", "USB-SERIAL CH340"),
                            _fake_port("COM4", "蓝牙链接上的标准串行"),
                            _fake_port("COM2", "ELTIMA Virtual Serial Port"),
                        ]))
    resolved = resolve_serial_config(catalog=_stub_catalog())

    # 已映射的 COM4（listener）被排除；蓝牙靠后；COM2 < COM10 自然序
    assert resolved["port"] == "COM2"
    assert resolved["mapping_id"] == ""
    assert resolved["baudrate"] == 9600
    assert resolved["parity"] == "E"
    assert resolved["bytesize"] == 8
    assert resolved["stopbits"] == 1
    assert resolved["port_identity"]["device"] == "COM2"


@_requires_serial
def test_resolve_auto_select_raises_when_only_claimed_ports(monkeypatch):
    import sim_concentrator.serial_io as mod

    monkeypatch.setattr(mod, "list_ports",
                        SimpleNamespace(comports=lambda: [
                            _fake_port("COM4", "蓝牙链接上的标准串行"),
                        ]))
    with pytest.raises(ValueError, match="未找到可用串口"):
        resolve_serial_config(catalog=_stub_catalog())


def test_resolve_serial_config_preserves_explicit_unmapped_port_override():
    resolved = resolve_serial_config(port="COM24")

    assert resolved["mapping_id"] == ""
    assert resolved["port"] == "COM24"
    assert resolved["port_identity"]["device"] == "COM24"
    assert resolved["baudrate"] == 115200
    assert resolved["parity"] == "N"


def test_serial_io_respects_shared_backend_serial_registry():
    from shared.serial_resources import SerialResourceRegistry

    registry = SerialResourceRegistry()
    registry.reserve(
        "module:ms-cco", label="模块日志会话 CCO",
        resource_id="cco-main", aliases=("COM_SHARED",),
    )
    io = SerialIO(
        port="COM_SHARED",
        port_identity={"mapping_id": "cco-main", "windows_com": "COM_SHARED"},
        resource_registry=registry,
    )

    with pytest.raises(RuntimeError, match="模块日志会话 CCO"):
        io.open()
