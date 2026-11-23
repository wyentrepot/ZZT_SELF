"""case_runner 内核（需求 0041）：四层模型 + 事实 + 原子块编排。

目录分层（REQS v3 §10.4，单向依赖，由 ``test_layering.py`` 用 AST 守门）：

```
入口 tools/scripts/case_run.py / apps/workbench/cases_api.py     ① 参数/IO，零测试逻辑
        ↓
case_runner/core/{models,registry,planner,executor}.py            ② 内核（零领域依赖）
        ↓
case_runner/capabilities/*.py                                    ③ 能力适配（彼此零 import）
        ↓
parser_lib / sim_concentrator / sim_meter / loghooks / minute_assert   ④ 领域层
```

本包 ``core`` 只允许依赖 stdlib / pydantic / ``test_automation.models`` 的结论契约。

导出采用 **PEP 562 延迟导出**（``__getattr__``）：``import case_runner`` 不会连带
加载 ``case_runner.core``（因而也不加载 pydantic / test_automation）。这样能力层与
入口层在做依赖桩（stub ``sys.modules['case_runner.core.models']``）或分模块导入时，
不会因为包级急加载而互相拖累。真正的取用请直接写
``from case_runner.core.models import BlockSpec`` 或 ``from case_runner import BlockSpec``
（后者同样可用）。
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    # 事实三态 / 结论口径 / 块执行态
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
    # models
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

_LAZY = frozenset(__all__)


def _core():
    # 用 import_module 而不是 ``from . import core``：后者会被本 __getattr__ 再次拦截成递归
    return importlib.import_module(f"{__name__}.core")


def __getattr__(name: str) -> Any:
    if name == "core":
        return _core()
    if name in _LAZY:
        return getattr(_core(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(_LAZY | {"core"})
