"""case_runner 内核数据模型（REQS v3 §10.2 四层模型）。

四层：Capability（能力适配）→ Fact（只读事实）→ Block（原子块）→ Case/Suite（编排）。

本模块**零领域依赖**：只允许 stdlib / pydantic，不得 import ``parser_lib`` /
``sim_concentrator`` / ``sim_meter`` / ``loghooks`` / ``minute_assert`` / ``apps.*``
（由 ``libs/case_runner/test_layering.py`` 铁律 1 守门）。

约定：
- 所有 ``*Spec`` 都是 **JSON 数据的形状**（``data/case_*/**.json``），不是执行体；
  执行体按 id 注册在 ``core/registry.py``。
- 结论模型**不新增**：块结果、事实结果、用例结论一律落
  ``test_automation.models.AssertionResult``（``outcome ∈ pass|fail|inconclusive``）
  或本模块的三态常量，见 ``OUTCOME_*`` / ``FACT_*``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# 事实三态（REQS §10.5 FT-3）：unknown 永不折算为 yes / pass
# --------------------------------------------------------------------------- #

FACT_YES = "yes"
FACT_NO = "no"
FACT_UNKNOWN = "unknown"

#: 短别名（契约正文口径 `YES = "yes"` 等）
YES = FACT_YES
NO = FACT_NO
UNKNOWN = FACT_UNKNOWN

FactState = Literal["yes", "no", "unknown"]
FACT_STATES: frozenset[str] = frozenset({FACT_YES, FACT_NO, FACT_UNKNOWN})


# --------------------------------------------------------------------------- #
# 结论口径（与 test_automation.models.ASSERTION_OUTCOMES 完全一致，不新造模型）
# --------------------------------------------------------------------------- #

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_INCONCLUSIVE = "inconclusive"
OUTCOMES: frozenset[str] = frozenset(
    {OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_INCONCLUSIVE}
)

# --------------------------------------------------------------------------- #
# 块执行态（执行器内部状态；对外结论仍映射到上面三种 outcome）
# --------------------------------------------------------------------------- #

BLOCK_PASS = "pass"
BLOCK_FAIL = "fail"
BLOCK_SKIPPED = "skipped"
BLOCK_UNKNOWN = "unknown"
BLOCK_STATES: frozenset[str] = frozenset(
    {BLOCK_PASS, BLOCK_FAIL, BLOCK_SKIPPED, BLOCK_UNKNOWN}
)


# --------------------------------------------------------------------------- #
# 能力适配层契约（REQS §10.3）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Health:
    """能力就绪度（缺依赖时返回 ``ok=False`` + ``reason``，**不抛异常**）。"""

    ok: bool
    reason: str = ""


@runtime_checkable
class Capability(Protocol):
    """能力适配器契约：每个实现只对接一个领域库，彼此零 import。"""

    name: str

    def probe(self) -> Health:
        """探测依赖是否就绪；缺依赖返回 ``Health(ok=False, reason=...)``。"""
        ...


# --------------------------------------------------------------------------- #
# 用例步骤（UI 展示口径：用例名 + 步骤说明）
# --------------------------------------------------------------------------- #


class CaseStep(BaseModel):
    """用例步骤：``do`` 操作 / ``expect`` 预期，仅用于 UI 展示与文档。"""

    no: int = Field(ge=1)
    do: str = Field(min_length=1)
    expect: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Fact / Block / Case / Suite 声明
# --------------------------------------------------------------------------- #


class FactSpec(BaseModel):
    """事实声明：只读观测，不产生激励。``probe`` 是注册名（见 registry）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    probe: str = Field(min_length=1)  # 注册名，如 "archive.contains"
    params_schema: dict[str, Any] = Field(default_factory=dict)


class BlockSpec(BaseModel):
    """原子块声明：一次可独立判定的交互。

    ``action`` 必填：``{afn, fn, params}`` | ``{recipe}`` | ``{semantic}``。
    JSON 里写 ``assert``，模型里落在 ``assert_``（Python 关键字冲突）；
    ``model_dump(by_alias=True)`` 会还原成 ``assert``。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    needs: list[str] = Field(default_factory=list)  # fact ids
    provides: list[str] = Field(default_factory=list)  # fact ids
    action: dict[str, Any]  # afn/fn/params | recipe | semantic
    assert_: list[dict[str, Any]] = Field(default_factory=list, alias="assert")
    cleanup: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CaseSpec(BaseModel):
    """用例声明：**只做编排**，零帧逻辑（不得出现帧 hex / 参数计算）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    steps: list[CaseStep] = Field(min_length=1)  # UI 展示：用例名 + 步骤说明
    needs: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(min_length=1)  # block ids，顺序即执行序
    asserts: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SuiteSpec(BaseModel):
    """用例方案：用户勾选组合（``data/case_suites/*.json`` 持久化，文件可 diff）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    case_ids: list[str] = Field(min_length=1)
    stop_on_fail: bool = False
    notes: str = ""


__all__ = [
    # 事实三态
    "FACT_YES",
    "FACT_NO",
    "FACT_UNKNOWN",
    "YES",
    "NO",
    "UNKNOWN",
    "FACT_STATES",
    "FactState",
    # 结论口径
    "OUTCOME_PASS",
    "OUTCOME_FAIL",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOMES",
    # 块执行态
    "BLOCK_PASS",
    "BLOCK_FAIL",
    "BLOCK_SKIPPED",
    "BLOCK_UNKNOWN",
    "BLOCK_STATES",
    # 能力契约
    "Health",
    "Capability",
    # 数据模型
    "CaseStep",
    "FactSpec",
    "BlockSpec",
    "CaseSpec",
    "SuiteSpec",
]
