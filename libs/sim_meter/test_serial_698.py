"""串口通道 698.45 切帧集成测试（loop:// 虚拟回环）。

验证 serial_channel._try_extract 双协议切帧：
- 698 即装即采请求（含 FE 前导、68 起始、pos3=控制域非 68）能切入并应答；
- 698 应答帧（DIR=1）回环后不自激；
- 既有 645 13H 请求行为不变。
"""
import time

import pytest

from parser_lib.adapters.adapter_645 import build_frame_escaped
from sim_meter.journal import MeterJournal
from sim_meter.meter import MeterBrain
from sim_meter.protocol_698 import (
    build_get_request_43000200,
    parse_698_frame,
)
from sim_meter.serial_channel import MeterSerialChannel

pytest.importorskip("serial", reason="需要 pyserial")

ADDR = bytes.fromhex("000000000001")


@pytest.fixture
def channel():
    brain = MeterBrain()                       # 默认地址 000000000001
    journal = MeterJournal(port="loop://", log_dir=None)
    ch = MeterSerialChannel(brain, port="loop://", baudrate=2400,
                            journal=journal)
    ch.open()
    time.sleep(0.05)
    yield ch
    ch.close()


def _recv_until(channel, pred, timeout=3.0):
    """轮询 rx 队列，返回第一个满足 pred 的帧；超时返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = channel.recv_frame(timeout=0.2)
        if frame is not None and pred(frame):
            return frame
    return None


def _strip_fe(frame: bytes) -> bytes:
    i = 0
    while i < len(frame) and frame[i] == 0xFE:
        i += 1
    return frame[i:]


class TestSerial698:
    def test_698_jzjc_request_gets_reply_no_storm(self, channel):
        req = build_get_request_43000200(ADDR, piid=1, security=True)
        channel.send_frame(req)
        reply = _recv_until(
            channel,
            lambda f: _strip_fe(f)[0] == 0x68
            and _strip_fe(f)[3] == 0xC3)         # 698 服务器响应控制域
        assert reply is not None, "未收到 698 应答帧"
        parsed = parse_698_frame(reply)
        assert parsed.control == 0xC3
        assert parsed.security == 0x90             # SECURITY-Response 明文
        assert parsed.service == 0x85              # GET-Response
        # GET-Response 内层：85 01 PIID 43 00 02 00 01 0A N 描述符
        assert parsed.inner_apdu[3:7] == bytes([0x43, 0x00, 0x02, 0x00])
        # 不自激：应答回环后读线程不应再产生新的 tx 应答
        time.sleep(0.3)                            # 等回环应答帧被读线程收完
        entries = channel.journal.entries()
        tx_reply = [e for e in entries if e["dir"] == "tx"
                    and e["note"].get("reply_hex")]
        assert len(tx_reply) == 1, (
            f"出现自激/重复应答：应答 tx 数={len(tx_reply)}")

    def test_645_13h_still_replies(self, channel):
        req = build_frame_escaped(ADDR, 0x13, b"")  # 广播读通信地址
        channel.send_frame(req)
        reply = _recv_until(
            channel,
            lambda f: len(f) > 10 and _strip_fe(f)[8] & 0x80)
        assert reply is not None, "未收到 645 应答帧"
        reply = _strip_fe(reply)
        assert reply[8] == 0x93
        assert reply[10:16] == ADDR

    def test_try_extract_prefers_698(self):
        """_try_extract 单元级：698 帧正确切出，FE 计入 consumed。"""
        frame = build_get_request_43000200(ADDR, piid=1, security=True)
        res = MeterSerialChannel._try_extract(frame)
        assert res is not None
        raw, consumed = res
        assert consumed == len(frame)
        assert raw == _strip_fe(frame)
        assert raw[3] == 0x43                      # 客户机请求控制域

    def test_try_extract_dirty_bytes_unchanged(self):
        """脏字节/FE 前导后 698 与 645 都能重同步切出。"""
        frame698 = build_get_request_43000200(ADDR, security=False)
        res = MeterSerialChannel._try_extract(b"\x01\x02" + frame698)
        assert res is None                         # buf[0] 非 FE/68 → None（重同步由 _run 做）
        res = MeterSerialChannel._try_extract(b"\xFE\xFE" + frame698[4:])
        assert res is not None
        raw, consumed = res
        assert raw[3] == 0x43
        frame645 = build_frame_escaped(ADDR, 0x13, b"")
        res = MeterSerialChannel._try_extract(b"\xFE" + frame645)
        assert res is not None
        assert res[1] == len(frame645) + 1
