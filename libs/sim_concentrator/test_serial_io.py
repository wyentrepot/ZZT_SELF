"""serial_io 单元测试：用注入假串口验证切帧入队 / 发送 / 接收。

真实 pyserial 不可用时跳过；这里通过构造假串口对象驱动 SerialIO 的
读线程与发送逻辑，验证帧切分与队列语义。
"""
import threading
import time
from types import SimpleNamespace

import pytest

from sim_concentrator.batch import meter_read_645
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


# -- REQS-0039 LR-4/LR-5 回归：单调帧序号游标 + 有界接收队列 ------------------

def test_rx_cursor_survives_history_truncation():
    """len() 游标在历史截断到 1000 条后恒等、hist[seen:] 恒为空（req0007 实测
    高频长跑全部超时的根因）；seq 游标必须与截断解耦。"""
    io = SerialIO(port="COM_TEST")
    for _ in range(1200):
        io._record_rx(_frame())
    assert io.rx_cursor() == 1200
    assert len(io.rx_history()) == 1000
    # 旧口径的陷阱（文档化断言）：len 游标之后永远切不出新帧
    seen = len(io.rx_history())
    assert io.rx_history()[seen:] == []
    # 新口径：基线之前的帧被截掉，只是从现存最早帧起返回
    frames, cur = io.rx_history_since(0)
    assert len(frames) == 1000 and cur == 1200
    # 正常增量：只要末尾新帧
    frames, cur = io.rx_history_since(1195)
    assert len(frames) == 5 and cur == 1200
    # 游标超前（无新帧）：返回空且游标不回退
    frames, cur = io.rx_history_since(5000)
    assert frames == [] and cur == 5000


def test_rx_queue_bounded_drops_oldest():
    """队列有界：满时丢最旧帧并计数，drain 排空（防 48h 内存单调增长）。"""
    io = SerialIO(port="COM_TEST")
    for i in range(3000):
        io._enqueue_rx(bytes([i & 0xFF]) * 8)
    assert io.pending_frames() == 2048
    assert io.dropped_frames() == 952
    assert io.drain_rx() == 2048
    assert io.pending_frames() == 0


def _saturated_seq_io():
    """seq 游标口径假 io：历史只留最近 1000 条（复刻 SerialIO 截断语义），
    send 后灌 1199 条噪声再给真应答——应答永远落在截断线之后。"""
    from sim_concentrator.batch import _decoded_seq
    from sim_concentrator.frame_codec import decode_frame

    class _SaturatedIO:
        def __init__(self):
            self._entries = []
            self.sent = []
            for _ in range(1000):       # 预饱和：len() 游标从此失效
                self._append(build_13762_frame(afn=0x06, fn=1, direction="up",
                                               info={"seq": 0}))

        def _append(self, f):
            nxt = self._entries[-1][0] + 1 if self._entries else 1
            self._entries.append((nxt, f))
            if len(self._entries) > 1000:
                del self._entries[: len(self._entries) - 1000]

        def send_frame(self, raw):
            self.sent.append(raw)
            app = meter_read_645("000000000010")
            proto = bytes([2, 0]) + len(app).to_bytes(2, "little") + app
            reply = build_13762_frame(
                afn=0xF1, fn=1, appdata=proto, direction="up",
                info={"seq": _decoded_seq(decode_frame(raw))})
            for _ in range(1199):
                self._append(build_13762_frame(afn=0x06, fn=1, direction="up",
                                               info={"seq": 0}))
            self._append(reply)

        def rx_cursor(self):
            return self._entries[-1][0] if self._entries else 0

        def rx_history_since(self, last_seq):
            entries = list(self._entries)
            if not entries:
                return [], max(int(last_seq), 0)
            start = max(0, int(last_seq) - entries[0][0] + 1)
            return ([f for _, f in entries[start:]],
                    max(int(last_seq), entries[-1][0]))

        def rx_history(self):
            return [f for _, f in self._entries]

    return _SaturatedIO()


def test_batch_read_matches_after_history_saturation():
    """REQS-0039 LR-4 回归：历史饱和（>1000 条）后 batch 抄表仍能匹配应答。"""
    from sim_concentrator.batch import BatchReadJob

    job = BatchReadJob(_saturated_seq_io(), ["000000000010"],
                       mode="batch", timeout=2)
    job._read_one("000000000010")
    rows = job.snapshot()["rows"]
    assert rows and rows[0]["status"] == "success"


def test_batch_read_fallback_without_seq_cursor():
    """无 seq 游标接口的 io（旧测试桩）：回退 len() 基线仍可用。"""
    from sim_concentrator.batch import BatchReadJob

    class _PlainIO:
        def __init__(self):
            self.hist = []

        def send_frame(self, raw):
            app = meter_read_645("000000000010")
            proto = bytes([2, 0]) + len(app).to_bytes(2, "little") + app
            self.hist.append(build_13762_frame(
                afn=0xF1, fn=1, appdata=proto, direction="up",
                info={"seq": 7}))

        def rx_history(self):
            return list(self.hist)

    job = BatchReadJob(_PlainIO(), ["000000000010"], mode="batch", timeout=2)
    job._read_one("000000000010")
    rows = job.snapshot()["rows"]
    assert rows and rows[0]["status"] == "success"


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
    # 用不存在的端口名保证"未映射"语义（COM24 现已被 config 映射为 listener）
    resolved = resolve_serial_config(port="COM_FAKE")

    assert resolved["mapping_id"] == ""
    assert resolved["port"] == "COM_FAKE"
    assert resolved["port_identity"]["device"] == "COM_FAKE"
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


def test_serial_io_passes_write_timeout_to_driver(monkeypatch):
    """DEF-1 根因回归：open 必须给 pyserial 传 write_timeout。

    部分虚拟/USB 串口驱动在写阻塞时会让 write() 无限期挂死（实测 COM4
    首帧即挂死），不设超时会导致 verify 线程永久阻塞。
    """
    captured = {}

    class _FakeSerial:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.is_open = True

        def read(self, n):
            return b""

        def write(self, data):
            return len(data)

        def close(self):
            self.is_open = False

    class _FakeSerialModule:
        Serial = _FakeSerial
        PARITY_NONE = "N"
        PARITY_EVEN = "E"
        FIVEBITS = 5
        SIXBITS = 6
        SEVENBITS = 7
        EIGHTBITS = 8
        STOPBITS_ONE = 1
        STOPBITS_TWO = 2

    monkeypatch.setattr("sim_concentrator.serial_io.serial", _FakeSerialModule)
    io = SerialIO(port="COM_TEST", baudrate=9600, parity="E")
    io.open()
    try:
        assert io.write_timeout == 5.0
        assert captured.get("write_timeout") == 5.0
    finally:
        io.close()
