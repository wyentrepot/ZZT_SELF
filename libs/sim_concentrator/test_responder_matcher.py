"""responder 应答引擎 + matcher 匹配判定 单元测试（单 68 标准帧）。"""
import pytest

from sim_concentrator.frame_codec import build_13762_frame, decode_frame
from sim_concentrator.matcher import match_frame
from sim_concentrator.responder import Responder


def _up_frame(afn, fn=1, seq=0x05, appdata=b""):
    return build_13762_frame(afn=afn, fn=fn, direction="up",
                             info={"seq": seq}, appdata=appdata)


class TestResponder:
    def test_builtin_01_init_confirm(self):
        r = Responder()
        reply = r.reply_for(_up_frame(afn=0x01, seq=0x05))
        assert reply is not None
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x00  # 确认帧
        # seq 沿用上行（信息域报文序列号）
        info_field = d["fields"]["信息域R"]["raw"]
        assert info_field.endswith("05")

    def test_builtin_03_query_echo(self):
        r = Responder()
        reply = r.reply_for(_up_frame(afn=0x03, seq=0x02))
        assert reply is not None
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x03

    def test_no_match_returns_none(self):
        # 内置规则未覆盖 AFN=0xF1 时（无内置规则），应返回 None
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
        # 单 68 无独立 SEQ 字段；用信息域R hex 断言 seq
        ok, _, reasons = match_frame(raw, {"fields": {"信息域R": "000000000007"}})
        assert ok, reasons
        ok2, _, _ = match_frame(raw, {"fields": {"信息域R": "000000000008"}})
        assert not ok2

    def test_match_nested_645(self):
        from sim_concentrator.frame_codec import build_13762_frame as bf
        # 构造 AFN=02H 数据转发，含 645 帧（协议类型02 + 长度 + 内容）
        f645 = bytes.fromhex("6812345678901268910800000100123456783416")
        appdata = bytes([0x02, len(f645)]) + f645
        raw = bf(afn=0x02, fn=1, appdata=appdata, direction="up")
        ok, d, reasons = match_frame(raw, {"afn": 0x02, "nested": True})
        assert ok, reasons
        assert d["nested"][0]["structure"] == "645"

    def test_nested_field_assert(self):
        from sim_concentrator.frame_codec import build_13762_frame as bf
        f645 = bytes.fromhex("6812345678901268910800000100123456783416")
        appdata = bytes([0x02, len(f645)]) + f645
        raw = bf(afn=0x02, fn=1, appdata=appdata, direction="up")
        # 只断言能匹配到嵌套帧
        ok, _, reasons = match_frame(raw, {"afn": 0x02, "nested": True})
        assert ok
