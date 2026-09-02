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

    def test_builtin_14F2_clock_reply(self):
        # 14H-F2 路由请求集中器时钟：上行无数据单元 → 应答 14H-F2 + 6B BCD 时间
        r = Responder()
        reply = r.reply_for(_up_frame(afn=0x14, fn=2, seq=0x06))
        assert reply is not None
        d = decode_frame(reply)
        assert d["fields"]["AFN"]["raw"] == 0x14
        assert d["fields"]["FN"]["raw"] == 2
        # 应用数据 = 6 字节 BCD 时间（秒分时日月年）
        # raw_hex 为小写 hex；R(6B) + AFN + DT1 + DT2 后为时间数据
        import re
        hexstr = d["raw_hex"].replace(" ", "")
        m = re.search(r"68[0-9a-f]{6}14(?:[0-9a-f]{2}){2}([0-9a-f]{12})16$", hexstr)
        assert m, f"未找到时间数据: {hexstr}"
        time_hex = m.group(1)
        assert len(time_hex) == 12
        assert int(time_hex[0:2], 16) <= 0x59  # 秒
        assert int(time_hex[2:4], 16) <= 0x59  # 分
        assert int(time_hex[4:6], 16) <= 0x23  # 时
        # seq 沿用上行
        info_field = d["fields"]["信息域R"]["raw"]
        assert info_field.endswith("06")

    def test_builtin_14F2_single_reply_not_confirm(self):
        """14H-F2 主动应答：收到一帧上行只回 1 帧（14F2 应答帧），
        不会额外回 00H 确认帧，也不会是确认帧+应答帧两帧。"""
        r = Responder()
        up = _up_frame(afn=0x14, fn=2, seq=0x08)
        reply = r.reply_for(up)
        assert reply is not None
        # reply_for 对一帧上行只返回一帧（唯一应答），不会返回多帧列表
        assert isinstance(reply, bytes)
        d = decode_frame(reply)
        # 应答必须是 14H-F2 本身（非 00H 确认帧）
        assert d["fields"]["AFN"]["raw"] == 0x14
        assert d["fields"]["FN"]["raw"] == 2
        assert d["fields"]["AFN"]["raw"] != 0x00

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
