"""workbench.orchestration.models —— 编排层数据模型（FR-5.1 / FR-5.2 落地）。

Run 抽象：每次闭环验证 = 一个批次（run_id），含输入/执行/产出/归档。
统一验证报告：loghooks 摘要、sim_concentrator 结论、侦听台分钟报表
三者字段语义不统一，归一为一份 Report（FR-5.2）。

无 UI 依赖，可被 CLI / REST / AI agent 三端复用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# FR-5.1 Run 抽象
# ---------------------------------------------------------------------------


class FirmwareInfo(BaseModel):
    """固件信息（可追溯证据链之一，NFR-4）。"""

    version: str = ""
    commit: str = ""
    flash_file_sha256: str = ""


class RunInput(BaseModel):
    """Run 的输入：代码 commit / 固件 / 场景模板 id / 参数。"""

    scenario_id: str
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    # 可选：执行阶段开关（每步可跳过，支持"仅监控"/"仅激励"局部闭环）
    skip_flash: bool = False
    skip_monitor: bool = False
    skip_stimulus: bool = False
    skip_compare: bool = False
    skip_feedback: bool = False
    # 可选覆盖：日志目录 / 任务文件 / 规则集（默认取场景模板）
    log_dir: Optional[str] = None
    task_file: Optional[str] = None
    rules: Optional[List[str]] = None
    extras: Dict[str, Any] = Field(default_factory=dict)


RunStatus = Literal["pending", "running", "passed", "failed", "aborted"]


class Run(BaseModel):
    """一个验证批次（FR-5.1）。"""

    run_id: str
    scenario_id: str
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    status: RunStatus = "pending"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    steps: List["RunStep"] = Field(default_factory=list)
    report_path: Optional[str] = None


class RunStep(BaseModel):
    """Run 中的一个执行步骤。"""

    seq: int
    kind: Literal["flash", "monitor", "stimulus", "compare", "feedback"]
    detail: Optional[str] = None
    result: Literal["pass", "fail", "skipped", "running", "pending"] = "pending"


# ---------------------------------------------------------------------------
# FR-5.2 统一验证报告
# ---------------------------------------------------------------------------


class Assertion(BaseModel):
    """一条断言（期望 vs 实际）。"""

    id: str
    expected: str = ""
    actual: str = ""
    result: Literal["pass", "fail"] = "fail"


class FlowCompare(BaseModel):
    """FR-5.3 期望流程比对输出。"""

    steps: List[Dict[str, Any]] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)      # ❌ 缺失
    timeouts: List[str] = Field(default_factory=list)     # ⚠️ 超时
    out_of_order: List[str] = Field(default_factory=list) # 🔀 顺序错乱
    negated: List[str] = Field(default_factory=list)      # 🚫 负向触发
    verdict: Literal["pass", "fail"] = "pass"


class SourcesSummary(BaseModel):
    """三源归一摘要（loghooks / listener / sim_concentrator）。"""

    module_log: Dict[str, Any] = Field(default_factory=dict)
    listener: Dict[str, Any] = Field(default_factory=dict)
    sim_concentrator: Dict[str, Any] = Field(default_factory=dict)


class Report(BaseModel):
    """统一验证报告（FR-5.2 Schema 落地）。"""

    run_id: str
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    scenario: str = ""
    sources: SourcesSummary = Field(default_factory=SourcesSummary)
    assertions: List[Assertion] = Field(default_factory=list)
    flow_compare: FlowCompare = Field(default_factory=FlowCompare)
    feedback: List[Dict[str, Any]] = Field(default_factory=list)
    verdict: Literal["pass", "fail"] = "fail"
    artifacts: List[str] = Field(default_factory=list)
    ts: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    evidence_index: Dict[str, Any] = Field(default_factory=dict)
    evidence_frozen: bool = False
