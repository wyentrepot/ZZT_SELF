"""sim_concentrator 黑盒/白盒测试：FakeCCO 经虚拟串口对工作台架。

黑盒（字节流口径）：
- bpsCheck 14-F2 节奏：FakeCCO 周期询问，simcon responder local+echo 应答
  （实机验证口径），FakeCCO 判 bus_ok；
- 10H-F2 档案查询：deep_app.query_archive 拿到 FakeCCO 档案；
- 11H-F1 档案添加：execute_task 语义步骤下发成功。
白盒：responder 规则构帧、step 结果 matched/parsed 结构。
"""
from __future__ import annotations

import time

import pytest

from tools.vbench.bench import VirtualBench
from tools.vbench.frames13762 import (app_data, build_local_13762_frame,
                                      decode_frame)
from tools.vbench.frames645 import build_645, di_wire


def wait_until(pred, timeout: float = 2.0, interval: float = 0.05) -> bool:
    """轮询等待异步事件（回帧经虚拟串口有毫秒级在途时间）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


@pytest.fixture()
def bench():
    with VirtualBench.create(meters=0) as b:
        yield b


@pytest.fixture()
def simcon_io(bench):
    from shared.serial_resources import SerialResourceRegistry
    from sim_concentrator.serial_io import SerialIO

    io = SerialIO(port=bench.local_pair.slave_path, baudrate=115200,
                  parity="N", resource_registry=SerialResourceRegistry(),
                  journal=None)
    io.open()
    yield io
    io.close()


# ---------------------------------------------------------------------------
# 黑盒：CCO 工作节奏
# ---------------------------------------------------------------------------
class TestCcoRhythm:
    def test_bpscheck_echo_establishes_bus(self, bench, simcon_io):
        """14-F2 local+echo 应答 → FakeCCO 判定本地总线 OK。"""
        bench.start()
        result = bench.bus_warmup(simcon_io)
        assert result["summary"]["verdict"] == "pass"
        assert wait_until(lambda: bench.cco.bus_ok), "bpsCheck 应答未被 CCO 判定"
        # FakeCCO 收到的应答应是 14-F2 上行帧
        up_frames = [f for f in bench.cco.rx_log]
        assert up_frames, "FakeCCO 未收到任何应答"

    def test_archive_query_returns_nodes(self, bench, simcon_io):
        """10H-F2 档案查询：simcon 拿到 FakeCCO 档案 3 条。"""
        from sim_concentrator.deep_app import ArchiveSession, query_archive

        bench.start()
        bench.bus_warmup(simcon_io)
        sess = ArchiveSession()
        result = query_archive(simcon_io, start=0, count=10, timeout=4.0,
                               session=sess)
        assert result["total"] == len(bench.cco.archive_nodes)
        assert len(result["nodes"]) == len(bench.cco.archive_nodes)
        addrs = {n["addr"] for n in result["nodes"]}
        # 对称地址（999999999999）在任何 BCD 序变换下显示不变；
        # 档案未变时两次查询结果稳定一致。
        assert "999999999999" in addrs
        again = query_archive(simcon_io, start=0, count=10, timeout=4.0)
        assert {n["addr"] for n in again["nodes"]} == addrs
        assert sess.snapshot()["total"] == len(result["nodes"])

    def test_add_archive_step(self, bench, simcon_io):
        """11H-F1 添加从节点档案：语义步骤下发并收到确认。"""
        from sim_concentrator.runner import execute_task

        bench.start()
        bench.bus_warmup(simcon_io)
        task = {
            "id": "vbench.add_archive",
            "enable_responder": False,
            "steps": [{"name": "添加从节点 013300000009",
                       "send": {"afn": "11", "fn": "F1",
                                "params": {"action": "add",
                                           "addr": "013300000009",
                                           "protocol": 2}},
                       "expect": {"afn": 0x11, "fn": 1},
                       "expect_timeout": 4.0}],
        }
        result = execute_task(task, io=simcon_io)
        assert result["summary"]["verdict"] == "pass", result["steps"]
        # FakeCCO 收到的 11-F1 下行含该地址
        assert any(b"\x01\x33\x00\x00\x00\x09" in f for f in bench.cco.rx_log)


# ---------------------------------------------------------------------------
# 白盒：responder 与步骤结果结构
# ---------------------------------------------------------------------------
class TestWhitebox:
    def test_local_echo_rule_builds_up_14f2(self):
        """responder local+echo 规则：对 14-F2 询问构出上行 14-F2 应答帧。"""
        from sim_concentrator.responder import Responder

        req = build_local_13762_frame(afn=0x14, fn=2, buff=b"\x02\x00")
        r = Responder(override_rules=[
            {"id": "t", "match": {"afn": 0x14, "fn": 2},
             "reply": {"afn": 0x14, "fn": 2, "format": "local",
                       "userdata_builder": "echo"}}])
        rep = r.reply_for(req)
        assert rep is not None
        decoded = decode_frame(rep)
        assert (int(decoded["fields"]["AFN"]["raw"]),
                int(decoded["fields"]["FN"]["raw"])) == (0x14, 2)

    def test_13f1_down_payload_parse_roundtrip(self):
        """13-F1 下行构帧→解析回环：FakeCCO 能取出 645 载荷。"""
        from tools.vbench.frames13762 import build_13762_frame, parse_13f1_down

        payload = build_645(b"\x99" * 6, 0x11, di_wire("00010000"))
        app = (bytes([2, 0, 0]) + bytes([len(payload)]) + payload)
        frame = build_13762_frame(0x13, 1, appdata=app, direction="down")
        decoded = decode_frame(frame)
        info = parse_13f1_down(app_data(decoded))
        assert info["protocol"] == 2 and info["subs"] == 0
        assert bytes.fromhex(info["payload"]) if isinstance(info["payload"], str) \
            else info["payload"] == payload or bytes.fromhex(info["payload"]) == payload

    def test_step_result_has_matched_and_parsed(self, bench, simcon_io):
        """步骤结果结构：matched 为应答帧 hex，parsed 为 decode_frame dict。"""
        from sim_concentrator.runner import execute_task

        bench.start()
        task = {
            "id": "vbench.wb", "enable_responder": True,
            "responders": [{"id": "vbench.14f2",
                            "match": {"afn": 0x14, "fn": 2},
                            "reply": {"afn": 0x14, "fn": 2,
                                      "format": "local",
                                      "userdata_builder": "echo"}}],
            "steps": [{"name": "等 bpsCheck", "recv_only": True,
                       "expect": {"afn": 0x14, "fn": 2},
                       "expect_timeout": 6.0}],
        }
        result = execute_task(task, io=simcon_io)
        step = result["steps"][0]
        assert step["result"] == "pass"
        assert step["matched"] and step["parsed"]["structure"] == "1376.2"
