"""MeterBrain 协议应答引擎单元测试（645-2007 / 645-1997）。

请求帧按线上口径构造（数据域 +33H、传输转义），应答帧断言控制码、
DI 回显、数据 BCD 与 +33H 传输形态。
"""
from datetime import datetime, timedelta

import pytest

from parser_lib.adapters.adapter_645 import build_frame, build_frame_escaped
from sim_meter.meter import MeterBrain

ADDR = bytes.fromhex("000000000001")
ADDR_B = b"\xFE" * 6


def _d33(data: bytes) -> bytes:
    return bytes((b + 0x33) & 0xFF for b in data)


def _bcd(n: int) -> int:
    return ((n // 10) << 4) | (n % 10)


def _unbcd(b: int) -> int:
    """BCD 字节 → 整数（0x26 → 26）。"""
    return ((b >> 4) & 0xF) * 10 + (b & 0xF)


def _bcd_time6(dt: datetime) -> bytes:
    """秒 分 时 日 月 年（BCD，低字节在前）。"""
    return bytes([_bcd(dt.second), _bcd(dt.minute), _bcd(dt.hour),
                  _bcd(dt.day), _bcd(dt.month), _bcd(dt.year % 100)])


def _req(ctrl: int, data: bytes = b"", addr: bytes = ADDR, escaped: bool = True) -> bytes:
    if escaped:
        return build_frame_escaped(addr, ctrl, _d33(data))
    return build_frame(addr, ctrl, _d33(data))


def _parse_reply(reply: bytes):
    """应答帧 → (addr, ctrl, 逻辑数据域)。"""
    assert reply[0] == 0x68 and reply[7] == 0x68 and reply[-1] == 0x16
    assert sum(reply[:-2]) % 256 == reply[-2], "应答帧 CS 校验失败"
    ctrl = reply[8]
    data = reply[10:-2]
    if ctrl != 0x93:  # 13H 应答地址不加 33H
        data = bytes((b - 0x33) & 0xFF for b in data)
    return reply[1:7], ctrl, data


@pytest.fixture
def brain():
    return MeterBrain(addr="000000000001", version="2007")


# ---------------------------------------------------------------------------
# 应答地址
# ---------------------------------------------------------------------------
class TestReadAddress:
    def test_broadcast_13h(self, brain):
        res = brain.handle_frame(_req(0x13, b"", addr=ADDR_B))
        assert res.reply is not None
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x93
        assert data == ADDR
        assert res.note["func"] == "读通信地址"

    def test_unicast_13h(self, brain):
        res = brain.handle_frame(_req(0x13))
        assert res.reply is not None
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x93 and data == ADDR

    def test_di_04000401_cco_flavor(self, brain):
        # CCO 固件读表地址口径：11H + DI 04000401H（线上 34 37 33 37）
        res = brain.handle_frame(_req(0x11, bytes.fromhex("01040004")))
        assert res.reply is not None
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x91
        assert data[:4] == bytes.fromhex("01040004")     # DI 回显
        assert data[4:] == ADDR
        assert res.note["func"] == "读通信地址(DI 04000401)"

    def test_write_address_15h(self, brain):
        new = bytes.fromhex("120000000007")
        res = brain.handle_frame(_req(0x15, new))
        assert res.reply is not None
        _, ctrl, _ = _parse_reply(res.reply)
        assert ctrl == 0x95
        assert brain.addr_hex == "120000000007"
        # 改址后旧地址不再应答
        res2 = brain.handle_frame(_req(0x13))
        assert res2.reply is None

    def test_write_address_raw_no33_fallback(self, brain):
        # 主站未加 33H 的兼容路径：数据域按原始地址解释
        new = bytes.fromhex("120000000009")
        req = build_frame(ADDR, 0x15, new)               # 未 +33H
        res = brain.handle_frame(req)
        assert res.reply is not None
        assert brain.addr_hex == "120000000009"


# ---------------------------------------------------------------------------
# 查询时间
# ---------------------------------------------------------------------------
class TestReadTime:
    def test_read_date_04000101(self, brain):
        res = brain.handle_frame(_req(0x11, bytes.fromhex("01010004")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x91
        assert data[:4] == bytes.fromhex("01010004")
        ww, dd, mo, yy = (_unbcd(data[4]), _unbcd(data[5]),
                          _unbcd(data[6]), _unbcd(data[7]))
        now = datetime.now()
        assert yy == now.year % 100 and mo == now.month and dd == now.day
        assert 1 <= ww <= 7

    def test_read_time_04000102(self, brain):
        res = brain.handle_frame(_req(0x11, bytes.fromhex("02010004")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x91
        ss, mi, hh = _unbcd(data[4]), _unbcd(data[5]), _unbcd(data[6])
        now = datetime.now()
        assert (hh, mi, ss) == (now.hour, now.minute, now.second)

    def test_broadcast_time_08h(self, brain):
        target = datetime.now() - timedelta(hours=3)
        req = build_frame(b"\x99" * 6, 0x08, _bcd_time6(target))  # 广播校时不加 33H
        res = brain.handle_frame(req)
        assert res.reply is None                          # 无需应答
        meter_now = brain.now()
        assert abs((meter_now - datetime.now()).total_seconds() + 3 * 3600) < 5

    def test_write_time_14h(self, brain):
        di = bytes.fromhex("02010004")
        pa_pwd_op = bytes(8)
        payload = bytes([_bcd(30), _bcd(20), _bcd(10)])   # hh=10 mi=20 ss=30（低在前）
        res = brain.handle_frame(_req(0x14, di + pa_pwd_op + payload))
        _, ctrl, _ = _parse_reply(res.reply)
        assert ctrl == 0x94
        n = brain.now()
        assert (n.hour, n.minute, n.second) == (10, 20, 30)


# ---------------------------------------------------------------------------
# 抄表
# ---------------------------------------------------------------------------
class TestReadData:
    def test_read_energy_total(self, brain):
        res = brain.handle_frame(_req(0x11, bytes.fromhex("00000100")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x91
        assert data[:4] == bytes.fromhex("00000100")
        bcd = data[4:]
        digits = "".join(f"{b >> 4}{b & 0xF}" for b in reversed(bcd))
        assert digits == "00123456"                       # 001234.56 kWh

    def test_set_then_read_energy(self, brain):
        brain.set_register("00010000", "000987.65")
        res = brain.handle_frame(_req(0x11, bytes.fromhex("00000100")))
        _, _, data = _parse_reply(res.reply)
        digits = "".join(f"{b >> 4}{b & 0xF}" for b in reversed(data[4:]))
        assert digits == "00098765"

    def test_read_voltage(self, brain):
        res = brain.handle_frame(_req(0x11, bytes.fromhex("00010102")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x91
        digits = "".join(f"{b >> 4}{b & 0xF}" for b in reversed(data[4:]))
        assert digits == "2201"                           # 220.1 V

    def test_alias_chongqing_minute_flavor(self, brain):
        # 重庆分钟采集口径：00000001H → 正向有功总电能
        res = brain.handle_frame(_req(0x11, bytes.fromhex("01000000")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x91
        assert data[4:] == bytes.fromhex("56341200")      # 001234.56 低字节在前

    def test_unknown_di_err(self, brain):
        res = brain.handle_frame(_req(0x11, bytes.fromhex("99000100")))
        assert res.reply is not None
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0xD1
        assert data == bytes([0x02])                      # 无请求数据

    def test_read_next_12h_err(self, brain):
        res = brain.handle_frame(_req(0x12, bytes.fromhex("00000100") + b"\x01"))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0xD2 and data == bytes([0x02])


# ---------------------------------------------------------------------------
# 波特率
# ---------------------------------------------------------------------------
class TestBaudrate:
    def test_change_baud_17h(self, brain):
        res = brain.handle_frame(_req(0x17, bytes([0x20])))   # b5=9600
        assert res.reply is not None
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x97
        assert data == bytes([0x20])                          # 回显速率特征字
        assert brain.baudrate == 9600
        assert res.action == ("baud", 9600)

    def test_change_baud_09h_compat(self, brain):
        res = brain.handle_frame(_req(0x09, bytes([0x04])))   # 知识库口径 09H → 2400
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x89
        assert brain.baudrate == 2400
        assert res.action == ("baud", 2400)

    def test_change_baud_raw_no33(self, brain):
        req = build_frame(ADDR, 0x17, bytes([0x40]))          # 未加 33H，b6=19200
        res = brain.handle_frame(req)
        assert brain.baudrate == 19200

    def test_change_baud_invalid_word(self, brain):
        res = brain.handle_frame(_req(0x17, bytes([0x30])))   # 多 bit 组合无效
        _, ctrl, _ = _parse_reply(res.reply)
        assert ctrl == 0xD7


# ---------------------------------------------------------------------------
# 总线纪律与健壮性
# ---------------------------------------------------------------------------
class TestBusDiscipline:
    def test_other_address_no_reply(self, brain):
        res = brain.handle_frame(_req(0x11, bytes.fromhex("00000100"),
                                      addr=bytes.fromhex("999999999999")))
        assert res.reply is None
        assert res.note.get("mine") is False

    def test_cs_error_no_reply(self, brain):
        req = bytearray(_req(0x11, bytes.fromhex("00000100")))
        req[-2] ^= 0xFF                                       # 破坏 CS
        res = brain.handle_frame(bytes(req))
        assert res.reply is None

    def test_leading_fe_preamble(self, brain):
        req = b"\xFE" * 4 + _req(0x11, bytes.fromhex("00000100"))
        res = brain.handle_frame(req)
        assert res.reply is not None

    def test_unknown_ctrl_err(self, brain):
        res = brain.handle_frame(_req(0x1A, bytes(4)))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0xDA                                   # 0x1A|0xC0
        assert data == bytes([0x01])

    def test_13h_response_addr_not_33h(self, brain):
        # 13H 应答的地址为原始字节（不加 33H），与知识库示例一致
        res = brain.handle_frame(_req(0x13, b"", addr=ADDR_B))
        wire = res.reply_wire
        assert wire[9] == 0x06
        assert wire[10:16] == ADDR


# ---------------------------------------------------------------------------
# 645-1997 模式
# ---------------------------------------------------------------------------
class TestMode1997:
    @pytest.fixture
    def brain97(self):
        return MeterBrain(addr="000000000001", version="97")

    def test_version_switch(self, brain):
        assert brain.set_version("97") == "1997"
        assert brain.set_version("07") == "2007"
        with pytest.raises(ValueError):
            brain.set_version("25")

    def test_read_energy_9010(self, brain97):
        # CCO 固件常量：线上 43 C3（= DI 9010H + 33H）
        res = brain97.handle_frame(_req(0x01, bytes.fromhex("1090")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x81
        assert data[:2] == bytes.fromhex("1090")
        digits = "".join(f"{b >> 4}{b & 0xF}" for b in reversed(data[2:]))
        assert digits == "00123456"

    def test_read_addr_c032(self, brain97):
        # CCO 固件常量：线上 65 F3（= DI C032H + 33H）
        res = brain97.handle_frame(_req(0x01, bytes.fromhex("32C0")))
        _, ctrl, data = _parse_reply(res.reply)
        assert ctrl == 0x81
        assert data[2:] == ADDR

    def test_broadcast_time_05h(self, brain97):
        target = datetime.now() + timedelta(minutes=10)
        req = build_frame(b"\x99" * 6, 0x05, _bcd_time6(target))
        res = brain97.handle_frame(req)
        assert res.reply is None
        assert abs((brain97.now() - datetime.now()).total_seconds() - 600) < 5

    def test_change_baud_07h(self, brain97):
        res = brain97.handle_frame(_req(0x07, bytes([0x20])))
        _, ctrl, _ = _parse_reply(res.reply)
        assert ctrl == 0x87
        assert brain97.baudrate == 9600

    def test_08h_in_97_is_password_not_time(self, brain97):
        # 97 模式下 08H 是修改密码：广播地址不应答，时钟也不被改动
        before = brain97.now()
        req = build_frame(b"\x99" * 6, 0x08, _bcd_time6(datetime.now()))
        res = brain97.handle_frame(req)
        assert res.reply is None
        assert abs((brain97.now() - before).total_seconds()) < 2   # 时钟未被校时
        # 单播到本表 → 按未知控制码异常应答 C8H
        res2 = brain97.handle_frame(_req(0x08, bytes(8)))
        assert res2.reply is not None and res2.reply[8] == 0xC8
