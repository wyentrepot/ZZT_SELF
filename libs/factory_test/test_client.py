"""厂测客户端逻辑测试：用 FakeSerial 模拟 STA 应答，不需要硬件。"""

from __future__ import annotations

from factory_test.client import FactoryTestClient
from factory_test.codec import build_response


class FakeSerial:
    """最小串口替身：收到 A5 请求后按命令生成应答。"""

    def __init__(self, rf_ok: bool = True) -> None:
        self.rx = bytearray()
        self.writes = []
        self.rf_ok = rf_ok
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def read(self, size: int = 1) -> bytes:
        if not self.rx:
            return b""
        chunk = bytes(self.rx[:size])
        del self.rx[:size]
        return chunk

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        self._respond(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.rx.clear()

    def close(self) -> None:
        self.is_open = False

    def _respond(self, data: bytes) -> None:
        if len(data) < 6 or data[0] != 0xA5:
            return
        ctrl = data[2]
        di = data[4] | (data[5] << 8)

        if (ctrl & 0x0F) == 0:  # 读请求
            if di == 0x0100:
                self.rx.extend(build_response(ctrl | 0x80, di, b"\x29"))
            elif di == 0x010B:
                self.rx.extend(build_response(ctrl | 0x80, di, b"\x03"))
            elif di == 0x0001:
                self.rx.extend(
                    build_response(ctrl | 0x80, di, bytes.fromhex("55 55 55 55 55 55 00 00"))
                )
            elif di == 0x0000:
                self.rx.extend(
                    build_response(ctrl | 0x80, di, bytes.fromhex("01 01 01 01 00 00 00"))
                )
            elif di == 0x000E:
                self.rx.extend(
                    build_response(ctrl | 0x80, di, bytes.fromhex("01 01 06 09 00 23 10 26"))
                )
            elif di == 0x010A:
                self.rx.extend(
                    build_response(ctrl | 0x80, di, bytes.fromhex("21 00 12 0A 0B"))
                )
            elif di == 0x0204:
                self.rx.extend(
                    build_response(ctrl | 0x80, di, bytes.fromhex("30 08 20 07"))
                )
            elif di in (0x0103, 0x0206):
                self.rx.extend(build_response(ctrl | 0x80, di, b"\x22"))
            else:
                self.rx.extend(build_response(0x83))
            return

        # 写请求
        if di == 0x0109 and not self.rf_ok:
            self.rx.extend(build_response(0x83))
        else:
            self.rx.extend(build_response(0x82))


def make_client(rf_ok: bool = True) -> FactoryTestClient:
    client = FactoryTestClient()
    client._ser = FakeSerial(rf_ok=rf_ok)
    return client


def test_read_channel_and_option():
    client = make_client()
    assert client.get_channel() == 0x29
    assert client.get_option() == 3


def test_read_version_address_and_call_results():
    client = make_client()
    version = client.read_version()
    assert version["hversion"] == 0x0101
    assert version["sversion"] == 0x000101

    address = client.read_address()
    assert address["mac"] == "55:55:55:55:55:55"

    rf = client.read_rf_call_result()
    assert rf == {"rx_rssi": 0x21, "freq_dev": 0, "tx_rssi": 0x12, "rx_snr": 10, "tx_snr": 11}

    hplc = client.read_hplc_call_result()
    assert hplc == {"rx_rssi": 0x30, "rx_snr": 8, "tx_rssi": 0x20, "tx_snr": 7}


def test_write_channel_and_option():
    client = make_client()
    assert client.set_channel(0x32).ctrl == 0x82
    assert client.set_option(3).ctrl == 0x82


def test_enter_and_reset():
    client = make_client()
    client.enter(at_prelude=False, timeout=1.0)
    assert client.reset().ctrl == 0x82


def test_rf_call_success_and_nak():
    client = make_client(rf_ok=True)
    assert client.rf_call(100) is True

    client = make_client(rf_ok=False)
    assert client.rf_call(100) is False
