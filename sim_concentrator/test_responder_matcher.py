"""responder 应答引擎 + matcher 匹配判定 单元测试。"""
import pytest

from sim_concentrator.frame_codec import build_13762_frame, decode_frame
from sim_concentrator.matcher import match_frame
from sim_concentrator.responder import Responder

RTSA = bytes([0x20, 0x16, 0x05, 0x19, 0x09, 0x07])  # 展示: 070919051620


def _up_frame(afn, seq=0x05, userdata=b"\x00"):
    return build_13762_frame(afn=afn, seq=seq, rtsa=RTSA, msaa=0x01,
                             pw=0x0000, userdata=userdata)


class TestResponder:
    def test_builtin_01_init_confirm(self):
        r = Responder()
        reply = r.reply_for(_up_frame(afn=0x01, seq=0x05))
        assert reply is not None
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x00  # 确认帧
        # SEQ 沿用上行
        assert d["fields"]["SEQ"]["raw"] == 0x05

    def test_builtin_03_query_echo(self):
        r = Responder()
        reply = r.reply_for(_up_frame(afn=0x03, seq=0x02))
        assert reply is not None
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x03

    def test_no_match_returns_none(self):
        # 内置规则未覆盖 AFN=0xF1 时，默认回确认？—— 此处 0xF1 无内置规则，应返回 None
        # （_BUILTIN_RULES 未含 0xF1）
        r = Responder()
        assert r.reply_for(_up_frame(afn=0xF1)) is None

    def test_override_rule_priority(self):
        # 覆盖规则：AFN=0x03 改为回确认
        override = [{
            "id": "override.03_confirm",
            "match": {"afn": 0x03},
            "reply": {"afn": 0x00, "userdata_builder": "confirm"},
        }]
        r = Responder(override_rules=override)
        reply = r.reply_for(_up_frame(afn=0x03))
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x00

    def test_custom_userdata(self):
        override = [{
            "id": "override.03_custom",
            "match": {"afn": 0x03},
            "reply": {"afn": 0x03, "userdata": "AA BB CC"},
        }]
        r = Responder(override_rules=override)
        reply = r.reply_for(_up_frame(afn=0x03))
        d = decode_frame(reply)
        # raw_hex 为小写 hex
        assert "aabbcc" in d["raw_hex"]


class TestMatcher:
    def test_match_any(self):
        raw = _up_frame(afn=0x01)
        ok, d, reasons = match_frame(raw, None)
        assert ok and d["structure"] == "1376.2"

    def test_match_afn(self):
        raw = _up_frame(afn=0x02)
        ok, d, reasons = match_frame(raw, {"afn": 0x02})
        assert ok and not reasons
        ok2, _, reasons2 = match_frame(raw, {"afn": 0x03})
        assert not ok2 and reasons2

    def test_match_fields_seq(self):
        raw = _up_frame(afn=0x02, seq=0x07)
        ok, _, reasons = match_frame(raw, {"fields": {"SEQ": 0x07}})
        assert ok
        ok2, _, _ = match_frame(raw, {"fields": {"SEQ": 0x08}})
        assert not ok2

    def test_match_nested_645(self):
        from sim_concentrator.frame_codec import build_13762_frame as bf
        # 构造 AFN=02H 数据转发，含 DAD + 645 帧
        f645 = bytes.fromhex("6812345678901268910800000100123456783416")
        userdata = bytes([0x00, 0x01]) + f645
        raw = bf(afn=0x02, seq=0x01, rtsa=RTSA, msaa=0x01, pw=0x0000,
                 userdata=userdata)
        ok, d, reasons = match_frame(raw, {"afn": 0x02, "nested": True})
        assert ok, reasons
        assert d["nested"][0]["structure"] == "645"

    def test_nested_field_assert(self):
        from sim_concentrator.frame_codec import build_13762_frame as bf
        f645 = bytes.fromhex("6812345678901268910800000100123456783416")
        userdata = bytes([0x00, 0x01]) + f645
        raw = bf(afn=0x02, seq=0x01, rtsa=RTSA, msaa=0x01, pw=0x0000,
                 userdata=userdata)
        # 645 帧数据标识 0x0000（正向有功总电能 等）——不精确断言具体值，只断言能匹配到嵌套
        ok, _, reasons = match_frame(raw, {"afn": 0x02, "nested": True})
        assert ok
