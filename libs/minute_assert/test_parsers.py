"""parsers：分钟采集日志单行解析器 契约测试（移植自 H_CCO/analyze_minute_logs.py）。"""
import pytest

from minute_assert.parsers import (
    parse_active_report,
    parse_read_frame,
    parse_task_config,
    parse_full_f231_frame,
    parse_f232_frame,
)


class TestParseActiveReport:
    def test_parse_anhui_report(self):
        # 表 000000000008，任务 3，周期 2 分钟（来自 H_LOG 真实行）
        line = "11e400000132400000001200010800000000000343004608310726000000"
        result = parse_active_report(line)
        assert result is not None
        address, task_id, fz_bytes, result_code = result
        assert address == "000000000008"
        assert task_id == 3
        assert fz_bytes == bytes.fromhex("004608310726")
        assert result_code == 2  # 与原始脚本一致（data[20]>>5）

    def test_parse_other_task_report(self):
        line = "11e400000132b3000000370001610000141223010200450831072601250068610000141223689106333434357356aa16686100001412236891073334"
        result = parse_active_report(line)
        assert result is not None
        address, task_id, fz_bytes, _ = result
        assert address == "231214000061"
        assert task_id == 1
        assert fz_bytes == bytes.fromhex("004508310726")

    def test_non_report_line_returns_none(self):
        assert parse_active_report("11e20000c10315000000080000000000030102") is None
        assert parse_active_report("hello world") is None

    def test_short_line_returns_none(self):
        assert parse_active_report("11e4000001") is None


class TestParseReadFrame:
    def test_parse_request(self):
        # 11e3 request（被动采集下发）
        line = "11e300000105030000004208000000000001004608310726"
        result = parse_read_frame(line)
        assert result is not None
        direction, task_id, address, fz_bytes, result_code = result
        assert direction == "request"
        assert task_id == 1
        assert address == "000000000008"
        assert result_code is None

    def test_parse_reply(self):
        # 11e3 reply（被动上报）
        line = "11e30000c115020000000208000000000001004608310726000000"
        result = parse_read_frame(line)
        assert result is not None
        direction, task_id, address, fz_bytes, result_code = result
        assert direction == "reply"
        assert task_id == 1
        assert address == "000000000008"
        assert result_code == 0


class TestParseTaskConfig:
    def test_parse_config(self):
        line = "11e20000c10315000000080000000000030102"
        result = parse_task_config(line)
        assert result == (3, 2, 1)  # 任务3，周期2分钟，启停=1

    def test_parse_delete(self):
        line = "11e20000c10315000000080000000000030002"
        result = parse_task_config(line)
        assert result == (3, 2, 0)  # 任务3 删除

    def test_parse_broadcast_delete(self):
        line = "11e20000c10315000000080000000000ff0002"
        result = parse_task_config(line)
        assert result == (0xFF, 0, 0)  # 全部任务删除

    def test_parse_full_f231(self):
        # 完整 1376.2 帧 11H_F231：任务7/启停1/周期5
        # build_13762_frame(serial=1, afn=0x11, fn=231, payload=任务7/启停1/周期5) 实测解析 (7,5,1)
        line = "681f004300000000000111401c07010005000000000000000000000000be16"
        assert parse_full_f231_frame(line) == (7, 5, 1)


class TestParseF232Frame:
    def test_parse_archives(self):
        # 68 帧 11H_F232：任务1 + 2 个档案（000000000008 / 231214000050）
        payload = bytes((1,)) + (2).to_bytes(2, "little") + bytes.fromhex("080000000000") + bytes.fromhex("500000141223")
        frame = bytearray([0x68, 0, 0, 0x43]) + bytes(5) + bytes((0x01, 0x11, 0x80, 0x1C)) + payload
        frame[1:3] = len(frame).to_bytes(2, "little")
        frame.append(sum(frame[3:]) & 0xFF)
        frame.append(0x16)
        result = parse_f232_frame(bytes(frame))
        assert result is not None
        task_id, addresses = result
        assert task_id == 1
        # 地址字节倒序后转大写十六进制（与原始脚本一致）
        assert addresses == ("000000000008", "231214000050")

    def test_parse_rejects_bad_frame(self):
        assert parse_f232_frame(b"\x68\x00\x00\x00" + bytes(20)) is None
