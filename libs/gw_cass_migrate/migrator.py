"""gw_cass 用例迁移：读 GW-CASS TestSce.json → 生成 CasePackage 集合（任务3）。

设计：
- 97 个用例（TEST_CASE_CONTENT）每个转成一个 CasePackage。
- case_id = gw_cass_<序号>
- name = 标题；description = 原文（操作→预期，保留追溯）
- parameters = 结构化：source="gw_cass"、index、afn_group、steps（action+expected）、needs_hardware
- assertions = 从预期结果提取的机器断言（present/contains/equals），source="gw_cass" kind="frame"
- 契约来源：docs/03 骨架设计 §3（CasePackage）、§4（用例）

输出：CasePackage 集合 JSON 文件（可被 test_automation 加载）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from test_automation.models import AssertionSpec, CasePackage, DeviceSpec

#: 预期结果 → 机器断言的提取规则（正则 → AssertionSpec 参数）
_ASSERTION_PATTERNS: list[tuple[re.Pattern[str], dict[str, Any]]] = [
    # 确认帧应答
    (re.compile(r"(?:应答|正确应答|返回).*(?:确认|00H-F1|00H[_-]?F1)"), {
        "kind": "present", "field": "afn_fn", "expected": "00H-F1", "message": "CCO 应答确认帧",
    }),
    # 否认帧应答
    (re.compile(r"(?:应答|返回).*(?:否认|00H-F2|00H[_-]?F2)"), {
        "kind": "present", "field": "afn_fn", "expected": "00H-F2", "message": "CCO 应答否认帧",
    }),
    # 主动上报（AFN=06H 系列）
    (re.compile(r"(?:上报|主动上报).*(?:06H|AFN=06H)"), {
        "kind": "present", "field": "afn", "expected": "06H", "message": "CCO 主动上报 06H 帧",
    }),
    # 查询返回指定 AFN
    (re.compile(r"返回\s*(AFN=)?([0-9A-Fa-f]{2}H)[-_]?F([0-9]+)"), {
        "kind": "present", "field": "afn_fn", "expected": None, "message": "CCO 返回指定 AFN/FN",
    }),
    # 数量为 0
    (re.compile(r"(?:数量|只数|个数)[=＝]?\s*0\b"), {
        "kind": "equals", "field": "count", "expected": 0, "message": "数量应为 0",
    }),
    # 从节点数量一致
    (re.compile(r"(?:数量|只数).*(?:一致|相等)"), {
        "kind": "present", "field": "count", "message": "从节点数量一致",
    }),
]


def _extract_assertions(step: dict[str, Any], index: int) -> list[AssertionSpec]:
    """从单个步骤的预期结果提取机器断言；无法提取时返回空列表。"""
    expected = step.get("预期结果", "") or ""
    assertions: list[AssertionSpec] = []
    for pattern, base in _ASSERTION_PATTERNS:
        m = pattern.search(expected)
        if not m:
            continue
        kwargs = dict(base)
        # 动态提取 AFN/FN（如 "返回 03H-F1"）
        if kwargs.get("expected") is None and m.lastindex and m.lastindex >= 2:
            kwargs["expected"] = f"{m.group(2)}-F{m.group(3)}"
        assertions.append(
            AssertionSpec(
                id=f"a{index}_{len(assertions) + 1}",
                source="gw_cass",
                kind_filter="frame",
                **kwargs,
            )
        )
    return assertions


def _detect_afn_group(title: str) -> str:
    """从标题提取 AFN 分组（如 'AFN=00H-F1确认' → '00H'）。"""
    m = re.search(r"AFN\s*[=＝]?\s*([0-9A-Fa-f]{2}H)", title)
    return m.group(1).upper() if m else "场景"


def _needs_hardware(title: str, steps: list[dict[str, Any]]) -> bool:
    """判断用例是否依赖真实硬件（继电器/真实电表/多 CCO/噪声）。"""
    text = title + " " + " ".join(s.get("操作", "") + s.get("预期结果", "") for s in steps)
    markers = ("继电器", "真实电表", "真实表", "多厂家", "混合组网", "混合抄表",
               "噪声", "开表盖", "上电", "插在底座", "复位重启", "实机")
    return any(marker in text for marker in markers)


def migrate_case(item: dict[str, Any], index: int) -> CasePackage:
    """将单个 TEST_CASE_CONTENT 用例转成 CasePackage。"""
    title = item.get("标题", f"用例{index}")
    steps_raw = item.get("步骤", [])
    steps = [{"action": s.get("操作", ""), "expected": s.get("预期结果", "")} for s in steps_raw]
    assertions: list[AssertionSpec] = []
    for i, step in enumerate(steps_raw, start=1):
        assertions.extend(_extract_assertions(step, i))
    return CasePackage(
        case_id=f"gw_cass_{index:02d}",
        version="1.0.0",
        name=title,
        description=f"GW-CASS 用例迁移 #{index}：{title}",
        timeout_s=30.0,
        parameters={
            "source": "gw_cass",
            "index": index,
            "afn_group": _detect_afn_group(title),
            "steps": steps,
            "needs_hardware": _needs_hardware(title, steps_raw),
            "origin": "TestSce.json TEST_CASE_CONTENT",
        },
        device=DeviceSpec(resource_type="serial_port", resource_id="COM24", shared=False),
        assertions=assertions,
    )


def load_gw_cass_cases(path: Path) -> list[CasePackage]:
    """读 GW-CASS TestSce.json，返回全部迁移后的 CasePackage。"""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    content = data.get("TEST_CASE_CONTENT", [])
    return [migrate_case(item, i) for i, item in enumerate(content, start=1)]


def dump_cases_json(cases: list[CasePackage], output: Path) -> None:
    """将 CasePackage 集合序列化为 JSON 文件（数组）。"""
    output.write_text(
        json.dumps([c.model_dump() for c in cases], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
