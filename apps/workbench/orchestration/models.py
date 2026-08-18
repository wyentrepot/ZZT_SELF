"""workbench.orchestration.models —— 编排层数据模型（FR-5.1 / FR-5.2 落地）。

Run 抽象：每次闭环验证 = 一个批次（run_id），含输入/执行/产出/归档。
统一验证报告：loghooks 摘要、sim_concentrator 结论、侦听台分钟报表
三者字段语义不统一，归一为一份 Report（FR-5.2）。

无 UI 依赖，可被 CLI / REST / AI agent 三端复用。

Run 状态复用规范领域模型（libs/test_automation.models.RunStatus），
消除 workbench 投影与规范枚举的契约漂移（13-设计契约偏差核查 D-01）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from test_automation.models import RunStatus

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


#: Run 状态复用规范领域枚举（单一事实来源，libs/test_automation.models.RunStatus）。
#: 兼容投影注意：规范枚举无 `pending`/`aborted`；Run 初态为 created，
#: 终态含 cancelled/passed/failed/inconclusive/error（含 13 号文档 D-01/D-02）。
RunStatus = RunStatus


class Run(BaseModel):
    """一个验证批次（FR-5.1）。

    状态复用规范枚举（RunStatus），初态 created（规范模型无 pending）。
    序列化输出枚举值字符串（pydantic v2 对 str Enum 输出 .value）。
    """

    run_id: str
    scenario_id: str
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    status: RunStatus = RunStatus.CREATED
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
    """一条断言（期望 vs 实际）。

    result 三态：pass/fail/inconclusive（13 号文档 D-02：必要证据缺失时 inconclusive）。
    """

    id: str
    expected: str = ""
    actual: str = ""
    result: Literal["pass", "fail", "inconclusive"] = "fail"


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


class ArtifactInfo(BaseModel):
    """Artifact 登记项（D-03：审计链结构化，逻辑 ID 供下载）。

    对齐规范 libs/test_automation.models.Artifact：run_id/type/name/sha256/path/created_at。
    id 为逻辑 Artifact ID（<run_id>-art-<N>），下载接口据此解析真实路径。
    """

    id: str
    run_id: str
    type: str
    name: str
    sha256: str
    path: str
    size: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class Report(BaseModel):
    """统一验证报告（FR-5.2 Schema 落地）。

    verdict 三态：pass/fail/inconclusive（D-02：必要来源缺失 → inconclusive）。
    artifacts 结构化 ArtifactInfo 列表（D-03：审计链，逻辑 ID 下载）。
    """

    run_id: str
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    scenario: str = ""
    sources: SourcesSummary = Field(default_factory=SourcesSummary)
    assertions: List[Assertion] = Field(default_factory=list)
    flow_compare: FlowCompare = Field(default_factory=FlowCompare)
    feedback: List[Dict[str, Any]] = Field(default_factory=list)
    verdict: Literal["pass", "fail", "inconclusive"] = "fail"
    artifacts: List[ArtifactInfo] = Field(default_factory=list)
    ts: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    evidence_index: Dict[str, Any] = Field(default_factory=dict)
    evidence_detail: Dict[str, Any] = Field(default_factory=dict)
    evidence_frozen: bool = False
