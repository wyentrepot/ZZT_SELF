"""case_runner 内核数据模型（REQS v3 §10.2 四层模型）。

四层：Capability（能力适配）→ Fact（只读事实）→ Block（原子块）→ Case/Suite（编排）。

本模块**零领域依赖**：只允许 stdlib / pydantic，不得 import ``parser_lib`` /
``sim_concentrator`` / ``sim_meter`` / ``loghooks`` / ``minute_assert`` / ``apps.*``
（由 ``libs/case_runner/test_layering.py`` 铁律 1 守门）。

约定：
- 所有 ``*Spec`` 都是 **JSON 数据的形状**（``data/case_*/**.json``），不是执行体；
  执行体按 id 注册在 ``core/registry.py``。
- 结论模型**分两层，且只有两层**（REQS-0042 §3.5 / D2-4）：

  * **断言层三态**：``test_automation.models.AssertionResult`` 的
    ``outcome ∈ pass|fail|inconclusive``（``ASSERTION_OUTCOMES``）**一字不改**，
    永远不出现 ``blocked``。块结果、事实结果落在这里。
  * **结论层四态**：用例 / 方案的 ``verdict`` ∈ ``pass|fail|blocked|inconclusive``
    （本模块 ``OUTCOMES`` / ``OUTCOME_*``）。``blocked`` 只在聚合处产生，来源
    只是块态 ``skipped``（台架不具备条件），见 ``executor.BLOCK_TO_OUTCOME``。

- 事实三态见 ``FACT_*``（``unknown`` 永不折算 ``yes`` / ``pass``）。
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
# 结论口径：用例 / 方案 verdict **四态**（REQS-0042 §3.5 失败三分类 / D2-4）
# --------------------------------------------------------------------------- #
#
# 四态语义（同一份口径同时用于用例级与方案级聚合）：
#
# - ``pass``         判据成立（所有步骤都 pass，且用例级断言也 pass）
# - ``fail``         **被测对象真失败**：收到应答但判据不成立
# - ``blocked``      **台架不具备条件**：服务不可达 / 端口占用 / 闸门忙 / 前置事实不满足
#                    （块态 ``skipped`` 的唯一合法去处；产品本身没被证伪）
# - ``inconclusive`` **说不清**：超时无应答 / 解析不出 / 等待超时仍是过程态
#                    （块态 ``unknown`` 的去处）
#
# 优先级（同一用例/方案内多块混合时取最严重，见 ``OUTCOME_PRIORITY``）：
# ``fail`` > ``blocked`` > ``inconclusive`` > ``pass``。
#
# **红线（D2-4）**：``blocked`` 只活在结论层。断言层
# （``test_automation.models.AssertionResult``）仍然严格三值，见 ``OUTCOMES_ASSERTION``；
# 不许为了「四态一致」去扩 ``ASSERTION_OUTCOMES``。

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_BLOCKED = "blocked"
OUTCOME_INCONCLUSIVE = "inconclusive"

#: 用例 / 方案 verdict 允许的四个取值
OUTCOMES: frozenset[str] = frozenset(
    {OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_BLOCKED, OUTCOME_INCONCLUSIVE}
)

#: 严重度顺序（越靠前越严重）：聚合时取第一个出现的
OUTCOME_PRIORITY: tuple[str, ...] = (
    OUTCOME_FAIL,
    OUTCOME_BLOCKED,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
)

#: 断言层三态（``AssertionResult.outcome`` 的口径，取自
#: ``test_automation.models.ASSERTION_OUTCOMES``，此处**只读镜像**、不得扩成四态）
OUTCOMES_ASSERTION: frozenset[str] = frozenset(
    {OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_INCONCLUSIVE}
)

# --------------------------------------------------------------------------- #
# 块执行态（执行器内部状态；对外由 executor.BLOCK_TO_OUTCOME 折算成结论层四态，
# 折算到断言层时 skipped/unknown 仍是 inconclusive）
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
    - ``StepSpec``（JSON 里的 ``plan[]``）是**执行单元**：``{id, block, params, on_fail, expect}``，
      给执行器看。一个块可以在一条用例里被实例化多次，每次带自己的 ``params`` ——
      这就是「一次下发 3 块不同档案」的实现方式（``blocks[]`` 只是块 id 列表，
      参数只能全 run 一份，做不到这件事）。

    ``expect`` 语义（REQS-0044 §4.2「判据归属」，求值器见 ``core.expect``）
    ------------------------------------------------------------------
    块 = 传感器（只返回**观测**），"值符不符合期望"由**本步骤**声明、由求值器评。
    因此 ``expect`` 是**声明式**的期望清单（list / 单条 dict），**不允许代码字符串**：

    ```jsonc
    "plan": [{
      "id": "s2", "block": "nwk.topology", "params": {"wait_s": 300},
      "expect": [{"op": "complete"},
                 {"op": "ge_field", "field": "在网从节点数", "value": "门槛"}]
    }]
    ```

    - 空 ``expect`` = 本步**没有判据**：执行器沿用块自己给的 ``state``（旧路径，
      未迁移的 16 个旧块就是这条道，行为逐字不变）；
    - 有 ``expect`` 且块返回了观测 = 走求值器（``core.expect.evaluate``）；
    - ``op`` 词表与求值规则只在 ``core.expect`` 一处定义，这里只校验"有没有 op、
      op 在不在词表里"（加载期就报错，不必等跑台架才落一个 unknown）。

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
    #: 本步的**期望**（声明式：list 多条 / dict 单条；空 = 没有判据，沿用块的 state）
    expect: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_reserved_params(self) -> "StepSpec":
        reserved = [key for key in self.params if str(key).startswith("_")]
        if reserved:
            raise ValueError(
                f"步骤 {self.id!r} 的 params 里出现保留键 {reserved}："
                "以下划线开头的键由执行器注入（_assert / _cleanup），用例里写了也会被覆盖"
            )
        return self

    @model_validator(mode="after")
    def _check_expect_is_declarative(self) -> "StepSpec":
        """``expect`` 必须是声明式期望清单：每项带 ``op``，且 ``op`` 出自唯一词表。

        类型层已经挡掉代码字符串（只收 list / dict，写成字符串 pydantic 直接报错）；
        这里补两条**语义**校验，让作者在 ``case_run check`` 阶段就发现问题：

        - 每项都必须是带非空 ``op`` 的映射（否则求值器只能落 unknown，等于没判据）；
        - ``op`` 必须出自 :data:`case_runner.core.expect.EXPECT_OPS`（写错 op 在加载期
          报错，不必等跑台架时才落一个 unknown）。

        函数内 import 是为了避开 ``models ↔ expect`` 的模块级循环（``expect`` 要用
        本模块的块态常量）。
        """

        from .expect import EXPECT_OPS, VALUE_OPS  # 见 docstring（避开模块级循环）

        raw = self.expect
        if isinstance(raw, dict) and not raw:
            return self
        items = raw if isinstance(raw, list) else [raw]
        for index, item in enumerate(items):
            op = str(item.get("op") or "").strip().lower()
            if not op:
                raise ValueError(
                    f"步骤 {self.id!r} 的 expect[{index}] 缺少 op：期望必须是声明式的"
                    f" {{op, field?, value?}}（不允许代码字符串），收到 {dict(item)!r}"
                )
            if op not in EXPECT_OPS:
                raise ValueError(
                    f"步骤 {self.id!r} 的 expect[{index}] 的 op={op!r} 不在词表里；"
                    f"允许：{sorted(EXPECT_OPS)}（词表唯一来源：core/expect.py）"
                )
            if op in VALUE_OPS and not str(item.get("field") or "").strip():
                raise ValueError(
                    f"步骤 {self.id!r} 的 expect[{index}] 的 op={op!r} 必须给 field"
                    f"（要比的是观测里的哪个字段）"
                )
        return self

    @property
    def declares_expect(self) -> bool:
        """是否声明了期望（空 list / 空 dict 都算"没声明"，执行器据此走旧路径）。

        双轨切换的**唯一依据之一**：本步声明了期望 **且** 块返回了观测 → 走求值器；
        否则沿用块自己的 ``state``（旧路径逐字不变，见 ``executor._run_block``）。
        """

        return bool(self.expect)

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

    块元数据三字段（REQS-0044 §4.3 / AC-4）
    --------------------------------------
    - ``kind``：``case``（真测试）/ ``setup``（台架准备）/ ``teardown``（收尾）/ ``probe``（探测）。
      ``setup`` 类块判 pass 不进产品通过率（D-6「永真用例」的解药）。
    - ``side_effect``：``none`` / ``reversible`` / ``irreversible``。复位、烧录不可逆只能标
      顺序敏感；开表口、改档案可逆，必须有收场动作（D-7）。
    - ``version``：整数，判据/默认值一改必须递增；配合 :attr:`fingerprint`（声明内容指纹）
      回答「当时用的是哪一版」。

    迁移标记（REQS-0044 §4.2 / §6.2 第 3 步）
    ----------------------------------------
    - ``observation_only``：``true`` = 本块已迁为「只返回观测、不输出通过/失败」；
      引用它的步骤**必须**声明 ``expect``（无判据不入库，AC-6，见 loader 门禁）。
    - ``wait_param``：非空 = 本块是**等待型**块，该参数名（如 ``wait_s``）是等待预算，
      Procedure 步骤必须**显式**给值（v2 拍板 #3：预算写死在 Procedure 里，块默认只兜底；
      缺预算视为缺判据，loader 门禁拦截）。
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
    #: 块类别（AC-4）：真测试 / 台架准备 / 收尾 / 探测
    kind: Literal["case", "setup", "teardown", "probe"] = "case"
    #: 副作用（AC-4）：无 / 可逆 / 不可逆
    side_effect: Literal["none", "reversible", "irreversible"] = "none"
    #: 声明版本（AC-4）：判据/默认值一改必须递增；内容指纹见 :attr:`fingerprint`
    version: int = Field(default=1, ge=1)
    #: 已迁为「只返回观测」（REQS-0044 §4.2）；引用它的步骤必须声明 expect
    observation_only: bool = False
    #: 等待型块的**等待预算参数名**（如 ``wait_s``）；非空 = 调用方必须显式给该参数
    wait_param: str = ""

    @property
    def fingerprint(self) -> str:
        """声明内容指纹（sha256 前 16 位十六进制）。

        与 ``version`` 配合回答「当时用的是哪一版」：version 是作者声明的整数，
        指纹由**声明内容**算出——判据/默认值/事实链任一处改动，指纹必变，
        历史结果即可对照。``assert``/``cleanup`` 元数据一并计入（声明即资产）。
        """

        import hashlib
        import json as _json

        payload = self.model_dump(mode="json", by_alias=True)
        canonical = _json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class ProcedureCategory(BaseModel):
    """Procedure 分类（REQS-0042 D10-2 正式入模；取值须出自知识库检索结果）。

    报表契约（REQS-0044 变更 3 L2）：``group`` 取 ``scene``、``subgroup`` 取 ``domain``；
    ``kind=setup`` 的 Procedure 两项按 null 渲染为「—」。
    """

    usage: str = ""     # common / normal / rare（常用度）
    domain: str = ""    # CCO / STA / ECU / bench（设备域）
    scene: str = ""     # 组网 / 抄表 …（业务场景）
    region: str = ""    # 国网通用 / 南网通用 / 省份


class ProcedureSpec(BaseModel):
    """Procedure 声明（REQS-0044 §4.1 实体归一：**用例与场景是同一实体**）。

    旧名 ``CaseSpec``（0041/0042 的「编排层叫 Case」）——术语按标准正名
    （test procedure，ISTQB CTFL v4.0.1 §1.4.1）后，本类更名为 ``ProcedureSpec``；
    ``CaseSpec`` 作为别名保留（``libs/`` 内既有引用与外部调用方渐进替换，
    对外 ``/api/cases/*`` 契约语义不变、字段只加不改）。

    只做编排，零帧逻辑（不得出现帧 hex / 参数计算）。两种编排写法（REQS-0042 D3-1）：

    - 老写法 ``blocks: ["块 id", ...]``：顺序即执行序，参数全 run 一份；
    - 新写法 ``plan: [{id, block, params, on_fail, expect}, ...]``：**步骤实例化**，
      同一个块可以出现多次，每次带自己的参数与失败策略。

    规则：``blocks`` 与 ``plan`` **至少给一个**；两者都给 → **以 ``plan`` 为准**
    （``blocks`` 原样保留，供 ``case_run check`` 给一条提示，不是一个错误）。
    ``steps[]``（人读说明）两种写法都照旧必填，语义不变。

    ``kind``（REQS-0044 §4.4 + 变更 3 L1）
    ------------------------------------
    - ``regression``：常驻回归（默认），进产品通过率；
    - ``scenario``：一次性方案验证，跟需求走，不进回归方案；
    - ``setup``：**台架准备型流程**（L1），报表大类/子类为 null（渲染「—」）、
      不计入产品通过率（``counts_as_product=false``）。
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
    #: Procedure 类别（REQS-0044 变更 3 L1）：regression / scenario / setup
    kind: Literal["regression", "scenario", "setup"] = "regression"
    #: 分类维度（REQS-0042 D10-2 正式入模）：usage / domain / scene / region
    category: ProcedureCategory = Field(default_factory=ProcedureCategory)
    #: 协议标签（D10-2）：1376.2 / 645 / 698.45 / 10376.2 …
    protocols: list[str] = Field(default_factory=list)

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
    def _validate_orchestration(self) -> "ProcedureSpec":
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
    """用例方案：用户勾选组合（``data/case_suites/*.json`` 持久化，文件可 diff）。

    ``case_ids`` 指向 Procedure（REQS-0044 §4.1 实体归一后语义不变：方案 = 一批
    Procedure 的执行批次）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    case_ids: list[str] = Field(min_length=1)
    stop_on_fail: bool = False
    notes: str = ""


#: 旧名别名（REQS-0044 §4.1 术语正名：Case → Procedure）。``libs/`` 内既有引用与
#: 外部调用方渐进替换；``/api/cases/*`` 对外契约语义不变。
CaseSpec = ProcedureSpec


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
    # 结论口径（用例 / 方案 verdict 四态）
    "OUTCOME_PASS",
    "OUTCOME_FAIL",
    "OUTCOME_BLOCKED",
    "OUTCOME_INCONCLUSIVE",
    "OUTCOMES",
    "OUTCOME_PRIORITY",
    "OUTCOMES_ASSERTION",
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
    "ProcedureSpec",
    "ProcedureCategory",
    "CaseSpec",
    "SuiteSpec",
]
