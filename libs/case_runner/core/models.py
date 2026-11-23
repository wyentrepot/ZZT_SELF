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
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class StepSpec(BaseModel):
    """用例的**可执行步骤**（REQS-0042 D3-1「步骤实例化」）。

    与 :class:`CaseStep` 的区别（两者并存、互不替代）：

    - ``CaseStep``（JSON 里的 ``steps[]``）是**人读说明**：``{no, do, expect}``，给 UI / 文档看；
    - ``StepSpec``（JSON 里的 ``plan[]``）是**执行单元**：``{id, block, params, on_fail}``，
      给执行器看。一个块可以在一条用例里被实例化多次，每次带自己的 ``params`` ——
      这就是「一次下发 3 块不同档案」的实现方式（``blocks[]`` 只是块 id 列表，
      参数只能全 run 一份，做不到这件事）。

    ``on_fail`` 语义（与用例级/方案级 ``stop_on_fail`` 的关系）
    --------------------------------------------------------
    - 本步**显式**声明 ``on_fail`` → 以它为准（``"stop"`` 下游记 skipped /
      ``"continue"`` 下游照跑）；
    - 本步**没**显式声明 → 继承 run 级 ``stop_on_fail``（``case_run run --stop-on-fail``
      或方案的 ``stop_on_fail``）。这是老用例行为逐字不变的前提：
      ``blocks[]`` 自动展开出来的步骤不带 ``on_fail``，永远跟着 ``stop_on_fail`` 走。
    - 显式声明优先于 run 级默认（``stop_on_fail`` 只是「默认值」，不是「强制值」）。

    ``params`` 里不允许出现执行器的保留键（``_assert`` / ``_cleanup``），否则会被
    执行器覆盖成块声明值，静默失效 —— 加载期直接报错。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)  # 用例内唯一（同块多次实例化靠它区分）
    block: str = Field(min_length=1)  # 块 id
    params: dict[str, Any] = Field(default_factory=dict)  # 本步的块参数
    on_fail: Literal["stop", "continue"] = "stop"
    note: str = ""

    @model_validator(mode="after")
    def _reject_reserved_params(self) -> "StepSpec":
        reserved = [key for key in self.params if str(key).startswith("_")]
        if reserved:
            raise ValueError(
                f"步骤 {self.id!r} 的 params 里出现保留键 {reserved}："
                "以下划线开头的键由执行器注入（_assert / _cleanup），用例里写了也会被覆盖"
            )
        return self

    @property
    def declares_on_fail(self) -> bool:
        """是否**显式**写了 ``on_fail``（没写 = 继承 run/方案级 ``stop_on_fail``）。

        用 pydantic 的 ``model_fields_set`` 判断「原文里有没有这个键」：
        ``blocks[]`` 自动展开出来的步骤不是凭空造一个 ``on_fail="stop"``，
        否则 ``stop_on_fail=False`` 的老用例会从「后续照跑」变成「首败即停」。
        """

        return "on_fail" in self.model_fields_set

    def resolved_on_fail(self, run_default: bool = True) -> str:
        """本步实际生效的失败策略：显式声明优先，否则取 run 级默认。"""

        if self.declares_on_fail:
            return self.on_fail
        return "stop" if run_default else "continue"


# --------------------------------------------------------------------------- #
# Fact / Block / Case / Suite 声明
# --------------------------------------------------------------------------- #


class FactSpec(BaseModel):
    """事实声明：只读观测，不产生激励。``probe`` 是注册名（见 registry）。

    ``parameterized``（REQS-0042）：该事实的结论**随入参变化**（``archive.contains``
    按表地址求值、``nwk.onnet_count_min`` 按 ``min`` 求值）。这是「参数化事实」的
    **权威口径**——旧的「有没有 ``required: true`` 参数」只是兼容写法：0042 D2-2 之后
    参数都有默认值（``"default": null`` 取 ``case_defaults.json``），必需参数为空的
    事实仍可能是参数化的，靠 required 反推会让结构门禁空转。默认 ``False``
    （向后兼容：老声明不加这个字段照旧按 required 判定）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    probe: str = Field(min_length=1)  # 注册名，如 "archive.contains"
    params_schema: dict[str, Any] = Field(default_factory=dict)
    parameterized: bool = False


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
    """用例声明：**只做编排**，零帧逻辑（不得出现帧 hex / 参数计算）。

    两种编排写法（REQS-0042 D3-1）：

    - 老写法 ``blocks: ["块 id", ...]``：顺序即执行序，参数全 run 一份；
    - 新写法 ``plan: [{id, block, params, on_fail}, ...]``：**步骤实例化**，
      同一个块可以出现多次，每次带自己的参数与失败策略。

    规则：``blocks`` 与 ``plan`` **至少给一个**；两者都给 → **以 ``plan`` 为准**
    （``blocks`` 原样保留，供 ``case_run check`` 给一条提示，不是一个错误）。
    ``steps[]``（人读说明）两种写法都照旧必填，语义不变。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    steps: list[CaseStep] = Field(min_length=1)  # UI 展示：用例名 + 步骤说明
    needs: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)  # 块 ids，顺序即执行序（D3-1 起可选）
    plan: list[StepSpec] = Field(default_factory=list)  # 步骤实例化（D3-1）
    asserts: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _require_orchestration(cls, data: Any) -> Any:
        """编排自检（**先于字段校验**，一次把缺的东西全点名）。

        为什么放在 ``mode="before"``：字段级校验（``steps`` 至少 1 条）一旦报错，
        ``mode="after"`` 的校验器就不会再跑，于是「steps 空 + blocks 空」这种同时
        缺两样的声明只会报其中一条，作者要试两次才知道还缺什么。
        """

        if not isinstance(data, Mapping):
            return data
        blocks = data.get("blocks") or []
        plan = data.get("plan") or []
        steps = data.get("steps") or []
        missing: list[str] = []
        if not blocks and not plan:
            missing.append("blocks 或 plan（两种编排写法至少给一个，可同块多次的是 plan）")
        if not steps:
            missing.append("steps（人读步骤说明，至少 1 条）")
        if missing:
            raise ValueError(f"用例 {data.get('id')!r} 缺少：{'；'.join(missing)}")
        return data

    @model_validator(mode="after")
    def _validate_orchestration(self) -> "CaseSpec":
        """编排自检：``plan`` 内 step id 唯一（错误信息点名用例与步骤）。"""

        seen: set[str] = set()
        for step in self.plan:
            if step.id in seen:
                raise ValueError(
                    f"用例 {self.id!r} 的 plan 里 step id 重复：{step.id!r}"
                    "（step id 在用例内必须唯一，同块多次实例化请给不同的 id）"
                )
            seen.add(step.id)
        return self

    @property
    def uses_plan(self) -> bool:
        """是否用 ``plan`` 编排（``False`` = 老写法：只有 ``blocks[]``）。"""

        return bool(self.plan)

    def effective_steps(self) -> list[StepSpec]:
        """实际执行步骤序列（执行器/规划器/**唯一**展开入口）。

        - 有 ``plan`` → 用 ``plan``（两者都给时以它为准）；
        - 只有 ``blocks`` → 自动展开成一「块 = 一步」，step id 就是块 id
          （老用例因此逐字兼容：结果里的 ``step_id`` 回填成块 id，
          断言 id 仍是 ``用例.块``，失败策略仍跟 ``stop_on_fail``）；
        - ``blocks`` 里重复出现同一个块 → 第 2 次起 step id 后缀 ``#2``/``#3``（用例内唯一）。
        """

        if self.plan:
            return list(self.plan)
        seen: dict[str, int] = {}
        steps: list[StepSpec] = []
        for block_id in self.blocks:
            seen[block_id] = seen.get(block_id, 0) + 1
            index = seen[block_id]
            steps.append(
                StepSpec(
                    id=block_id if index == 1 else f"{block_id}#{index}",
                    block=block_id,
                )
            )
        return steps

    def step_ids(self) -> list[str]:
        return [step.id for step in self.effective_steps()]

    def block_ids(self) -> list[str]:
        """本次实际会执行的块 id（按步骤顺序，可能重复；``plan`` 形态下取自 ``plan``）。"""

        return [step.block for step in self.effective_steps()]


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
    "StepSpec",
    "FactSpec",
    "BlockSpec",
    "CaseSpec",
    "SuiteSpec",
]
