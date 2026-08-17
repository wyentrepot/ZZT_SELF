"""workbench.orchestration.scenarios —— 场景模板库加载/校验（JSON 目录）。

场景 = "期望流程" + "激励任务" + "监控规则集" 三者绑定（FR-5.3 期望侧声明）。

模板文件：apps/workbench/scenarios/*.json（随包分发），运行期也可加载用户
目录（--scenarios-dir）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


def load_scenarios(scenarios_dir: Optional[Path] = None) -> List[dict]:
    """加载目录下所有场景模板 JSON。"""
    root = scenarios_dir or SCENARIOS_DIR
    if not root.exists():
        return []
    out: List[dict] = []
    for f in sorted(root.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                data["_file"] = str(f)
                out.append(data)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_scenario(scenario_id: str, scenarios_dir: Optional[Path] = None) -> Optional[dict]:
    """按 id 加载场景模板。"""
    for s in load_scenarios(scenarios_dir):
        if s.get("id") == scenario_id:
            return s
    return None


def validate_scenario(s: dict) -> List[str]:
    """校验场景模板结构，返回错误清单（空 = 合法）。"""
    errors: List[str] = []
    if not s.get("id"):
        errors.append("缺少 id")
    if not isinstance(s.get("expected_flow"), list) or not s["expected_flow"]:
        errors.append("expected_flow 必须为非空列表")
    for i, step in enumerate(s.get("expected_flow", [])):
        if not step.get("event_type"):
            errors.append(f"expected_flow[{i}] 缺少 event_type")
        if step.get("negate") and not step.get("event_type"):
            errors.append(f"expected_flow[{i}] negate 步必须声明 event_type")
    return errors
