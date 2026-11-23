"""case_runner 内核四件套（零领域依赖）。

- :mod:`case_runner.core.models`：四层模型的 JSON 声明形状 + 事实三态 + 能力契约
- :mod:`case_runner.core.registry`：块/事实执行体注册表（按 id 对应 JSON 声明）
- :mod:`case_runner.core.planner`：事实求值（带缓存）与块规划（unknown → 不可执行）
- :mod:`case_runner.core.executor`：顺序执行 + 聚合断言（落 ``AssertionResult``）
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
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
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
    "OUTCOME_FAIL",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOME_PASS",
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
]
