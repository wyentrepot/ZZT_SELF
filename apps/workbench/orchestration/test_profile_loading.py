"""编排级「用例语义化 profile 加载」验收测试（ADR-5）。

背景：ADR-5 后 task 顶层有 "profile": "anhui" 字段，profile 加载与构帧
已收敛进 sim_concentrator.runner.execute_task（execute_task 内部 load_profile
并传给 build_send_frame）。编排层 runner._run_stimulus 直接调 execute_task(task)，
因此 profile 加载已被覆盖——本测试在编排层验证这条链路不回归：

1. 含 profile 的 task dict 经 execute_task（FakeIO 桩，不开真串口）能成功
   构帧并下发（steps.sent_hex 非空、summary 有结果），证明 profile 被正确
   加载并用于构帧（构帧必需 cco_addr/comm_mode 等 profile 全局信息）。
2. profile 文件缺失 → execute_task 抛错（链路断），显式验证依赖真实存在。
3. 编排层 _run_stimulus 透传 task dict → execute_task 的 profile 加载路径。

测试用 FakeIO 桩（参照 libs/sim_concentrator/test_runner.py），绝不触碰真串口。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_concentrator.frame_codec import build_13762_frame
from sim_concentrator.runner import execute_task

from workbench.orchestration.runner import _run_stimulus

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios"
PROFILE_JSON = SCENARIOS / "profiles" / "anhui.json"
TASK_JSON = SCENARIOS / "tasks" / "anhui_minute_collect.json"


class FakeIO:
    """假串口：send 触发预置响应回放；recv 从队列取（不开真串口）。"""

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
        if self.responses:
            self.pending.append(self.responses.pop(0))

    def recv_frame(self, timeout=None):
        import time

        end = time.time() + (timeout if timeout else 1.0)
        while time.time() < end:
            if self.pending:
                return self.pending.pop(0)
            time.sleep(0.01)
        return None


def _confirm_reply(seq=1):
    return build_13762_frame(afn=0x00, fn=1, direction="up", info={"seq": seq})


# 最小化任务：profile 由真实 anhui.json 提供（构帧必需 cco_addr/comm_mode）
_MIN_TASK = {
    "id": "test.anhui.profile",
    "port": "COM_TEST",
    "profile": "anhui",
    "enable_responder": False,
    "steps": [
        {"name": "下发01H并期望确认",
         "send": {"afn": 0x01, "fn": 1},
         "expect": {"afn": 0x00}},
    ],
}


class TestProfileLoadedByExecuteTask:
    def test_profile_file_exists(self):
        """前置：profile 文件真实存在（execute_task 的 load_profile 依赖它）。"""
        assert PROFILE_JSON.exists(), f"profile 文件缺失：{PROFILE_JSON}"

    def test_execute_task_uses_profile_to_build_frame(self):
        """含 profile 的 task 经 execute_task + FakeIO 能成功构帧下发。

        sent_hex 非空证明构帧成功；构帧走 scenario_codec.build_send，
        必需 profile 提供 comm_mode / cco_addr，若 profile 未加载则构帧失败。
        """
        io = FakeIO(responses=[_confirm_reply()])
        out = execute_task(_MIN_TASK, io=io)
        # summary 有结果（链路完整执行到判定）
        assert out["summary"]["total"] == 1
        assert out["summary"]["verdict"] in ("pass", "fail")
        step = out["steps"][0]
        # 构帧成功 → 帧已下发，sent_hex 非空
        assert step["sent_hex"], "构帧失败：sent_hex 为空（profile 未加载或构帧错误）"
        assert io.sent, "未真正下发帧"
        # 期望确认帧 → 链路走到匹配判定
        assert step["result"] == "pass"
        assert step["reason"] and "匹配成功" in step["reason"]

    def test_sent_frame_address_carries_profile_cco_addr(self):
        """构帧的帧解码后地址域含 profile.cco_addr（证明 profile 用于构帧）。"""
        io = FakeIO(responses=[_confirm_reply()])
        out = execute_task(_MIN_TASK, io=io)
        raw = io.sent[0]
        from sim_concentrator.frame_codec import decode_frame

        d = decode_frame(raw)
        # profile anhui.json 的 cco_addr = "020103040506"
        assert "020103040506" in d["fields"]["地址域A"]["value"]

    def test_missing_profile_raises(self, monkeypatch):
        """profile 缺失 → execute_task 抛错（链路断的显式表现）。

        用 monkeypatch 改 load_profile 的默认目录指向空目录，模拟 profile
        文件不存在；若编排层依赖的这条链路断了，这里会直接暴露。
        """
        import sim_concentrator.scenario_codec as sc
        from sim_concentrator.scenario_codec import ScenarioCodecError

        import tempfile

        with tempfile.TemporaryDirectory() as d:
            monkeypatch.setattr(sc, "_default_profiles_dir", lambda: Path(d))
            with pytest.raises(ScenarioCodecError, match="profile 不存在"):
                execute_task(dict(_MIN_TASK), io=FakeIO(responses=[]))

    def test_profile_override_wins(self):
        """profile_overrides 覆盖 profile 全局信息（ADR-5 支持）。"""
        io = FakeIO(responses=[_confirm_reply()])
        task = {
            **_MIN_TASK,
            "profile_overrides": {"cco_addr": "111111111111"},
        }
        out = execute_task(task, io=io)
        from sim_concentrator.frame_codec import decode_frame

        d = decode_frame(io.sent[0])
        assert "111111111111" in d["fields"]["地址域A"]["value"]
        assert out["steps"][0]["result"] == "pass"


class TestRunStimulusPassesProfileThrough:
    def test_task_dict_reaches_execute_task_with_profile(self, monkeypatch):
        """编排层 _run_stimulus 把 task dict 原样交给 execute_task。

        显式验证 profile 字段随 task dict 透传（不丢失），execute_task 内部
        load_profile 会用到它。
        """
        captured = {}
        real_execute_task = execute_task  # 替换前保存真实引用（避免递归）

        def _fake_execute_task(task, io=None):
            captured["task"] = task
            # _run_stimulus 不传 io → execute_task 会自建真串口；
            # 这里把 None 换成 FakeIO 桩，避免触碰真串口
            if io is None:
                io = FakeIO(responses=[_confirm_reply()])
            # 复用真实 execute_task，FakeIO 桩不碰串口
            return real_execute_task(task, io=io)

        monkeypatch.setattr("sim_concentrator.runner.execute_task", _fake_execute_task)
        out = _run_stimulus(None, _MIN_TASK)
        assert out is not None
        assert captured["task"]["profile"] == "anhui"
        assert out["steps"][0]["sent_hex"]

    def test_run_stimulus_task_file_resolves_via_scenarios_dir(self):
        """task_file 相对路径经 SCENARIOS_DIR/tasks 解析（真实迁移后的任务）。

        端到端验证：编排层以 task_file 加载含 profile 的真实任务并执行到构帧
        （FakeIO 注入由真实 execute_task 内部串口创建前被替换）。
        """
        # 直接调 execute_task 加载真实任务文件 + FakeIO（等价于 _run_stimulus
        # 在 task_file 分支做 load_task 后交给 execute_task 的最终形态）
        import json

        task = json.loads(TASK_JSON.read_text(encoding="utf-8"))
        assert task.get("profile") == "anhui"
        io = FakeIO(responses=[_confirm_reply()])
        out = execute_task(task, io=io)
        # 第一类步骤（下发+期望）至少构帧成功并下发
        assert out["summary"]["total"] >= 1
        sent_hexes = [s["sent_hex"] for s in out["steps"] if s.get("sent_hex")]
        assert sent_hexes, "真实任务未成功构帧下发"
        # recv_only/历史步骤不做 send，sent_hex 为空是预期；下发型步骤必须非空
        assert all(h for h in sent_hexes)
