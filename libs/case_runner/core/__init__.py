"""case_runner 内核四件套（零领域依赖）。

- :mod:`case_runner.core.models`：四层模型的 JSON 声明形状 + 事实三态 + 能力契约
- :mod:`case_runner.core.registry`：块/事实执行体注册表（按 id 对应 JSON 声明）
- :mod:`case_runner.core.planner`：事实求值（带缓存）与块规划（unknown → 不可执行）
- :mod:`case_runner.core.expect`：观测 / 期望求值器（REQS-0044 §4.2：块只报观测，
  判据归步骤；纯函数，离线可测）
- :mod:`case_runner.core.executor`：顺序执行 + 聚合断言（落 ``AssertionResult``）
- :mod:`case_runner.core.report`：一句话报告（块 id → 业务动作、失败原因人话化、
  证据路径；REQS-0042 v11 D11-1/D11-2/D11-3），CLI / API / UI 共用唯一口径
"""

from __future__ import annotations

from .executor import (
    BlockRecord,
    BlockResult,
    CaseRunResult,
    RunContext,
    coerce_block_return,
    execute_case,
)
from .expect import (
    ENVIRONMENT_REASON_CODES,
    EXPECT_OPS,
    OBSERVATION_KEY,
    OBS_REASON_LABELS,
    Evaluation,
    Observation,
    coerce_observation,
    describe_expect,
    describe_expects,
    evaluate,
    normalize_expects,
)
from .models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_STATES,
    BLOCK_UNKNOWN,
    FACT_NO,
    FACT_STATES,
    FACT_UNKNOWN,
    FACT_YES,
    OUTCOME_BLOCKED,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
    OUTCOME_PRIORITY,
    OUTCOMES,
    BlockSpec,
    Capability,
    CaseSpec,
    CaseStep,
    FactSpec,
    Health,
    SuiteSpec,
)
from .planner import (
    FactsContext,
    FactValue,
    PlannedBlock,
    coerce_fact_value,
    describe_facts,
    plan,
    resolve,
)
from .registry import (
    BLOCK_REGISTRY,
    FACT_REGISTRY,
    Duplicate,
    NotFound,
    clear_registries,
    get_block,
    get_fact,
    has_block,
    has_fact,
    list_blocks,
    list_facts,
    register_block,
    register_fact,
)
from .report import (
    action_label,
    classify_failure,
    contains_block_id,
    evidence_paths,
    summarize_case,
    summarize_run,
)

__all__ = [
    # models
    "BLOCK_FAIL",
    "BLOCK_PASS",
    "BLOCK_SKIPPED",
    "BLOCK_STATES",
    "BLOCK_UNKNOWN",
    "FACT_NO",
    "FACT_STATES",
    "FACT_UNKNOWN",
    "FACT_YES",
    "OUTCOME_BLOCKED",
    "OUTCOME_FAIL",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOME_PASS",
    "OUTCOME_PRIORITY",
    "OUTCOMES",
    "BlockSpec",
    "Capability",
    "CaseSpec",
    "CaseStep",
    "FactSpec",
    "Health",
    "SuiteSpec",
    # registry
    "BLOCK_REGISTRY",
    "FACT_REGISTRY",
    "Duplicate",
    "NotFound",
    "clear_registries",
    "get_block",
    "get_fact",
    "has_block",
    "has_fact",
    "list_blocks",
    "list_facts",
    "register_block",
    "register_fact",
    # planner
    "FactsContext",
    "FactValue",
    "PlannedBlock",
    "coerce_fact_value",
    "describe_facts",
    "plan",
    "resolve",
    # executor
    "BlockRecord",
    "BlockResult",
    "CaseRunResult",
    "RunContext",
    "coerce_block_return",
    "execute_case",
    # expect（REQS-0044：观测 / 期望求值器）
    "ENVIRONMENT_REASON_CODES",
    "EXPECT_OPS",
    "OBSERVATION_KEY",
    "OBS_REASON_LABELS",
    "Evaluation",
    "Observation",
    "coerce_observation",
    "describe_expect",
    "describe_expects",
    "evaluate",
    "normalize_expects",
    # report（D11-1/D11-2：一句话报告，CLI / API / UI 共用）
    "action_label",
    "classify_failure",
    "contains_block_id",
    "evidence_paths",
    "summarize_case",
    "summarize_run",
]
