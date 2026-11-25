"""观测 / 期望求值器（REQS-0044 §4.2「判据归属」／TODO P1 第一条）。

本模块只做一件事：把「块看到了什么」（:class:`Observation`）与「调用方期望看到什么」
（``expect`` 声明）对一遍，产出该**步骤**的块执行态（``pass | fail | skipped | unknown``）。

为什么判定要单独成模块
======================
旧口径下"值符不符合期望"写在**块**里，于是同一份观测只能服务一种期望：建档用例期望
"含该地址"、清库用例期望"不含"，判据在块里就必须造两个块（REQS-0044 §1.2 D-3）。
新口径把两件事拆开：**块 = 传感器**（只报观测），**步骤 = 判据**（声明期望，本模块求值）。
四态语义与聚合优先级**一个字不改**（``core.models`` / ``executor.aggregate_verdict``）——
改的只是"谁在判"（§4.2）。

本模块是**纯函数**集合：不碰串口、不碰网络、不 import 任何领域层（``test_layering.py``
铁律 1 守门），因此可以离线单测、离线校验，也可以在报告层复用。

观测 schema（块返回什么）
=========================
块可以把观测放在返回映射的 ``observation`` 键里，也可以直接 ``return Observation(...)``；
执行器会把它归一成 :class:`Observation`（见 ``executor.coerce_block_return``）。

```jsonc
{
  "observation": {
    "values": {                      // 观测值：一次交互看到的**全部**字段（域名字段名自定）
      "节点总数量": 12,
      "主节点数": 1,
      "在网从节点数": 11,
      "档案数": 12,
      "门槛": 12,
      "探测次数": 3,
      "等待秒": 4.0
    },
    "complete": true,                // 完整性：是否取全（翻页取全 / 主节点取全 / 等满预算）
    "reason_code": "",               // 没拿到时的原因码（空 = 拿到了观测），取值见下表
    "detail": "翻了 2 页取全 40 条节点记录"   // 人话说明，附证据
  }
}
```

:data:`OBSERVATION_FIELDS` 四件套：

===============  ========  ==========================================================
键               类型      含义
===============  ========  ==========================================================
``values``       dict      观测值，可带多个字段；**空 dict = 什么都没看到**
``complete``     bool      完整性；**缺省 False**（未声明完整性 = 不完整，见下）
``reason_code``  str       没拿到观测时的原因（串口被占 / 台架不可达 / 闸门忙 /
                           等待超时 / 参数非法 / 观测不完整）
``detail``       str       人话说明（等了 N 秒、只取到 32 / 总数 40……）
===============  ========  ==========================================================

**观测里没有"通过 / 失败"**（REQS-0044 AC-1）：块只有两种处境——拿到了观测，或者
没拿到并说清为什么（``docs/blocks/`` 五份文档的"观测三件套"）。原因码一律是中性的
"为什么没拿到"，不描述"算不算失败"。

``complete`` 为什么**缺省 False**：缺省 True 等于替块宣布"我取全了"，取不全就会被读成
pass —— 那正是 0044 §6.3 R-1「主节点只在本页数 → 差一个判成达标」的假 PASS 形态。
宁可 unknown，绝不假 PASS（REQS RU-3）。

期望 schema（步骤声明什么，``StepSpec.expect``）
================================================
**声明式，不允许代码字符串**（写成字符串会被 pydantic 类型挡掉，见
``models.StepSpec._check_declared_expects``）。单条断言给 ``dict``，多条给 ``list``；
**多条之间是"与"**。取值全部是 JSON 原生类型。

```jsonc
"expect": [
  {"op": "complete"},                                         // ① 观测必须取全
  {"op": "ge_field", "field": "在网从节点数", "value": "门槛"}, // ② 读数 ≥ 门槛（门槛也是观测字段）
  {"op": "eq",       "field": "DI回显",      "value": "00010000"}, // ③ 值相等
  {"op": "has_value","field": "数值"}                          // ④ 有值且可解（不比数值真值）
]
```

op 词表（:data:`EXPECT_OPS`）：

==================================  ========  ==================================================
op                                  有无 field  含义
==================================  ========  ==================================================
``complete``                        无        期望观测完整（取全）
``got``                             无        期望拿到了观测（原因码为空）
``not_got``                         无        期望**没拿到**观测（负例）；可带 ``reason: [原因码]``
``eq`` / ``ne``                     有        等于 / 不等于（字符串按原样比较，大小写敏感）
``ge`` / ``gt`` / ``le`` / ``lt``   有        数值比较（任一侧不是数量 → 无法求值）
``eq_field`` / ``ne_field``         有        与**另一个观测字段**比较（阈值由块回传，如"门槛"）
``ge_field`` / ``gt_field``         有        同上，数值不等号
``le_field`` / ``lt_field``         有        同上
``contains`` / ``not_contains``      有        容器包含 / 不包含（str 子串、list 成员、dict 键）
``has_value``                       有        有值且可解（非 None / 非空串 / 非空容器）
``present`` / ``absent``            有        观测里有没有该字段（区分"没有字段"与"字段值为 null"）
==================================  ========  ==================================================

求值规则（铁律，改它等于改四态语义）
====================================
:func:`evaluate` 按**固定顺序**判，顺序本身就是口径：

1. **没有可识别的期望** → ``unknown``（没有判据不判决，绝不 pass）。
2. **块没给观测**（``None``）→ ``unknown``（无证据可比；连"为什么没拿到"都没有）。
3. **没拿到观测**（``reason_code`` 非空）：
   - 声明了 ``not_got``（负例）→ 按声明逐条比：拿到观测 → ``fail``；原因与声明不符 →
     ``unknown``（没验成，不冤判）；相符 → 继续比其余项；
   - 没声明负例 → **环境原因**（``serial_busy`` / ``bench_unreachable`` / ``gate_busy``，
     台架不具备条件）→ ``skipped``（聚合处落 ``blocked``）；**其余原因**
     （等待超时 / 参数非法 / 观测不完整）→ ``unknown``（聚合处落 ``inconclusive``）。
     两者都**绝不 pass**。
4. **观测不完整**（``complete is False``）→ ``unknown``，即便已取到的字段恰好都符合期望。
   ``docs/blocks/case-流程-单一档案抄表.md`` §①b 原文："查不全 → 不完整，**不得**折算成
   '含'或'不含'"；同一份口径也适用于"只取到 N / 总数 M"的组网读数。
5. 逐条求值：任一条**不成立** → ``fail``；任一条**无法求值** → ``unknown``；全部成立 → ``pass``。
   （``fail`` 优先于 ``unknown``，与四态优先级 ``fail > blocked > inconclusive > pass``
   同向：拿到确切反证就不该被"另一条没求值"盖成说不清。）

"无法求值"的三种来源，都落 ``unknown``（不是 fail）：期望项缺 ``field``、观测里没有该字段、
未知 ``op``。它们说明的是"这条判据没验成"，不是"产品不成立"。

与执行器的关系（双轨兼容）
==========================
:func:`evaluate` 是纯函数，执行器只在**块返回了观测**且**步骤声明了 ``expect``** 时调它；
否则原样沿用块自己给的 ``state``（旧路径逐字不变，见 ``executor._run_block``）。这样
未迁移的 16 个旧块行为一行不改，迁移一个块就多一条走新口径的步骤。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple

from .models import BLOCK_FAIL, BLOCK_PASS, BLOCK_SKIPPED, BLOCK_UNKNOWN

# --------------------------------------------------------------------------- #
# 观测侧：键名、原因码、环境原因集合
# --------------------------------------------------------------------------- #

#: 块返回映射里承载观测的键（``BlockResult.observation`` 的同名口径）。
#: 与 ``_assert`` / ``_cleanup`` 两个**保留参数键**无关：那些是入参，这个是返回值。
OBSERVATION_KEY = "observation"

#: 观测模型自身的字段（四件套；观测里**不得**出现 state / pass / fail 这类结论字段）
OBSERVATION_FIELDS: tuple[str, ...] = ("values", "complete", "reason_code", "detail")

#: 没拿到观测时的原因码（"为什么没拿到"，不是"算不算失败"）
OBS_SERIAL_BUSY = "serial_busy"  # 串口被占（僵尸会话 / 他人占用）
OBS_BENCH_UNREACHABLE = "bench_unreachable"  # 台架不可达（服务没起 / 网络不通）
OBS_GATE_BUSY = "gate_busy"  # 闸门忙（409：另一轮 run 正在占台架）
OBS_TIMEOUT = "timeout"  # 等待超时：预算等满仍无应答（一帧都没有）
OBS_INVALID_PARAM = "invalid_param"  # 参数非法：根本没能下发（地址/预算/白名单拒绝）
OBS_INCOMPLETE = "incomplete"  # 观测不完整：帧收到了，但取不全 / 解析不出

#: 原因码 → 人话（报告与断言 message 共用这一份口径，别处不要另写一套）
OBS_REASON_LABELS: dict[str, str] = {
    OBS_SERIAL_BUSY: "串口被占",
    OBS_BENCH_UNREACHABLE: "台架不可达",
    OBS_GATE_BUSY: "闸门忙（409）",
    OBS_TIMEOUT: "等待超时（全程无应答）",
    OBS_INVALID_PARAM: "参数非法（未下发）",
    OBS_INCOMPLETE: "观测不完整（只取到一部分）",
}

#: **台架不具备条件**的原因码：没拿到观测时落 ``skipped``（聚合处 → ``blocked``）。
#:
#: 三个都是"环境没准备好"，产品本身没被证伪；其余原因（等待超时 / 参数非法 /
#: 观测不完整）是"说不清"，落 ``unknown``（聚合处 → ``inconclusive``）。
#: 参数非法为什么不算台架问题：那是用例 / 配置写错，改一改就能跑，不是台架不具备条件，
#: 也不构成对产品的失败结论 —— 归到"说不清"最保守。
ENVIRONMENT_REASON_CODES: frozenset[str] = frozenset(
    {OBS_SERIAL_BUSY, OBS_BENCH_UNREACHABLE, OBS_GATE_BUSY}
)

# --------------------------------------------------------------------------- #
# 期望侧：op 词表
# --------------------------------------------------------------------------- #

# 观测级（不带 field）
OP_COMPLETE = "complete"
OP_GOT = "got"
OP_NOT_GOT = "not_got"

# 值级（带 field）
OP_EQ = "eq"
OP_NE = "ne"
OP_GE = "ge"
OP_GT = "gt"
OP_LE = "le"
OP_LT = "lt"
OP_EQ_FIELD = "eq_field"
OP_NE_FIELD = "ne_field"
OP_GE_FIELD = "ge_field"
OP_GT_FIELD = "gt_field"
OP_LE_FIELD = "le_field"
OP_LT_FIELD = "lt_field"
OP_CONTAINS = "contains"
OP_NOT_CONTAINS = "not_contains"
OP_HAS_VALUE = "has_value"
OP_PRESENT = "present"
OP_ABSENT = "absent"

#: 观测级 op（不带 ``field``）
OBSERVATION_OPS: frozenset[str] = frozenset({OP_COMPLETE, OP_GOT, OP_NOT_GOT})
#: 值级 op（必须带 ``field``）
VALUE_OPS: frozenset[str] = frozenset(
    {
        OP_EQ,
        OP_NE,
        OP_GE,
        OP_GT,
        OP_LE,
        OP_LT,
        OP_EQ_FIELD,
        OP_NE_FIELD,
        OP_GE_FIELD,
        OP_GT_FIELD,
        OP_LE_FIELD,
        OP_LT_FIELD,
        OP_CONTAINS,
        OP_NOT_CONTAINS,
        OP_HAS_VALUE,
        OP_PRESENT,
        OP_ABSENT,
    }
)
#: ``expect[].op`` 的全部合法取值（新增请一并更新本集合与模块头的词表）
EXPECT_OPS: frozenset[str] = OBSERVATION_OPS | VALUE_OPS

#: ``*_field`` op → 对应的值比较 op（阈值来自另一个观测字段）
_SAME_VALUE_OPS: dict[str, str] = {
    OP_EQ_FIELD: OP_EQ,
    OP_NE_FIELD: OP_NE,
    OP_GE_FIELD: OP_GE,
    OP_GT_FIELD: OP_GT,
    OP_LE_FIELD: OP_LE,
    OP_LT_FIELD: OP_LT,
}

#: 人读符号（detail 文案，CLI / 报告直接可用）
_OP_SYMBOLS: dict[str, str] = {
    OP_EQ: "=",
    OP_NE: "≠",
    OP_GE: "≥",
    OP_GT: ">",
    OP_LE: "≤",
    OP_LT: "<",
    OP_CONTAINS: "包含",
    OP_NOT_CONTAINS: "不含",
    OP_HAS_VALUE: "有值且可解",
    OP_PRESENT: "有该字段",
    OP_ABSENT: "无该字段",
}

# --------------------------------------------------------------------------- #
# 观测模型
# --------------------------------------------------------------------------- #


def _as_bool(value: Any, default: bool = False) -> bool:
    """把 ``complete`` 归一成 bool（``"false"`` 这种字符串绝不能被 ``bool()`` 读成 True）。"""

    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("true", "yes", "1"):
            return True
        if token in ("false", "no", "0", ""):
            return False
    return bool(value)


@dataclass
class Observation:
    """一次块交互的**结构化观测**（块 = 传感器；这里没有"通过/失败"）。

    三件事各归各位（REQS-0044 §4.2）：

    - ``values``：**观测值**，可以有多个字段（读数 / 帧原文 / 地址 / 计数……）；
    - ``complete``：**完整性**，是否取全（翻页取全、主节点取全、等待等满）。缺省 ``False``
      = 未声明完整性 → 按不完整处理（宁可 unknown，绝不假 pass）；
    - ``reason_code``：**没拿到时的原因**（空 = 拿到了观测）。原因码走
      :data:`OBS_REASON_LABELS`，只描述"为什么没拿到"。
    """

    #: 观测值：字段名自定（域名词），值须可 JSON 序列化（结果要落盘）
    values: dict[str, Any] = field(default_factory=dict)
    #: 完整性：是否取全。**缺省 False**（未声明 = 不完整，见类注释）
    complete: bool = False
    #: 没拿到观测时的原因码（空 = 拿到了）；取值见 :data:`OBS_REASON_LABELS`
    reason_code: str = ""
    #: 人话说明（等待读数、翻页进度、解析器报错原文……）
    detail: str = ""

    def __post_init__(self) -> None:
        # 块是外部执行体：形状不规范时**降级**而不是抛异常 —— 抛异常会被执行器记成
        # "块执行异常 → fail"，把"我写错了观测"误读成"产品失败"。
        if isinstance(self.values, Mapping):
            self.values = dict(self.values)
        elif self.values is None:
            self.values = {}
        else:
            self.values = {"value": self.values}
        self.complete = _as_bool(self.complete, default=False)
        self.reason_code = str(self.reason_code or "").strip()
        self.detail = str(self.detail or "").strip()

    @property
    def got(self) -> bool:
        """是否拿到了观测（原因码为空即拿到；**不**等于"完整"）。"""

        return not self.reason_code

    @property
    def reason_label(self) -> str:
        """原因码的人话（未知原因码原样透出，便于发现块里写错了码）。"""

        return OBS_REASON_LABELS.get(self.reason_code) or self.reason_code

    def summary(self) -> str:
        """观测值的一句话摘要（``字段=值`` 用顿号连起来，最多 6 个字段防刷屏）。"""

        if not self.values:
            return self.detail or "无观测值"
        parts = []
        for name, value in list(self.values.items())[:6]:
            parts.append(f"{name}={_render(value)}")
        if len(self.values) > 6:
            parts.append(f"…（共 {len(self.values)} 项）")
        return "、".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """落盘 / API 口径（键名与 JSON 声明一致，可原样回灌 :func:`coerce_observation`）。"""

        return {
            "values": dict(self.values),
            "complete": self.complete,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


def coerce_observation(raw: Any) -> Observation | None:
    """把块返回的任意"观测"归一为 :class:`Observation`（``None`` = 没给观测）。

    宽容但**不抬高**：形状不规范一律按"不完整"处理（``complete=False``），绝不因为
    字段看着对就当成可判定的完整观测。
    """

    if raw is None:
        return None
    if isinstance(raw, Observation):
        return raw
    if isinstance(raw, Mapping):
        values = raw.get("values")
        if values is None and "value" in raw:
            # 单项观测的兼容写法：``{"value": 1234.56}``
            values = {"value": raw.get("value")}
        code = str(raw.get("reason_code") or raw.get("reasonCode") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        detail = str(raw.get("detail") or "").strip()
        if not code and reason in OBS_REASON_LABELS:
            # 只给 ``reason`` 且它本身就是原因码 → 当码用；否则当人话说明
            code, reason = reason, ""
        if reason:
            detail = f"{detail}；{reason}" if detail else reason
        return Observation(
            values=dict(values) if isinstance(values, Mapping) else (values or {}),
            complete=_as_bool(raw.get("complete"), default=False),
            reason_code=code,
            detail=detail,
        )
    # 非 mapping：当成单一观测值，且**不完整**（形状不规范 = 说不清）
    return Observation(
        values={"value": raw},
        complete=False,
        detail=f"观测形状不规范（{type(raw).__name__}，应为 mapping），按不完整处理",
    )


# --------------------------------------------------------------------------- #
# 期望模型
# --------------------------------------------------------------------------- #


def normalize_expects(raw: Any) -> list[dict[str, Any]]:
    """把步骤声明的 ``expect`` 归一成"断言项列表"（永远返回 list，不抛异常）。

    - ``dict`` → 单条断言（必须自带 ``op``）；
    - ``list``/``tuple`` → 逐项收下（非 mapping 项丢弃 = 无法求值）；
    - ``str`` / 其它 → **一律不认**（REQS-0044 要求声明式：期望不许写成代码字符串）。
      丢弃后列表为空 → 求值器判 ``unknown``（没有判据不判决），绝不因为写错就放过。
    """

    if raw is None:
        return []
    if isinstance(raw, Mapping):
        return [dict(raw)] if raw else []
    if isinstance(raw, (list, tuple)):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def _op_of(item: Mapping[str, Any]) -> str:
    return str(item.get("op") or "").strip().lower()


def _reason_codes_of(item: Mapping[str, Any]) -> list[str]:
    """``not_got`` 的 ``reason`` 白名单（接受 str 或 list；空 = 任意原因均可）。"""

    raw = item.get("reason")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(item_).strip() for item_ in raw if str(item_).strip()]
    return []


def describe_expect(item: Mapping[str, Any]) -> str:
    """单条期望的人话（``DI回显 = 00010000`` / ``观测完整``），报告与 detail 共用。"""

    op = _op_of(item)
    if op == OP_COMPLETE:
        return "观测完整"
    if op == OP_GOT:
        return "已拿到观测"
    if op == OP_NOT_GOT:
        reasons = _reason_codes_of(item)
        if not reasons:
            return "未拿到观测"
        labels = "/".join(OBS_REASON_LABELS.get(code) or code for code in reasons)
        return f"未拿到观测（原因：{labels}）"
    field_name = str(item.get("field") or "").strip() or "?"
    symbol = _OP_SYMBOLS.get(op) or _OP_SYMBOLS.get(_SAME_VALUE_OPS.get(op, ""), "") or op
    if op in (OP_HAS_VALUE, OP_PRESENT, OP_ABSENT):
        return f"{field_name} {symbol}"
    value = item.get("value")
    suffix = "（观测字段）" if op in _SAME_VALUE_OPS else ""
    return f"{field_name} {symbol} {_render(value)}{suffix}"


def describe_expects(raw: Any) -> str:
    """整份期望的人话（``；`` 连接；空声明给一句明确说明而不是空串）。"""

    items = normalize_expects(raw)
    if not items:
        return "（未声明期望）"
    return "；".join(describe_expect(item) for item in items)


# --------------------------------------------------------------------------- #
# 求值
# --------------------------------------------------------------------------- #


class Evaluation(NamedTuple):
    """一步的求值结论（可直接解包成 ``state, detail, unmet, complete``）。

    - ``state``：块执行态 ``pass | fail | skipped | unknown``（聚合映射不变：
      ``skipped → blocked`` / ``unknown → inconclusive``）；
    - ``detail``：人话结论（含"为什么不是 pass"与观测摘要）；
    - ``unmet``：未成立 / 无法求值的条目说明（逐条，供证据与报告）；
    - ``complete``：本次观测是否取全（透传，报告层不必再解 Observation）。
    """

    state: str
    detail: str
    unmet: list[str]
    complete: bool


#: 单条断言的求值结果（内部三分，最后再收敛成步骤态）
_ITEM_HOLD = "hold"  # 成立
_ITEM_FAIL = "fail"  # 不成立（拿到确切反证）
_ITEM_UNKNOWN = "unknown"  # 无法求值（没验成）

_NUMERIC_OPS: frozenset[str] = frozenset({OP_GE, OP_GT, OP_LE, OP_LT})

#: 判断"有值且可解"时要排除的占位串（表里没这一项时常被写成这些）
_BLANK_TOKENS: frozenset[str] = frozenset({"", "none", "null", "-", "—", "n/a", "na"})


def _render(value: Any) -> str:
    """值的人读化（字符串不加引号，其余按 repr 风格；长内容截断防刷屏）。"""

    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif value is None:
        return "null"
    else:
        text = repr(value)
    return text if len(text) <= 60 else text[:57] + "…"


def _as_number(value: Any) -> float | None:
    """数值化（bool 不算数量：``True`` 参与 ``≥`` 比较没有意义）。"""

    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_meaningful(value: Any) -> bool:
    """值是否"可解且非空"（§8.1-4：只要求"有值且可解"，不校验数值真实性）。"""

    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _BLANK_TOKENS
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) > 0
    return True


def _equal(actual: Any, expected: Any) -> bool:
    """相等比较（bool 与数字**不互通**：``True == 1`` 在 Python 里成立，但语义完全不同）。"""

    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual == expected
    return actual == expected


def _compare(op: str, left: Any, right: Any) -> bool | None:
    """按 op 比较两个值；任一侧不是数量 → ``None``（无法求值，不是 fail）。"""

    if op in (OP_EQ, OP_NE):
        same = _equal(left, right)
        return same if op == OP_EQ else not same
    left_num, right_num = _as_number(left), _as_number(right)
    if left_num is None or right_num is None:
        return None
    if op == OP_GE:
        return left_num >= right_num
    if op == OP_GT:
        return left_num > right_num
    if op == OP_LE:
        return left_num <= right_num
    return left_num < right_num


def _eval_item(item: Mapping[str, Any], obs: Observation) -> tuple[str, str]:
    """单条断言求值 → ``(三分结果, 人话)``。"""

    op = _op_of(item)

    # -- 观测级：拿到 / 没拿到 / 完整 ------------------------------------- #
    if op == OP_GOT:
        if obs.reason_code:
            return _ITEM_FAIL, f"期望拿到观测，实际没拿到（{obs.reason_label}）"
        return _ITEM_HOLD, "已拿到观测"

    if op == OP_NOT_GOT:
        if not obs.reason_code:
            return _ITEM_FAIL, f"期望没拿到观测，实际拿到了（{obs.summary()}）"
        wanted = _reason_codes_of(item)
        if wanted and obs.reason_code not in wanted:
            # 原因不符 ≠ 产品不成立：这条负例没验成，落"说不清"（不冤判 fail）
            labels = "/".join(OBS_REASON_LABELS.get(code) or code for code in wanted)
            return (
                _ITEM_UNKNOWN,
                f"期望没拿到观测且原因为 {labels}，实际为 {obs.reason_label}（没验成）",
            )
        return _ITEM_HOLD, f"未拿到观测（{obs.reason_label}）"

    if op == OP_COMPLETE:
        if obs.complete:
            return _ITEM_HOLD, "观测完整"
        # 取数不全是"说不清"，不是"产品不成立"（flow 文档 §①b：不得折算成含/不含）
        return _ITEM_UNKNOWN, f"期望观测完整，实际{obs.reason_label or '未取全'}"

    # -- 值级：先确认字段在不在 -------------------------------------------- #
    field_name = str(item.get("field") or "").strip()
    if not field_name:
        return _ITEM_UNKNOWN, f"期望项缺少 field（op={op!r}），无法求值"
    if op == OP_PRESENT:
        if field_name in obs.values:
            return _ITEM_HOLD, f"{field_name} 在观测里（{_render(obs.values[field_name])}）"
        return _ITEM_FAIL, f"期望 {field_name} 在观测里，实际没有该字段"
    if op == OP_ABSENT:
        if field_name not in obs.values:
            return _ITEM_HOLD, f"{field_name} 不在观测里"
        return _ITEM_FAIL, f"期望没有 {field_name}，实际有（{_render(obs.values[field_name])}）"
    if field_name not in obs.values:
        # 块声明"完整"但字段缺失 = 这条判据没验成（unknown），不是 fail
        return _ITEM_UNKNOWN, f"观测里没有字段 {field_name}，期望项无法求值"
    actual = obs.values[field_name]

    if op == OP_HAS_VALUE:
        if _is_meaningful(actual):
            return _ITEM_HOLD, f"{field_name} 有值（{_render(actual)}）"
        return _ITEM_FAIL, f"期望 {field_name} 有值且可解，实际为空（{_render(actual)}）"

    if op in (OP_CONTAINS, OP_NOT_CONTAINS):
        expected = item.get("value")
        hit: bool | None
        if isinstance(actual, Mapping):
            hit = expected in actual
        elif isinstance(actual, (list, tuple, set, frozenset)):
            hit = expected in actual
        elif isinstance(actual, str):
            hit = str(expected) in actual if expected is not None else None
        else:
            hit = None
        if hit is None:
            return _ITEM_UNKNOWN, f"{field_name} 不是容器/字符串，包含判断无法求值"
        if op == OP_CONTAINS:
            if hit:
                return _ITEM_HOLD, f"{field_name} 包含 {_render(expected)}"
            return _ITEM_FAIL, f"期望 {field_name} 包含 {_render(expected)}，实际不含"
        if not hit:
            return _ITEM_HOLD, f"{field_name} 不含 {_render(expected)}"
        return _ITEM_FAIL, f"期望 {field_name} 不含 {_render(expected)}，实际含"

    if op in _SAME_VALUE_OPS:
        other_name = str(item.get("value") or "").strip()
        if other_name not in obs.values:
            return (
                _ITEM_UNKNOWN,
                f"阈值字段 {other_name or '?'} 不在观测里，{field_name} 的比对无法求值",
            )
        right = obs.values[other_name]
        base_op = _SAME_VALUE_OPS[op]
        outcome = _compare(base_op, actual, right)
        symbol = _OP_SYMBOLS.get(base_op, base_op)
        if outcome is None:
            return (
                _ITEM_UNKNOWN,
                f"{field_name}={_render(actual)} 与 {other_name}={_render(right)} 无法比较",
            )
        text = f"{field_name} {symbol} {other_name}（{_render(actual)} vs {_render(right)}）"
        return (_ITEM_HOLD, text) if outcome else (_ITEM_FAIL, text + " 不成立")

    if op in (OP_EQ, OP_NE):
        expected = item.get("value")
        outcome = _compare(op, actual, expected)
        if outcome:
            return _ITEM_HOLD, f"{field_name} = {_render(actual)}"
        return (
            _ITEM_FAIL,
            f"{field_name} {_OP_SYMBOLS.get(op, op)} {_render(expected)}"
            f"（实际 {_render(actual)}）不成立",
        )

    if op in _NUMERIC_OPS:
        expected = item.get("value")
        outcome = _compare(op, actual, expected)
        symbol = _OP_SYMBOLS.get(op, op)
        if outcome is None:
            return (
                _ITEM_UNKNOWN,
                f"{field_name}={_render(actual)} 与 {_render(expected)} 不是数量，"
                f"{symbol} 比较无法求值",
            )
        text = f"{field_name} {symbol} {_render(expected)}（实际 {_render(actual)}）"
        return (_ITEM_HOLD, text) if outcome else (_ITEM_FAIL, text + " 不成立")

    # 未知 op：不猜、不放过（落 unknown，绝不因为写错就判 pass）
    return _ITEM_UNKNOWN, f"未知期望 op {op!r}（{describe_expect(item)}），无法求值"


def _reduce(obs: Observation, results: list[tuple[str, str]]) -> Evaluation:
    """把逐条结果收敛成步骤态：任一 fail → fail；否则任一 unknown → unknown；否则 pass。"""

    fails = [text for verdict, text in results if verdict == _ITEM_FAIL]
    unknowns = [text for verdict, text in results if verdict == _ITEM_UNKNOWN]
    holds = [text for verdict, text in results if verdict == _ITEM_HOLD]
    tail = f"；观测：{obs.summary()}" if obs.values else ""
    if fails:
        return Evaluation(
            BLOCK_FAIL, "期望不成立：" + "；".join(fails) + tail, fails + unknowns, obs.complete
        )
    if unknowns:
        return Evaluation(
            BLOCK_UNKNOWN,
            "期望项无法求值（没验成，不判 pass）：" + "；".join(unknowns) + tail,
            unknowns,
            obs.complete,
        )
    return Evaluation(BLOCK_PASS, "期望成立：" + "；".join(holds), [], obs.complete)


def _not_observed(obs: Observation, expects: list[dict[str, Any]]) -> Evaluation:
    """没拿到观测、且步骤没声明 ``not_got`` 负例时的结论：环境原因 → skipped，其余 → unknown。"""

    planned = [describe_expect(item) for item in expects]
    detail = f"（{obs.detail}）" if obs.detail else ""
    if obs.reason_code in ENVIRONMENT_REASON_CODES:
        return Evaluation(
            BLOCK_SKIPPED,
            f"未拿到观测：{obs.reason_label}{detail} → 台架不具备条件（产品没被证伪），"
            f"期望项未验证：{'；'.join(planned)}",
            planned,
            obs.complete,
        )
    return Evaluation(
        BLOCK_UNKNOWN,
        f"未拿到观测：{obs.reason_label}{detail} → 说不清（不判 pass），"
        f"期望项未验证：{'；'.join(planned)}",
        planned,
        obs.complete,
    )


def evaluate(observation: Any, expects: Any) -> Evaluation:
    """按"期望 vs 观测"产出步骤态（纯函数；铁律见模块头的「求值规则」）。

    ``observation``：:class:`Observation` 或它的 dict 形态（``None`` = 块没给观测）；
    ``expects``：``StepSpec.expect`` 原样（list / dict / 其它）。

    返回 :class:`Evaluation` —— 可直接解包成 ``state, detail, unmet, complete``。
    """

    items = normalize_expects(expects)
    obs = coerce_observation(observation)
    if not items:
        # 没有判据不判决：宁可 inconclusive，也不替调用方宣布"通过"
        return Evaluation(
            BLOCK_UNKNOWN,
            "步骤未声明可识别的期望（expect 为空或不是声明式列表），不判决",
            [],
            obs.complete if obs is not None else False,
        )
    if obs is None:
        return Evaluation(
            BLOCK_UNKNOWN,
            "块没有返回观测（也没有原因码），期望无从比对：unknown 绝不判 pass",
            [describe_expect(item) for item in items],
            False,
        )

    negatives = [item for item in items if _op_of(item) == OP_NOT_GOT]
    if obs.reason_code:
        # 没拿到观测：声明了负例就按声明判，否则按"是否台架原因"分流（两者都不 pass）
        if negatives:
            return _reduce(obs, [_eval_item(item, obs) for item in items])
        return _not_observed(obs, items)
    if not obs.complete:
        # 观测不完整：即便已取到的字段恰好都符合期望，也绝不判 pass（否则"只取到一页"
        # 会被读成"全部达标"，正是 0044 R-1 的假 PASS 形态）
        return Evaluation(
            BLOCK_UNKNOWN,
            "观测不完整（%s），不判 pass：期望项未全部验证（%s）"
            % (obs.detail or "块未声明取全", describe_expects(items)),
            [describe_expect(item) for item in items],
            False,
        )
    return _reduce(obs, [_eval_item(item, obs) for item in items])


def step_state(observation: Any, expects: Any) -> tuple[str, str]:
    """便捷入口：只要 ``(state, detail)`` 的调用方用这个（报告/CLI 常见）。"""

    evaluation = evaluate(observation, expects)
    return evaluation.state, evaluation.detail


__all__ = [
    # 观测侧
    "OBSERVATION_KEY",
    "OBSERVATION_FIELDS",
    "OBS_SERIAL_BUSY",
    "OBS_BENCH_UNREACHABLE",
    "OBS_GATE_BUSY",
    "OBS_TIMEOUT",
    "OBS_INVALID_PARAM",
    "OBS_INCOMPLETE",
    "OBS_REASON_LABELS",
    "ENVIRONMENT_REASON_CODES",
    "Observation",
    "coerce_observation",
    # 期望侧
    "EXPECT_OPS",
    "OBSERVATION_OPS",
    "VALUE_OPS",
    "OP_COMPLETE",
    "OP_GOT",
    "OP_NOT_GOT",
    "OP_EQ",
    "OP_NE",
    "OP_GE",
    "OP_GT",
    "OP_LE",
    "OP_LT",
    "OP_EQ_FIELD",
    "OP_NE_FIELD",
    "OP_GE_FIELD",
    "OP_GT_FIELD",
    "OP_LE_FIELD",
    "OP_LT_FIELD",
    "OP_CONTAINS",
    "OP_NOT_CONTAINS",
    "OP_HAS_VALUE",
    "OP_PRESENT",
    "OP_ABSENT",
    "normalize_expects",
    "describe_expect",
    "describe_expects",
    # 求值
    "Evaluation",
    "evaluate",
    "step_state",
]
