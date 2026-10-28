"""失败异步任务的证据链不能再是空数组（真机缺陷回归）。

真机实测：失败的烧录 / 验证任务调
``GET /api/ai/v2/jobs/{job_id}/evidence?level=L1`` 返回
``{"job_id": "...", "level": "L1", "summary": "verification_run error",
"items": [], "refs": []}``。真正的原因在 ``result.result.steps[].reason``
或操作级 ``error`` 上，证据链里完全看不到 —— 事后审计 / 复盘没有可引用证据，
只能翻 ``operations.json`` 原始文件。

这里用 stub store（不碰串口、不碰烧板）直接断言 AICapabilityService 的证据投影。
"""
import json

import pytest

from workbench.ai_capability_service import AICapabilityService, EvidenceRefForbidden
from workbench.ai_contracts import EvidenceLevel


class _StubStore:
    def __init__(self, op):
        self._op = op

    def get(self, op_id):
        return dict(self._op)


class _StubControl:
    def __init__(self, op):
        self.store = _StubStore(op)


def _jobs(svc, op, level):
    return svc.read_job_evidence("job-x", level=level)


def _service(op):
    return AICapabilityService(_StubControl(op))


def _verification_op(steps, *, state="succeeded", error=None):
    """父 job 形态：底层结果套在 result.result 下（_compact_operation）。"""
    return {
        "operation_id": "op-x",
        "kind": "verification_run",
        "state": state,
        "error": error,
        "result": {
            "underlying_operation_id": "op-u",
            "underlying_state": "error" if state == "error" else "succeeded",
            "result": {
                "task_id": "verify.task",
                "steps": steps,
                "summary": {"total": len(steps), "pass": 1, "fail": len(steps) - 1,
                            "verdict": "fail"},
            },
        },
    }


def _flash_op(flash, *, state="succeeded", error=None, nested=True):
    inner = {"session_id": "ms-cco", "flash": flash}
    return {
        "operation_id": "op-x",
        "kind": "flash_job",
        "state": state,
        "error": error,
        "result": {
            "underlying_operation_id": "op-u",
            "underlying_state": "succeeded" if state == "succeeded" else "error",
            **({"result": inner} if nested else {"flash": flash}),
        },
    }


# ---------------------------------------------------------------------------
# 1) 验证任务：失败步骤的 reason 必须出现在 L1
# ---------------------------------------------------------------------------

def _two_steps_fail_second():
    # 故意乱序传入：证据项必须按 step.index 升序，不能靠入参顺序/字典顺序。
    return [
        {"index": 1, "name": "查询从节点档案", "result": "fail", "sent_hex": "",
         "matched": False, "reason": "构帧失败: ValueError('缺少参数 nid')"},
        {"index": 0, "name": "下发 06H 上报", "result": "pass",
         "sent_hex": "6815000000000000680B", "matched": True, "reason": "匹配成功"},
    ]


def test_failed_verification_l1_carries_failing_step_reason():
    svc = _service(_verification_op(_two_steps_fail_second()))
    l1 = _jobs(svc, None, EvidenceLevel.L1)

    assert l1.items, "失败验证任务的 L1 不能是空数组"
    reasons = [str(item.data.get("reason")) for item in l1.items]
    assert any("构帧失败" in reason for reason in reasons), l1.model_dump()
    failed = next(item for item in l1.items if "构帧失败" in str(item.data.get("reason")))
    assert failed.data["result"] == "fail"
    assert failed.data["name"] == "查询从节点档案"


def test_verification_steps_are_index_ascending_and_l2_has_sent_hex():
    svc = _service(_verification_op(_two_steps_fail_second()))
    l2 = _jobs(svc, None, EvidenceLevel.L2)

    indexes = [item.data["index"] for item in l2.items]
    assert indexes == [0, 1], indexes  # 入参是乱序的，输出必须按 index 升序
    assert l2.items[0].data["sent_hex"] == "6815000000000000680B"
    assert l2.items[1].data["reason"].startswith("构帧失败")


# ---------------------------------------------------------------------------
# 2) 操作级失败：没有 steps/flash 明细时也必须有一条原因证据
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,source", [
    ("verification_run", "simcon"),
    ("flash_job", "module_log"),
    ("module_action", "module_log"),
    ("investigation", "module_log"),
])
def test_operation_level_failure_never_returns_empty_items(kind, source):
    op = {
        "operation_id": "op-x",
        "kind": kind,
        "state": "error",
        "error": "烧录失败：Bootloader prompt was not detected",
        "result": {"underlying_operation_id": "op-u", "underlying_state": "error"},
    }
    l1 = _jobs(_service(op), op, EvidenceLevel.L1)

    assert len(l1.items) == 1, l1.model_dump()
    item = l1.items[0]
    assert item.source.value == source
    assert "Bootloader prompt was not detected" in str(item.data["reason"])
    assert item.data["state"] == "error"


def test_operation_level_failure_without_error_text_says_service_gave_no_reason():
    op = {
        "operation_id": "op-x",
        "kind": "verification_run",
        "state": "timed_out",
        "result": {"underlying_operation_id": "op-u"},
    }
    l1 = _jobs(_service(op), op, EvidenceLevel.L1)

    assert len(l1.items) == 1
    assert l1.items[0].data["reason"] == "服务未给出原因"


def test_nested_underlying_error_is_used_when_parent_error_is_absent():
    """验证看门狗的超时原因落在 result.result.error 上（父 job error 为空）。"""
    op = {
        "operation_id": "op-x",
        "kind": "verification_run",
        "state": "error",
        "result": {"underlying_operation_id": "op-u",
                   "underlying_state": "error",
                   "result": {"error": "验证总超时（120s），已放弃等待"}},
    }
    l1 = _jobs(_service(op), op, EvidenceLevel.L1)

    assert len(l1.items) == 1
    assert "验证总超时" in str(l1.items[0].data["reason"])


# ---------------------------------------------------------------------------
# 3) 烧录任务：进度/成败从 result.result.flash 取
# ---------------------------------------------------------------------------

def test_successful_flash_l1_shows_packet_total_and_phase():
    flash = {"packet": 368, "total": 368, "phase": "done",
             "message": "success", "error": ""}
    l1 = _jobs(_service(_flash_op(flash)), None, EvidenceLevel.L1)

    assert len(l1.items) == 1, l1.model_dump()
    data = l1.items[0].data
    assert data["packet"] == 368 and data["total"] == 368
    assert data["phase"] == "done"
    assert "368/368" in json.dumps(l1.model_dump(), ensure_ascii=False)


def test_failed_flash_carries_flash_error_and_message():
    flash = {"packet": 12, "total": 368, "phase": "error",
             "message": "bootloader timeout", "error": "Bootloader prompt was not detected"}
    op = _flash_op(flash, state="error", error="烧录失败：Bootloader prompt was not detected")
    l1 = _jobs(_service(op), op, EvidenceLevel.L1)

    assert len(l1.items) == 1
    data = l1.items[0].data
    assert data["phase"] == "error"
    assert "Bootloader prompt was not detected" in str(data["error"])
    assert "bootloader timeout" in str(data["message"])


def test_flash_detail_is_found_on_the_underlying_sync_shape():
    """底层 module_flash 的同步结果把 flash 放在 result.flash（无 result.result）。"""
    flash = {"packet": 368, "total": 368, "phase": "done", "message": "success", "error": ""}
    op = _flash_op(flash, nested=False)
    op["kind"] = "module_flash"

    l1 = _jobs(_service(op), op, EvidenceLevel.L1)

    assert len(l1.items) == 1
    assert l1.items[0].data["packet"] == 368


# ---------------------------------------------------------------------------
# 4) 回归：investigation / module_action 的 observations 路径与 L3 不变
# ---------------------------------------------------------------------------

def _investigation_op(state="succeeded", *, observations, error=None):
    return {
        "operation_id": "op-x",
        "kind": "investigation",
        "state": state,
        "error": error,
        "result": {"observations": observations, "source_health": {}},
    }


def test_investigation_observation_path_is_unchanged():
    observations = [
        {"ordinal": 0, "source": "module_log", "state": "succeeded",
         "operation_id": "op-o", "result": {"condition_met": True, "reason": "hit"}},
        {"ordinal": 1, "source": "simcon", "state": "succeeded",
         "operation_id": None, "result": {"frames": [{"a": 1}]}},
    ]
    svc = _service(_investigation_op(observations=observations))

    l1 = _jobs(svc, None, EvidenceLevel.L1)
    l2 = _jobs(svc, None, EvidenceLevel.L2)

    assert [item.source.value for item in l1.items] == ["module_log", "simcon"]
    assert l1.items[0].data["reason"] == "hit"
    # L2 仍是原始 observations 投影（非 module_log/listener 直接透传 result）
    assert l2.items[1].data["frames"] == [{"a": 1}]


def test_observation_error_item_is_not_duplicated_by_failure_fallback():
    observations = [{"ordinal": 0, "source": "simcon", "state": "error",
                     "operation_id": None, "error": "模拟集中器服务不可用"}]
    op = _investigation_op("error", observations=observations,
                           error="模拟集中器服务不可用")

    l2 = _jobs(_service(op), op, EvidenceLevel.L2)

    assert len(l2.items) == 1
    assert l2.items[0].data["reason"] == "模拟集中器服务不可用"


def test_l3_ref_validation_is_untouched_by_the_new_mapping():
    observations = [{"ordinal": 0, "source": "listener", "state": "succeeded",
                     "operation_id": None,
                     "result": {"index": {"index_id": "idx-a"}, "matches": []}}]
    svc = _service(_investigation_op(observations=observations))

    assert _jobs(svc, None, EvidenceLevel.L3).items == []
    with pytest.raises(EvidenceRefForbidden):
        svc.read_job_evidence("job-x", level=EvidenceLevel.L3,
                              refs=["listener:idx-other:1"])
