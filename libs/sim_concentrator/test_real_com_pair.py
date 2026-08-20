"""【临时启用】COM1/COM2 真实串口对收发验证测试。

背景（2026-08-18 排查记录）：
  用户环境有一对互通虚拟串口 COM1↔COM2（ELTIMA Virtual Serial Port）。
  现象：CCO 日志接 COM1、集中器接 COM2，执行集中器用例后 COM1 侧
  “收不到任何数据”。本测试用真实串口对验证：
    1. COM1↔COM2 双向透传（pyserial 直连）；
    2. SerialIO(COM2) 发帧 → COM1 对端按帧切分完整收到；
    3. 执行一个本地协议 step 的 send 帧，确认完整到达对端（验证发送链路，
       不要求 CCO 回帧，故 expect 侧超时 fail 属预期，不视为本测试失败）；
    4. module_log cco 通道监听 COM1 + SerialIO 发帧 → 验证“module_log
       按行切分导致二进制帧滞留、不落盘”这一根因现象。

启用方式（临时，显式指定串口对才跑；未设置时整文件 skip）：
    set SIMCON_TEST_COM1=COM1
    set SIMCON_TEST_COM2=COM2
    python -m pytest libs/sim_concentrator/test_real_com_pair.py -v

注意：该测试会真实打开串口并收发字节，仅应在具备互通串口对的
环境中临时执行；CI / 无串口环境默认自动 skip，不影响全量回归。
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from sim_concentrator.frame_codec import build_local_13762_frame
from sim_concentrator.serial_io import SerialIO

COM1 = os.environ.get("SIMCON_TEST_COM1", "")
COM2 = os.environ.get("SIMCON_TEST_COM2", "")
BAUD = int(os.environ.get("SIMCON_TEST_BAUD", "115200"))

pytestmark = pytest.mark.skipif(
    not (COM1 and COM2),
    reason="未设置 SIMCON_TEST_COM1/SIMCON_TEST_COM2，跳过真实串口对测试（临时启用）",
)


def _open_pair():
    """打开串口对两端；任一失败给出可读原因并跳过。"""
    try:
        import serial
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"缺少 pyserial: {exc}")
    try:
        a = serial.Serial(COM1, BAUD, timeout=0.3)
        b = serial.Serial(COM2, BAUD, timeout=0.3)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"无法打开串口对 {COM1}/{COM2}: {exc}")
    return a, b


def _drain(ser, duration: float):
    """后台线程读走 ser 的所有字节，返回 bytes。"""
    out = []
    stop = threading.Event()

    def _loop():
        while not stop.is_set():
            d = ser.read(512)
            if d:
                out.append(bytes(d))
            else:
                time.sleep(0.01)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    try:
        time.sleep(duration)
    finally:
        stop.set()
        t.join(timeout=1.0)
    return b"".join(out)


# ---------------------------------------------------------------------------
# 1) COM1↔COM2 双向透传
# ---------------------------------------------------------------------------
def test_com_pair_bidirectional_passthrough():
    a, b = _open_pair()
    got_a, got_b = [], []
    stop = threading.Event()

    def _drain(ser, out):
        while not stop.is_set():
            d = ser.read(512)
            if d:
                out.append(d)
            else:
                time.sleep(0.005)

    ta = threading.Thread(target=_drain, args=(a, got_a), daemon=True)
    tb = threading.Thread(target=_drain, args=(b, got_b), daemon=True)
    ta.start()
    tb.start()
    time.sleep(0.2)  # 让两个 drain 线程先进入 read，消除起始时序
    try:
        b.write(b"hello-from-com2")
        a.write(b"hello-from-com1")
        time.sleep(0.5)
        assert b"hello-from-com2" in got_a, f"COM1 未收到 COM2 发送的数据: {got_a!r}"
        assert b"hello-from-com1" in got_b, f"COM2 未收到 COM1 发送的数据: {got_b!r}"
    finally:
        stop.set()
        ta.join(timeout=1.0)
        tb.join(timeout=1.0)
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# 2) SerialIO(COM2) 发帧 → COM1 对端完整收到
# ---------------------------------------------------------------------------
def test_serialio_send_frame_arrives_at_pair_peer():
    import serial

    a = serial.Serial(COM1, BAUD, timeout=0.3)
    io = SerialIO(port=COM2, baudrate=BAUD)
    try:
        io.open()
        frame = build_local_13762_frame(afn=0x10, fn=4, buff=b"", ctrl=0x43, seq=1)
        io.send_frame(frame)
        got = _drain(a, 0.6)
        assert got == frame, f"COM1 收到的帧与发送帧不一致:\n  sent={frame.hex()}\n  got ={got.hex()}"
    finally:
        io.close()
        a.close()


# ---------------------------------------------------------------------------
# 3) 本地协议 step 的 send 帧完整到达对端（不要求 CCO 回帧）
# ---------------------------------------------------------------------------
def test_local_step_send_frame_arrives():
    import serial

    a = serial.Serial(COM1, BAUD, timeout=0.3)
    io = SerialIO(port=COM2, baudrate=BAUD)
    try:
        io.open()
        # 与 anhui_minute_collect.json 第一步同型：10H-F4 查询路由运行状态
        frame = build_local_13762_frame(afn=16, fn=4, buff=b"", ctrl=0x43, seq=1)
        io.send_frame(frame)
        got = _drain(a, 0.6)
        assert got == frame, f"步骤下发帧未完整到达对端:\n  sent={frame.hex()}\n  got ={got.hex()}"
    finally:
        io.close()
        a.close()


# ---------------------------------------------------------------------------
# 4) module_log cco 通道监听 COM1：二进制帧滞留不落盘（根因复现）
# ---------------------------------------------------------------------------
def test_module_log_cco_buffers_binary_frame_without_logging():
    import sys
    from pathlib import Path
    import tempfile

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "apps"))
    from module_log.module_serial_service import ModuleSerialService

    tmp = tempfile.mkdtemp()
    svc = ModuleSerialService(log_dir=tmp)
    io = SerialIO(port=COM2, baudrate=BAUD)
    try:
        svc.start(COM1, baudrate=BAUD, bytesize=8, parity="N", stopbits=1, channel="cco")
        io.open()
        frame = build_local_13762_frame(afn=16, fn=4, buff=b"", ctrl=0x43, seq=1)
        io.send_frame(frame)
        time.sleep(0.8)

        st = svc.status()["channels"]["cco"]
        rx_lines = [ln for ln in svc.logs(after=-1, channel="cco")["lines"]
                    if ln["dir"] == "RX"]
        # 断言 1：RX 方向没有任何日志行（二进制帧按行切分，遇不到 \n 不落盘）
        assert not rx_lines, f"预期无 RX 日志行，实际: {rx_lines}"
        # 断言 2：帧字节滞留在线缓冲（未落盘、未进入内存日志）
        ch = svc.channel("cco")
        buf = bytes(ch._rx_line_buf)
        assert frame in buf, f"帧应滞留 _rx_line_buf，实际缓冲={buf.hex()}"
        assert st["lines"] == 1, "应只有「串口打开」EVENT 行"
    finally:
        io.close()
        svc.stop(channel="cco")
