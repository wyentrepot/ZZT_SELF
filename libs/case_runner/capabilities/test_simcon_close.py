"""``SimconCapability.close()`` × 自愈层的端到端（**离线**）用例 —— REQS-0042 §3.4 / SH-1。

为什么单独一个文件：``blocks/test_self_heal.py`` 那边用假能力（``FakeSimcon``）守
自愈**逻辑**，本文件守「真实 ``SimconCapability`` 实例 + 自愈层」这条对接缝：

    409（占用）→ 自愈层经 ``capability_closer`` 找到 ``close()`` → POST /api/simcon/close
    → 重试一次 → 成功，且 ``self_healed`` 留痕 ``result="healed"``、``detail`` 里能
    追溯到「抢占了谁」。

全部用注入的假 transport 回放，**零真实 HTTP、零真实等待、不碰台架**。
"""

from __future__ import annotations

import urllib.parse

from case_runner.blocks import common
from case_runner.capabilities.simcon import SimconCapability

#: 假 transport 用的 base_url（端口只是假地址，不代表任何真实台架）
FAKE_BASE = "http://127.0.0.1:19301"

VERIFY_PATH = "/api/ai/v2/verification-runs"
CLOSE_PATH = "/api/simcon/close"

#: 真机 409 的原始文案口径（= 谁占着，见 apps/workbench/ai_operations.py）
CONFLICT_409 = {"detail": "已有模拟集中器任务在运行：actor=alice，动作=verify，已运行 3.5s"}
#: close 应答（后端原样透传的那一份：抢占对象从这里挖）
CLOSE_PAYLOAD = {"open": False, "actor": "alice", "session_id": "sc-7",
                 "port": "/dev/ttyS3", "detail": "已关闭模拟集中器会话"}


class FakeTransport:
    """按 (method, path) 回放的假 transport；值可为 ``(status, payload)`` 或 callable。"""

    def __init__(self, routes=None, default=(404, {"detail": "fake: 未配置该路由"})):
        self.routes = dict(routes or {})
        self.default = default
        self.calls = []

    def __call__(self, method, url, body, timeout):
        parsed = urllib.parse.urlparse(url)
        key = (method.upper(), parsed.path)
        self.calls.append({"method": method.upper(), "path": parsed.path, "body": body})
        response = self.routes.get(key, self.default)
        if callable(response):
            response = response(method, url, body)
        return response

    def count(self, method, path):
        return sum(1 for call in self.calls
                   if call["method"] == method.upper() and call["path"] == path)

    def paths(self):
        return [call["path"] for call in self.calls]


def _conflict_then_ok(conflicts: int = 1):
    """前 ``conflicts`` 次 verify 回 409，之后回 202（job 已受理）。"""
    state = {"verify_calls": 0}

    def respond(method, url, body):
        state["verify_calls"] += 1
        if state["verify_calls"] <= conflicts:
            return 409, dict(CONFLICT_409)
        return 202, {"job_id": "job-1", "job_state": "queued"}

    return respond, state


def _cap(routes, **kwargs):
    transport = FakeTransport(routes)
    kwargs.setdefault("retry_backoff", 0.0)
    kwargs.setdefault("poll_interval", 0.0)
    return SimconCapability(FAKE_BASE, transport=transport, **kwargs), transport


# --------------------------------------------------------------------------- #
# 对接缝 1：自愈层怎么找到 close()（capability_closer + SIMCON_CLOSER_NAMES）
# --------------------------------------------------------------------------- #

def test_self_heal_layer_resolves_real_simcon_close():
    cap, _ = _cap({})
    closer = common.capability_closer(cap, common.SIMCON_CLOSER_NAMES)
    assert closer is not None, "自愈层找不到 SimconCapability.close()"
    assert common.SIMCON_CLOSER_NAMES == ("close",)
    # 零参可调（with_self_heal 就是零参调它），且返回 {"ok": ...} 形态
    result = closer()
    assert result["ok"] is False and result["error"] == "http_404"   # 假路由未配 = 404


# --------------------------------------------------------------------------- #
# SH-1：真 409 → close 抢占 → 重试一次 → 成功（留痕 healed + 抢占对象可追溯）
# --------------------------------------------------------------------------- #

def test_real_simcon_409_is_preempted_by_close_then_retried_once():
    respond, state = _conflict_then_ok(conflicts=1)
    cap, transport = _cap({("POST", VERIFY_PATH): respond,
                           ("POST", CLOSE_PATH): (200, dict(CLOSE_PAYLOAD))})

    attempt = lambda: cap.submit_verify([{"name": "f1", "afn": 2, "fn": 1}])
    result, healed = common.with_self_heal(
        attempt, action="simcon.close", target="simcon:%s" % cap.mapping_id, cap=cap)

    # 1) 重试成功（原来是 409，close 一次后第二次提交就过了）
    assert result["ok"] is True and result["job_id"] == "job-1", result
    assert state["verify_calls"] == 2, "重试次数必须是 1 次（SH-1）"
    assert transport.count("POST", CLOSE_PATH) == 1, "close 只能发一次"

    # 2) 动作顺序：verify(409) → close → verify(202)，中间没有别的调用
    assert transport.paths() == [VERIFY_PATH, CLOSE_PATH, VERIFY_PATH]

    # 3) 留痕：一条 healed，detail 里有原始 409 + 抢占对象（actor/session_id）
    assert len(healed) == 1, healed
    entry = healed[0]
    assert entry["result"] == common.SELF_HEAL_HEALED
    assert entry["action"] == "simcon.close" and entry["target"] == "simcon:simcon"
    assert "http_409" in entry["detail"]
    assert "actor=alice" in entry["detail"]
    assert "session_id=sc-7" in entry["detail"]
    assert "重试 1 次成功" in entry["detail"]


def test_real_simcon_close_failure_still_healed_when_retry_passes():
    """close 自己失败（如闸门 409）也照常重试一次：留痕仍是 healed，但写明 close 未成功。"""
    respond, _ = _conflict_then_ok(conflicts=1)
    cap, transport = _cap({
        ("POST", VERIFY_PATH): respond,
        ("POST", CLOSE_PATH): (409, {"detail": "已有模拟集中器任务在运行：actor=bob"}),
    })

    result, healed = common.with_self_heal(
        lambda: cap.submit_verify([{"name": "f1", "afn": 2, "fn": 1}]),
        action="simcon.close", target="simcon:simcon", cap=cap)

    assert result["ok"] is True
    entry = healed[0]
    assert entry["result"] == common.SELF_HEAL_HEALED
    assert "close 未成功（http_409）" in entry["detail"]
    assert "actor=bob" in entry["detail"]       # 服务端原话要留痕，不许吞
