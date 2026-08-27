"""runner 执行闭环单元测试：用假 SerialIO 驱动下发→应答→匹配→判定（单 68）。"""
from sim_concentrator.frame_codec import build_13762_frame, decode_frame
from sim_concentrator.runner import build_send_frame, execute_task


class FakeIO:
    """假串口：send 触发预置响应回放；recv 从队列取。"""

    def __init__(self, responses=None, port="COM_TEST"):
        self.responses = list(responses or [])
        self.sent = []
        self.pending = []
        self.port = port
        self.closed = False

    def open(self):
        return True

    def close(self):
        self.closed = True

    def is_open(self):
        return True

    def send_frame(self, raw):
        self.sent.append(raw)
        # 每发一帧，回放一个预置响应（若有）
        if self.responses:
            self.pending.append(self.responses.pop(0))

    def send_hex(self, hex_str):
        from sim_concentrator.frame_codec import hex_to_bytes
        self.send_frame(hex_to_bytes(hex_str))

    def recv_frame(self, timeout=None):
        import time
        end = time.time() + (timeout if timeout else 1.0)
        while time.time() < end:
            if getattr(self, "pending", None):
                return self.pending.pop(0)
            time.sleep(0.01)
        return None

    def pending_frames(self):
        return len(getattr(self, "pending", []))


def _confirm_reply(seq=1):
    return build_13762_frame(afn=0x00, fn=1, direction="up", info={"seq": seq})


_PROFILE = {
    "id": "test",
    "cco_addr": "070919051620",
    "comm_mode": 3,
    "seq_auto": True,
}


class TestBuildSendFrame:
    def test_address_field_from_profile(self):
        """下行带 profile：地址域含 cco_addr（src）。"""
        raw = build_send_frame(
            {"afn": 0x01, "fn": 1}, profile=_PROFILE, seq=1)
        d = decode_frame(raw)
        # 地址域 src = cco_addr；无显式目标时 A3 同址
        assert "070919051620" in d["fields"]["地址域A"]["value"]

    def test_userdata_via_params(self):
        """params 应用数据经 13762 库编码进帧（10H-F2 查询从节点）。"""
        raw = build_send_frame(
            {"afn": 0x10, "fn": 2,
             "params": {"start": 0, "count": 16}},
            profile=_PROFILE, seq=1)
        assert b"\x00\x00\x10" in raw  # start=0(2B) + count=16(1B)


class TestExecuteTask:
    def test_send_and_expect_confirm(self):
        task = {
            "id": "t1",
            "port": "COM_TEST",
            "profile": "test",
            "enable_responder": False,
            "steps": [
                {"name": "下发01H", "send": {"afn": 0x01, "fn": 1},
                 "expect": {"afn": 0x00}},
            ],
        }
        io = FakeIO(responses=[_confirm_reply()])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "pass"
        assert out["steps"][0]["result"] == "pass"

    def test_expect_afn_mismatch_fail(self):
        # 收到错误 AFN 帧不立即判 fail：跳过继续等，超时仍未收到期望帧才 fail
        task = {
            "id": "t2",
            "port": "COM_TEST",
            "profile": "test",
            "enable_responder": False,
            "fail_fast": True,
            "steps": [
                {"name": "期望确认却收到03", "send": {"afn": 0x01, "fn": 1},
                 "expect": {"afn": 0x00}, "expect_timeout": 0.3},
            ],
        }
        io = FakeIO(responses=[build_13762_frame(afn=0x03, fn=1, direction="up")])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "fail"
        assert out["steps"][0]["result"] == "fail"
        assert "超时" in out["steps"][0]["reason"]
        assert len(out["steps"][0].get("received_hex", [])) == 1

    def test_expect_timeout_fail(self):
        task = {
            "id": "t3", "port": "COM_TEST", "profile": "test", "enable_responder": False,
            "steps": [
                {"name": "无响应", "send": {"afn": 0x01, "fn": 1},
                 "expect": {"afn": 0x00}, "expect_timeout": 0.2},
            ],
        }
        io = FakeIO(responses=[])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "fail"
        assert "超时" in out["steps"][0]["reason"]

    def test_expect_no_reply(self):
        task = {
            "id": "t4", "port": "COM_TEST", "profile": "test", "enable_responder": False,
            "steps": [
                {"name": "期望无响应", "send": {"afn": 0x01, "fn": 1},
                 "expect_no_reply": True, "expect_timeout": 0.2},
            ],
        }
        io = FakeIO(responses=[])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "pass"

    def test_step_responder_override(self):
        task = {
            "id": "t5", "port": "COM_TEST",
            "profile": "test",
            "enable_responder": False,
            "steps": [
                {
                    "name": "应答引擎联动",
                    "send": {"afn": 0x01, "fn": 1},
                    "expect": {"afn": 0x00},
                    "responders": [{"match": {"afn": 0x01},
                                    "reply": {"afn": 0x00, "userdata_builder": "confirm"}}],
                },
            ],
        }
        io = FakeIO(responses=[_confirm_reply()])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "pass"
        assert out["steps"][0]["result"] == "pass"
