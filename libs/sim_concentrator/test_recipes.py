# -*- coding: utf-8 -*-
"""常用步骤（recipe）库测试：加载/参数化/执行/API（REQS-0028）。"""
import pytest
from fastapi.testclient import TestClient

from sim_concentrator.frame_codec import build_13762_frame, decode_frame
from sim_concentrator.recipes import (
    MAX_BATCH,
    build_task,
    get_recipe,
    list_recipes,
    run_recipe,
)
from sim_concentrator.api import create_simcon_app


class FakeIO:
    """假串口：send 触发预置响应回放；recv 从队列取（复用 test_runner.FakeIO 风格）。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.sent = []
        self.pending = []
        self.port = "COM_TEST"
        self.closed = False

    def open(self):
        return True

    def close(self):
        self.closed = True

    def is_open(self):
        return True

    def send_frame(self, raw):
        self.sent.append(raw)
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


def _query_reply(total=0, addrs=()):
    """构造 10H-F2 查询应答帧：总数量 + 本次数量 + 地址序列。"""
    appdata = bytearray()
    appdata += total.to_bytes(2, "little")       # 从节点总数量（2B 小端）
    appdata += bytes([len(addrs)])                # 本次应答数量
    for a in addrs:                              # 每地址 6B BCD + 1B 信息
        appdata += bytes.fromhex(a)
        appdata += b"\x0f\x18"
    return build_13762_frame(
        afn=0x10, fn=2, appdata=bytes(appdata), direction="up", info={"seq": 1})


def _confirm_reply():
    return build_13762_frame(afn=0x00, fn=1, direction="up", info={"seq": 1})


# ---------------------------------------------------------------------------
# 加载 / 参数化
# ---------------------------------------------------------------------------
class TestRecipesCatalog:
    def test_list_recipes_contains_expected(self):
        ids = [r["id"] for r in list_recipes()]
        assert "clear_archive" in ids
        assert "add_archive" in ids

    def test_get_recipe_metadata(self):
        r = get_recipe("clear_archive")
        assert r["id"] == "clear_archive"
        assert r["name"]
        assert any(p["key"] == "confirm" for p in r["params"])

    def test_get_recipe_missing_raises(self):
        with pytest.raises(KeyError):
            get_recipe("no_such_recipe")


class TestAddArchiveBuildTask:
    def test_build_task_single_meter(self):
        task = build_task("add_archive", {"meters": ["013300000001"], "protocol": 2})
        assert task["id"] == "add_archive"
        assert len(task["steps"]) == 1
        send = task["steps"][0]["send"]
        assert send["afn"] == "11" and send["fn"] == "F1"
        assert send["params"]["addr"] == "013300000001"
        assert send["params"]["protocol"] == 2

    def test_build_task_multiple_meters(self):
        task = build_task("add_archive",
                          {"meters": ["013300000001", "013300000002", "013300000003"]})
        assert len(task["steps"]) == 3

    def test_build_task_missing_meters_raises(self):
        with pytest.raises(ValueError):
            build_task("add_archive", {})

    def test_build_task_empty_meters_raises(self):
        with pytest.raises(ValueError):
            build_task("add_archive", {"meters": []})

    def test_build_task_over_batch_raises(self):
        with pytest.raises(ValueError):
            build_task("add_archive", {"meters": [f"01{i:09d}" for i in range(MAX_BATCH + 1)]})

    def test_clear_archive_build_task_raises(self):
        # clear_archive 是动态流程，build_task 应拒绝
        with pytest.raises(ValueError):
            build_task("clear_archive", {"confirm": True})


# ---------------------------------------------------------------------------
# 执行：clear_archive
# ---------------------------------------------------------------------------
class TestRunClearArchive:
    def test_skip_without_confirm(self):
        io = FakeIO()
        result = run_recipe("clear_archive", {}, io=io)
        assert result["summary"]["verdict"] == "skip"
        assert io.sent == []  # 未下发任何帧

    def test_clear_empty_archive(self):
        # 查询返回 total=0（查询 + 复查各需一个应答）
        io = FakeIO(responses=[_query_reply(total=0, addrs=[]), _query_reply(total=0, addrs=[])])
        result = run_recipe("clear_archive", {"confirm": True}, io=io)
        s = result["summary"]
        assert s["cleared"] is True
        assert s["remain"] == 0
        assert s["verdict"] == "pass"
        # 下发：1 次查询（无档案可删） + 1 次复查查询
        assert len(io.sent) >= 2

    def test_clear_archive_with_meters(self):
        # 查询返回 2 个档案 → 删除 1 批（收确认） → 复查 0
        io = FakeIO(responses=[
            _query_reply(total=2, addrs=["103827699719", "160100141223"]),  # 查询
            _confirm_reply(),        # 删除应答（确认）
            _query_reply(total=0),   # 复查
        ])
        result = run_recipe("clear_archive", {"confirm": True}, io=io)
        s = result["summary"]
        assert s["deleted"] == 2
        assert s["remain"] == 0
        assert s["cleared"] is True
        # 下发帧含 11H-F2 删除帧（无地址域）
        from sim_concentrator.frame_codec import decode_frame
        delete_frames = []
        for f in io.sent:
            if len(f) > 4 and f[0] == 0x68 and f[-1] == 0x16:
                d = decode_frame(f)
                if d["fields"]["AFN"]["raw"] == 0x11 and d["fields"]["FN"]["raw"] == 2:
                    delete_frames.append(f)
        assert delete_frames, "应下发 11H-F2 删除帧"
        # 删除帧应无地址域（信息域 module_id=0）
        d = decode_frame(delete_frames[0])
        assert d["fields"]["地址域A"]["value"] == "(无)"


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
class TestRecipesAPI:
    def _client(self):
        return TestClient(create_simcon_app(prefix=""))

    def test_list_endpoint(self):
        r = self._client().get("/recipes")
        assert r.status_code == 200
        ids = [x["id"] for x in r.json()["recipes"]]
        assert "clear_archive" in ids

    def test_detail_endpoint(self):
        r = self._client().get("/recipes/add_archive")
        assert r.status_code == 200
        assert r.json()["name"] == "添加从节点档案"

    def test_detail_missing_404(self):
        assert self._client().get("/recipes/nope").status_code == 404

    def test_run_add_archive_no_serial_409(self):
        # 无真实串口 → recipe 执行返回 409
        r = self._client().post("/recipes/add_archive/run",
                                json={"overrides": {"meters": ["013300000001"]}})
        assert r.status_code in (409, 500)  # 无串口环境：执行失败
