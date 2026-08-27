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


class TestBuildSendFrame:
    def test_rtsa_str_reversed(self):
        raw = build_send_frame({"afn": 0x01, "rtsa": "070919051620",
                                "userdata": "00"})
        d = decode_frame(raw)
        # rtsa → 地址域 src+dst
        assert "070919051620" in d["fields"]["地址域A"]["value"]

    def test_userdata_hex_str(self):
        raw = build_send_frame({"afn": 0x02, "rtsa": "070919051620",
                                "userdata": "00 01"})
        assert b"\x00\x01" in raw


class TestExecuteTask:
    def test_send_and_expect_confirm(self):
        task = {
            "id": "t1",
            "port": "COM_TEST",
            "enable_responder": False,
            "steps": [
                {"name": "下发01H", "send": {"afn": 0x01, "rtsa": "070919051620"},
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
            "enable_responder": False,
            "fail_fast": True,
            "steps": [
                {"name": "期望确认却收到03", "send": {"afn": 0x01, "rtsa": "070919051620"},
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
            "id": "t3", "port": "COM_TEST", "enable_responder": False,
            "steps": [
                {"name": "无响应", "send": {"afn": 0x01, "rtsa": "070919051620"},
                 "expect": {"afn": 0x00}, "expect_timeout": 0.2},
            ],
        }
        io = FakeIO(responses=[])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "fail"
        assert "超时" in out["steps"][0]["reason"]

    def test_expect_no_reply(self):
        task = {
            "id": "t4", "port": "COM_TEST", "enable_responder": False,
            "steps": [
                {"name": "期望无响应", "send": {"afn": 0x01, "rtsa": "070919051620"},
                 "expect_no_reply": True, "expect_timeout": 0.2},
            ],
        }
        io = FakeIO(responses=[])
        out = execute_task(task, io=io)
        assert out["summary"]["verdict"] == "pass"

    def test_step_responder_override(self):
        task = {
            "id": "t5", "port": "COM_TEST",
            "enable_responder": False,
            "steps": [
                {
                    "name": "应答引擎联动",
                    "send": {"afn": 0x01, "rtsa": "070919051620"},
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
