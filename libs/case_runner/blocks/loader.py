"""声明加载器：JSON 声明（``data/blocks`` / ``data/facts`` …）→ Spec 模型 + 校验。

REQS-0041 §10.5 KE-1：非法声明（缺 ``action`` / ``needs`` 未注册 / ``blocks`` 为空）
**加载期报 ``file:reason``，不得静默跳过**；本模块把这句话落成两条硬约束：

1. 任何**存在但非法**的 JSON 文件 → 收集成 ``"<file>: <原因>"``，``strict=True`` 时
   一次性抛 :class:`DeclarationError`（消息逐行可 grep，不是吞掉）。
2. 目录不存在 / 目录为空 → 返回 ``[]``（并行开发期 ``data/cases`` 等尚未落地属正常，
   这不是「非法声明」，所以不报错）。

第三条（防「跨参数假 PASS」的结构门禁）
------------------------------------
:func:`check_provides_parameterized`：**块的 ``provides`` 不得引用参数化事实**
（``params_schema`` 里有必需参数、或声明了 ``parameterized: true`` 的）。
原因：块 ``provides`` 回写事实缓存只按 ``fact_id``（``FactsContext.set``），参数不同也会
命中 —— 给 meter_addr A 建档成功，会让 meter_addr B 的探测直接 ``yes``。删掉 ``archive.add``
的 ``provides`` 是治标，这条校验是防止别人再写回来的治本。
（REQS-0042 D2-2 起「参数化」的权威口径是显式 ``parameterized: true``，
必需参数只是兼容旧写法：参数都有默认值之后 required 为空，门禁会空转。）

第四条（``null`` 占位解析，REQS-0042 D2-2）
----------------------------------------
:func:`resolve_declaration_defaults`：声明里 ``"meter_addr": null``（以及事实
``params_schema`` 里地址参数的 ``default: null``）表示「取两层配置的默认值」，在
**加载期**解析成 :func:`case_runner.core.defaults.default_meter_addr` 的真实值 ——
于是 ``case_run show`` 与 ``--params`` 看到/覆盖的都是真值。解析只在内存里做，
**不改写 JSON 文件**；默认值读不到时保留 ``null``，由上层照常报「缺参数」。

另提供 :func:`register_domain_modules`：自动发现 ``case_runner/blocks/`` 下的领域模块并
import，触发其 ``register_block`` / ``register_fact``；领域模块若另外暴露 ``register()``
则一并调用（该函数应自身幂等，见 ``blocks/fix.py`` 的实现）。

本模块只依赖 ``case_runner.core``（声明形状与注册表）——不含任何领域逻辑，也不 import
``case_runner.capabilities``（分层铁律）。
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from case_runner.core import defaults
from case_runner.core.models import BlockSpec, CaseSpec, FactSpec, SuiteSpec
from case_runner.core.registry import (
    Duplicate,
    has_block,
    has_fact,
    list_blocks,
    list_facts,
)

#: 包内数据根目录：``libs/case_runner/data``
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

#: 各类声明在 ``data/`` 下的默认子目录
BLOCKS_DIRNAME = "blocks"
FACTS_DIRNAME = "facts"
CASES_DIRNAME = "cases"
SUITES_DIRNAME = "case_suites"

#: ``register_domain_modules`` 默认扫描的包
BLOCKS_PACKAGE = "case_runner.blocks"

#: 不算「领域模块」的文件名（包入口 / 加载器自身 / 测试）
NON_DOMAIN_MODULES = frozenset({"loader", "conftest"})
NON_DOMAIN_PREFIXES = ("test_", "_")

SpecT = TypeVar("SpecT", bound=BaseModel)


# --------------------------------------------------------------------------- #
# 错误与结果
# --------------------------------------------------------------------------- #


class DeclarationError(ValueError):
    """声明加载/校验失败。``problems`` 每条形如 ``"<file>: <原因>"``。"""

    def __init__(self, problems: Iterable[str], *, headline: str = "声明校验未通过") -> None:
        self.problems: list[str] = list(problems)
        message = f"{headline}（{len(self.problems)} 条）：\n" + "\n".join(
            f"  - {problem}" for problem in self.problems
        )
        super().__init__(message)


@dataclass
class RegisterReport:
    """领域模块导入结果（``failed`` 非空即表示有模块没注册上）。"""

    imported: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def problems(self) -> list[str]:
        return [f"{name}: {reason}" for name, reason in self.failed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": list(self.imported),
            "failed": [{"module": name, "error": reason} for name, reason in self.failed],
        }


@dataclass
class LoadReport:
    """一次全量加载的结果（``strict=False`` 时携带 ``problems`` 而不抛异常）。"""

    blocks: list[BlockSpec] = field(default_factory=list)
    facts: list[FactSpec] = field(default_factory=list)
    cases: list[CaseSpec] = field(default_factory=list)
    suites: list[SuiteSpec] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    sources: dict[str, dict[str, str]] = field(default_factory=dict)
    register: RegisterReport | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def block_specs(self) -> dict[str, BlockSpec]:
        """``{block_id: BlockSpec}``（执行器 ``RunContext.block_specs`` 直接可用）。"""
        return {spec.id: spec for spec in self.blocks}

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks": [spec.id for spec in self.blocks],
            "facts": [spec.id for spec in self.facts],
            "cases": [spec.id for spec in self.cases],
            "suites": [spec.id for spec in self.suites],
            "problems": list(self.problems),
            "sources": {kind: dict(items) for kind, items in self.sources.items()},
            "register": self.register.to_dict() if self.register else None,
        }


# --------------------------------------------------------------------------- #
# 目录与单文件加载
# --------------------------------------------------------------------------- #


def resolve_dir(directory: str | Path | None, dirname: str = "") -> Path:
    """把「目录 / 数据根 / ``None``」统一解析成绝对目录。

    - ``None`` → :data:`DATA_DIR`（``dirname`` 非空时再拼一层子目录）
    - 目录名已经等于 ``dirname``（如直接给了 ``data/blocks``）→ 原样使用
    - 其下存在同名子目录（如给了 ``data/``）→ 用子目录
    - 其它（临时目录里直接放 JSON）→ 原样使用
    """
    if directory is None:
        return DATA_DIR / dirname if dirname else DATA_DIR
    path = Path(directory).expanduser()
    if not dirname or path.name == dirname:
        return path
    nested = path / dirname
    return nested if nested.is_dir() else path


def iter_declarations(directory: str | Path) -> list[Path]:
    """列目录下 ``*.json``（排序稳定；目录不存在/为空 → ``[]``）。"""
    path = Path(directory)
    if not path.is_dir():
        return []
    return sorted(p for p in path.glob("*.json") if p.is_file())


# --------------------------------------------------------------------------- #
# 声明里的 ``null`` 占位 → 两层默认配置（REQS-0042 D2-2）
# --------------------------------------------------------------------------- #

#: 声明里写 ``null`` 表示「取默认值」的两个键（D2-1 的唯一参数名口径）
METER_ADDR_KEY = "meter_addr"
METER_ADDR_LIST_KEY = "meter_addrs"
#: 参数名命中这两个，其 ``params_schema`` 里的 ``default: null`` 才按默认地址解析
DEFAULT_ADDR_PARAM_NAMES = frozenset({METER_ADDR_KEY, METER_ADDR_LIST_KEY})
PARAMS_SCHEMA_KEY = "params_schema"


def _placeholder_addr() -> str | None:
    """``null`` 占位解析出的默认地址；读不到默认值 → ``None``（保留原样）。

    刻意**不**把读不到的情况降级成 ``""``：空串地址会让块「看起来有参数」，
    最后发一帧地址全空的报文；保留 ``None`` 则由上层照常报「缺必填参数」。
    """

    return defaults.default_meter_addr() or None


def _resolve_param_entry(name: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """解析一个参数定义（``params_schema`` 里的一项）。

    只有**地址类参数名**（``meter_addr`` / ``meter_addrs``）的 ``default: null`` 会被
    解析成默认地址 —— 其它参数（``mapping_id`` / ``request_hex`` …）的 ``null`` 是
    「未配置」的语义，不能塞一个表地址进去。
    """

    resolved = {str(key): _resolve_defaults(value) for key, value in entry.items()}
    if name in DEFAULT_ADDR_PARAM_NAMES and "default" in resolved and resolved["default"] is None:
        placeholder = _placeholder_addr()
        if placeholder is not None:
            resolved["default"] = placeholder
    return resolved


def _resolve_params_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """解析 ``params_schema``（扁平写法与 JSON-Schema 写法都认，与 ``required_params`` 同口径）。"""

    resolved: dict[str, Any] = {}
    for name, entry in schema.items():
        key = str(name)
        if key == "properties" and isinstance(entry, Mapping):
            resolved[key] = {
                str(item_name): (
                    _resolve_param_entry(str(item_name), item)
                    if isinstance(item, Mapping)
                    else _resolve_defaults(item)
                )
                for item_name, item in entry.items()
            }
            continue
        if key == "required" and isinstance(entry, (list, tuple)):
            resolved[key] = list(entry)
            continue
        if key in _SCHEMA_KEYWORDS or not isinstance(entry, Mapping):
            resolved[key] = _resolve_defaults(entry)
            continue
        resolved[key] = _resolve_param_entry(key, entry)
    return resolved


def _resolve_defaults(node: Any) -> Any:
    """递归解析：键名命中地址占位 → 默认地址；``params_schema`` → 逐参数解析。"""

    if isinstance(node, Mapping):
        resolved: dict[str, Any] = {}
        for name, value in node.items():
            key = str(name)
            if key == METER_ADDR_KEY and value is None:
                resolved[key] = _placeholder_addr()
            elif key == METER_ADDR_LIST_KEY and isinstance(value, (list, tuple)):
                resolved[key] = [
                    _placeholder_addr() if item is None else _resolve_defaults(item)
                    for item in value
                ]
            elif key == PARAMS_SCHEMA_KEY and isinstance(value, Mapping):
                resolved[key] = _resolve_params_schema(value)
            else:
                resolved[key] = _resolve_defaults(value)
        return resolved
    if isinstance(node, (list, tuple)):
        return [_resolve_defaults(item) for item in node]
    return node


def resolve_declaration_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把一份声明原文里的 ``null`` 占位解析成两层配置的默认值（**不写回文件**）。

    覆盖三处（REQS-0042 D2-2，「声明不再各自写死地址」）：

    - 块 ``action.params.meter_addr`` / ``action.params.meter_addrs[]``；
    - 块 ``assert`` / ``cleanup`` 里同类键（递归走遍整份声明）；
    - 事实 ``params_schema.<参数>.default``（仅地址类参数名）。

    非地址的 ``null``（如 ``request_hex: null``）**原样保留**；默认值读不到时也保留
    ``null``，让上层照常报「缺参数」，而不是把一个空地址发到台架。
    """

    return _resolve_defaults(raw)


def _format_validation_error(path: Path, exc: ValidationError) -> list[str]:
    problems: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc") or ()) or "<root>"
        problems.append(f"{path.as_posix()}: {location}: {error.get('msg', '校验失败')}")
    return problems or [f"{path.as_posix()}: 校验失败（无详细信息）"]


def _load_one(path: Path, model: type[SpecT]) -> tuple[SpecT | None, list[str]]:
    """读一个 JSON 文件并校验；返回 ``(spec, problems)``。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, [f"{path.as_posix()}: 读取失败：{exc}"]
    except json.JSONDecodeError as exc:
        return None, [
            f"{path.as_posix()}: JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"
        ]
    if not isinstance(raw, dict):
        return None, [
            f"{path.as_posix()}: 顶层必须是 JSON 对象，实际是 {type(raw).__name__}"
        ]
    raw = resolve_declaration_defaults(raw)
    try:
        spec = model.model_validate(raw)
    except ValidationError as exc:
        return None, _format_validation_error(path, exc)
    return spec, []


def _load_dir(
    directory: str | Path | None,
    model: type[SpecT],
    dirname: str,
    kind: str,
    *,
    strict: bool,
    problems: list[str],
    sources: dict[str, str] | None = None,
    extra_checks: Sequence[Any] = (),
) -> list[SpecT]:
    """加载一个声明目录（内部通用实现：去重 + 逐文件校验 + 可插拔语义检查）。"""
    target = resolve_dir(directory, dirname)
    specs: list[SpecT] = []
    seen: dict[str, Path] = {}
    for path in iter_declarations(target):
        spec, file_problems = _load_one(path, model)
        problems.extend(file_problems)
        if spec is None:
            continue
        ident = str(getattr(spec, "id", "") or "")
        if ident in seen:
            problems.append(
                f"{path.as_posix()}: {kind} id 重复 {ident!r}"
                f"（已在 {(seen[ident]).as_posix()} 定义）"
            )
            continue
        seen[ident] = path
        for check in extra_checks:
            problems.extend(check(path, spec))
        specs.append(spec)
        if sources is not None:
            sources[ident] = path.as_posix()
    if strict and problems:
        raise DeclarationError(problems)
    return specs


# --------------------------------------------------------------------------- #
# 语义检查（KE-1 的「缺 action / needs 未注册」两条）
# --------------------------------------------------------------------------- #


def _check_block_action(path: Path, spec: BlockSpec) -> list[str]:
    """块必须有可执行的 ``action``（空字典 = 声明了但没法执行，按非法处理）。"""
    if not spec.action:
        return [
            f"{path.as_posix()}: block {spec.id!r} 的 action 为空"
            "（至少给出 afn/fn | recipe | semantic | kind 之一）"
        ]
    return []


def check_fact_refs(
    blocks: Iterable[BlockSpec],
    facts: Iterable[FactSpec],
    *,
    sources: Mapping[str, str] | None = None,
    require_registered: bool = False,
) -> list[str]:
    """``needs``/``provides`` 引用的事实必须已声明（或已注册）。

    ``require_registered=True`` 时还要求块/事实的**执行体**已在注册表里（KE-1 的
    「needs 未注册」字面口径）；默认只查声明集合，方便单独对 JSON 做静态校验。
    """
    src = dict(sources or {})
    declared_facts = {spec.id for spec in facts}
    declared_facts |= {spec.probe for spec in facts if spec.probe}
    problems: list[str] = []
    for spec in blocks:
        where = src.get(spec.id, f"block {spec.id!r}")
        for fact_id in list(spec.needs) + list(spec.provides):
            known = fact_id in declared_facts or has_fact(fact_id)
            if not known:
                problems.append(
                    f"{where}: block {spec.id!r} 引用的事实未声明/未注册：{fact_id!r}"
                )
        if require_registered and not has_block(spec.id):
            problems.append(f"{where}: block {spec.id!r} 没有注册执行体（register_block）")
    if require_registered:
        for spec in facts:
            where = src.get(spec.id, f"fact {spec.id!r}")
            if not has_fact(spec.probe):
                problems.append(
                    f"{where}: fact {spec.id!r} 的 probe 未注册：{spec.probe!r}"
                )
    return problems


#: ``params_schema`` 里不算「参数定义」的 JSON-Schema 关键字
_SCHEMA_KEYWORDS = frozenset(
    {
        "type", "properties", "required", "additionalProperties", "description",
        "title", "$schema", "definitions", "$defs", "patternProperties", "default",
    }
)


def required_params(spec: FactSpec) -> list[str]:
    """事实的**必需**参数名（空 = 非参数化事实）。

    两种 ``params_schema`` 写法都认：

    - 扁平写法：``{"addr": {"required": true, ...}}``（本项目 ``archive_contains.json`` 的风格）
    - JSON-Schema 写法：``{"type": "object", "required": ["min"], "properties": {...}}``，
      以及 ``properties`` 里逐项标 ``"required": true``

    注意：「参数化」的判据是**有必需参数**，不是「``params_schema`` 非空」——
    ``bench.simcon_online`` 这类事实的参数全都有默认值，不同取值不产生歧义，
    ``fix.simcon_session`` 的 ``provides`` 是合法的。

    0042 起「参数化」的**权威口径**改成 :func:`is_parameterized`（显式
    ``parameterized: true`` 标记 ∪ 必需参数）；本函数只负责后者（兼容旧写法）。
    """
    schema = spec.params_schema or {}
    if not isinstance(schema, Mapping):
        return []
    found: list[str] = []
    top_required = schema.get("required")
    if isinstance(top_required, (list, tuple)):
        found.extend(str(name) for name in top_required)
    for container in (schema.get("properties"), schema):
        if not isinstance(container, Mapping):
            continue
        for name, entry in container.items():
            if name in _SCHEMA_KEYWORDS or not isinstance(entry, Mapping):
                continue
            if entry.get("required") is True:
                found.append(str(name))
    return list(dict.fromkeys(found))


def is_parameterized(spec: FactSpec) -> bool:
    """该事实是否**按入参求值**（结论随参数变化）——结构门禁的权威判据。

    两条取或：

    1. 声明里显式写 ``"parameterized": true``（REQS-0042 起的**权威口径**）；
    2. 兼容旧写法：``required_params(spec)`` 非空（``required: true`` 的参数）。

    为什么不能只靠 required：D2-2 之后参数都有默认值（``"default": null`` 取
    ``case_defaults.json``），``archive.contains`` 这类「换地址结论就变」的事实
    **没有**必需参数了 —— 只按 required 反推会让门禁空转（测试
    ``test_parameterized_fact_set_is_not_empty`` 守的就是这件事）。
    """

    return bool(getattr(spec, "parameterized", False)) or bool(required_params(spec))


def check_provides_parameterized(
    blocks: Iterable[BlockSpec],
    facts: Iterable[FactSpec],
    *,
    sources: Mapping[str, str] | None = None,
) -> list[str]:
    """块 ``provides`` **不得**引用参数化事实（防「跨参数假 PASS」）。

    块 ``provides`` 回写事实缓存只按 ``fact_id``，参数不同也命中 ——
    给 ``addr=A`` 建档成功，会让 ``addr=B`` 的探测直接 ``yes``。
    参数化事实只能**现探**（``FactsContext`` 参数感知缓存 / 每次 probe），不能由块回写。

    「参数化」判据 = :func:`is_parameterized`（显式 ``parameterized: true`` ∪ 必需参数）；
    理由见该函数 docstring（0042 D2-2 之后必需参数可能为空，不能靠它反推）。
    """
    src = dict(sources or {})
    by_id: dict[str, FactSpec] = {}
    for spec in facts:
        by_id[spec.id] = spec
        if spec.probe and spec.probe not in by_id:
            by_id[spec.probe] = spec
    problems: list[str] = []
    for spec in blocks:
        where = src.get(spec.id, f"block {spec.id!r}")
        for fact_id in spec.provides:
            fact = by_id.get(fact_id)
            if fact is None:
                continue  # 未声明的由 check_fact_refs 报，这里不重复
            if not is_parameterized(fact):
                continue
            params = required_params(fact)
            why = (
                "必需参数 " + "/".join(params)
                if params
                else "声明 parameterized: true（按入参求值）"
            )
            problems.append(
                f"{where}: block {spec.id!r} 的 provides 引用了参数化事实 {fact_id!r}"
                f"（{why}）—— 块回写只按 fact_id 缓存，"
                "不同参数值会命中造成假 PASS；请从 provides 移除，由块内部判据直接断言"
            )
    return problems


#: 块声明**必须显式写出**的三字段（REQS-0044 §4.3 / AC-4：16 块全量补齐，
#: 靠模型缺省值「看起来有」不算——声明文件里没有就是没补）
REQUIRED_BLOCK_METADATA: frozenset[str] = frozenset({"kind", "side_effect", "version"})


def check_block_metadata(blocks: Iterable[BlockSpec], *,
                         sources: Mapping[str, str] | None = None) -> list[str]:
    """每个块声明必须**显式**带 ``kind`` / ``side_effect`` / ``version``（AC-4）。

    模型层给三字段留了缺省值（老声明不至于加载失败），但 AC-4 的口径是
    「**每个块有**三字段」：声明文件里没写就是没补，靠缺省值蒙混会让
    「台架准备判 pass 混进产品通过率」「不可逆动作没有收场标记」这类问题
    静默存在。``model_fields_set`` 记录了「原文里写了哪些键」，用它判。
    """
    src = dict(sources or {})
    problems: list[str] = []
    for spec in blocks:
        where = src.get(spec.id, f"block {spec.id!r}")
        missing = sorted(
            key for key in REQUIRED_BLOCK_METADATA if key not in spec.model_fields_set
        )
        if missing:
            problems.append(
                f"{where}: block {spec.id!r} 缺少块元数据字段：{'/'.join(missing)}"
                "（REQS-0044 §4.3 / AC-4：kind/side_effect/version 必须显式声明）"
            )
    return problems


def check_procedure_judgement(
    cases: Iterable[CaseSpec],
    blocks: Iterable[BlockSpec],
    *,
    sources: Mapping[str, str] | None = None,
    known_block_ids: Iterable[str] | None = None,
) -> list[str]:
    """Procedure 步骤判据门禁（REQS-0044 AC-6「无判据不入库」+ v2 拍板 #3 等待预算）。

    两条硬规则，命中任一即 problem（``check`` / ``run`` / API 一并拒绝）：

    1. 步骤引用 **已迁为只观测**（``observation_only=True``）的块却没声明 ``expect``
       → 无判据不入库：块不判了、步骤又不判，这条步骤等于没判据，跑出来只能是 unknown；
    2. 步骤引用**等待型**块（声明了 ``wait_param``，如组网/绑表/抄表）而 ``params``
       缺该参数（或值 ≤ 0）→ 等待预算视为缺判据（v2 拍板 #3：预算写死在 Procedure 里，
       块默认值只作兜底，缺了它历史结论不可比）。

    未迁移的旧块（``observation_only=False`` 且无 ``wait_param``）不受影响——
    迁移一块收紧一块（§6.2 迁移顺序，不允许跳）。
    """
    src = dict(sources or {})
    by_id = {spec.id: spec for spec in blocks}
    problems: list[str] = []
    for case in cases:
        where = src.get(case.id, f"case {case.id!r}")
        for step in case.effective_steps():
            spec = by_id.get(step.block)
            if spec is None:
                continue  # 引用完整性由 check_case_refs 报，这里不重复
            has_expect = bool(step.expect)
            if spec.observation_only and not has_expect:
                problems.append(
                    f"{where}: case {case.id!r} 的步骤 {step.id!r} 引用只观测块 "
                    f"{step.block!r} 但未声明 expect —— 无判据不入库"
                    "（REQS-0044 AC-6：判据在步骤，块只报观测）"
                )
            wait_param = str(getattr(spec, "wait_param", "") or "").strip()
            if wait_param:
                given = step.params.get(wait_param)
                try:
                    value = float(given) if given is not None else 0.0
                except (TypeError, ValueError):
                    value = 0.0
                if value <= 0:
                    problems.append(
                        f"{where}: case {case.id!r} 的步骤 {step.id!r} 引用等待型块 "
                        f"{step.block!r} 但 params 缺少等待预算 {wait_param!r}（或 ≤0）"
                        "—— 等待预算写死在 Procedure 里（0044 §8.1-3），缺预算视为缺判据"
                    )
    return problems


def _registered_block_spec(block_id: str) -> BlockSpec | None:
    """兜底：声明没给但块库注册过的块没有声明形状，返回 ``None``（由引用检查报）。"""
    return None


def check_case_refs(
    cases: Iterable[CaseSpec],
    blocks: Iterable[BlockSpec],
    *,
    sources: Mapping[str, str] | None = None,
    known_block_ids: Iterable[str] | None = None,
) -> list[str]:
    """用例引用的块必须已声明 —— 两种编排写法都查（REQS-0042 D3-1）。

    - ``blocks[]``（老写法）：逐块查，文案与 0041 一致；
    - ``plan[]``（步骤实例化）：逐**步骤**查，错误信息点名「哪个用例的哪一步引用哪个块」——
      同一个块可以被实例化多次，只报块 id 会分不清是哪一步写错了。

    「``blocks`` 与 ``plan`` 都给」不是错误（以 ``plan`` 为准），这里只校验引用完整性；
    「给了一条提示」由入口层（``case_run check``）负责，属于提醒不是失败。
    """
    src = dict(sources or {})
    known = {spec.id for spec in blocks} | set(known_block_ids or ())
    problems: list[str] = []
    for spec in cases:
        where = src.get(spec.id, f"case {spec.id!r}")
        for block_id in spec.blocks:
            if block_id not in known:
                problems.append(
                    f"{where}: case {spec.id!r} 引用了未声明的块：{block_id!r}"
                )
        for step in spec.plan:
            if step.block not in known:
                problems.append(
                    f"{where}: case {spec.id!r} 的步骤 {step.id!r} "
                    f"引用了未声明的块：{step.block!r}"
                )
    return problems


# --------------------------------------------------------------------------- #
# 对外加载入口
# --------------------------------------------------------------------------- #


def load_blocks(directory: str | Path | None = None, *,
                strict: bool = True) -> list[BlockSpec]:
    """加载块声明（默认 ``data/blocks/*.json``）。

    非法声明（JSON 语法错 / 字段缺失 / ``action`` 为空 / id 重复）→
    :class:`DeclarationError`，消息逐行 ``file:reason``。
    """
    problems: list[str] = []
    try:
        return _load_dir(directory, BlockSpec, BLOCKS_DIRNAME, "block",
                         strict=strict, problems=problems,
                         extra_checks=(_check_block_action,))
    except DeclarationError as exc:
        raise DeclarationError(exc.problems, headline="块声明校验未通过") from None


def load_facts(directory: str | Path | None = None, *,
               strict: bool = True) -> list[FactSpec]:
    """加载事实声明（默认 ``data/facts/*.json``）。"""
    problems: list[str] = []
    try:
        return _load_dir(directory, FactSpec, FACTS_DIRNAME, "fact",
                         strict=strict, problems=problems)
    except DeclarationError as exc:
        raise DeclarationError(exc.problems, headline="事实声明校验未通过") from None


def load_cases(directory: str | Path | None = None, *,
               strict: bool = True) -> list[CaseSpec]:
    """加载用例声明（默认 ``data/cases/*.json``；目录不存在 → ``[]``）。"""
    problems: list[str] = []
    try:
        return _load_dir(directory, CaseSpec, CASES_DIRNAME, "case",
                         strict=strict, problems=problems)
    except DeclarationError as exc:
        raise DeclarationError(exc.problems, headline="用例声明校验未通过") from None


def load_suites(directory: str | Path | None = None, *,
                strict: bool = True) -> list[SuiteSpec]:
    """加载方案声明（默认 ``data/case_suites/*.json``；目录不存在 → ``[]``）。"""
    problems: list[str] = []
    try:
        return _load_dir(directory, SuiteSpec, SUITES_DIRNAME, "suite",
                         strict=strict, problems=problems)
    except DeclarationError as exc:
        raise DeclarationError(exc.problems, headline="方案声明校验未通过") from None


def load_all(
    directory: str | Path | None = None,
    *,
    strict: bool = True,
    register_modules: bool = False,
    require_registered: bool = False,
) -> LoadReport:
    """一次加载块/事实/用例/方案，并做跨文件引用校验。

    - ``directory``：``data/`` 根；``None`` 用包内 :data:`DATA_DIR`
    - ``register_modules=True``：先 :func:`register_domain_modules`（默认不导入，
      避免「只想校验 JSON」的调用被领域依赖拖累）
    - ``require_registered=True``：还要求执行体已注册（进程内实跑前用）
    - ``strict=True``：有任何问题就抛 :class:`DeclarationError`
    """
    root = resolve_dir(directory) if directory is not None else DATA_DIR
    problems: list[str] = []
    report = LoadReport()
    if register_modules:
        report.register = register_domain_modules(strict=False)
        problems.extend(report.register.problems())

    sources: dict[str, dict[str, str]] = {
        "blocks": {}, "facts": {}, "cases": {}, "suites": {},
    }
    try:
        report.blocks = _load_dir(root, BlockSpec, BLOCKS_DIRNAME, "block",
                                  strict=True, problems=problems,
                                  sources=sources["blocks"],
                                  extra_checks=(_check_block_action,))
    except DeclarationError as exc:
        problems.extend(exc.problems)
    try:
        report.facts = _load_dir(root, FactSpec, FACTS_DIRNAME, "fact",
                                 strict=True, problems=problems,
                                 sources=sources["facts"])
    except DeclarationError as exc:
        problems.extend(exc.problems)
    try:
        report.cases = _load_dir(root, CaseSpec, CASES_DIRNAME, "case",
                                 strict=True, problems=problems,
                                 sources=sources["cases"])
    except DeclarationError as exc:
        problems.extend(exc.problems)
    try:
        report.suites = _load_dir(root, SuiteSpec, SUITES_DIRNAME, "suite",
                                  strict=True, problems=problems,
                                  sources=sources["suites"])
    except DeclarationError as exc:
        problems.extend(exc.problems)

    all_sources = {**sources["blocks"], **sources["facts"], **sources["cases"]}
    problems.extend(check_fact_refs(report.blocks, report.facts,
                                    sources=all_sources,
                                    require_registered=require_registered))
    problems.extend(check_provides_parameterized(report.blocks, report.facts,
                                                 sources=all_sources))
    problems.extend(check_case_refs(report.cases, report.blocks,
                                    sources=all_sources,
                                    known_block_ids=list_blocks()))
    problems.extend(check_block_metadata(report.blocks, sources=all_sources))
    problems.extend(check_procedure_judgement(report.cases, report.blocks,
                                              sources=all_sources))
    report.sources = sources
    report.problems = problems
    if strict and problems:
        raise DeclarationError(problems, headline="声明加载未通过")
    return report


# --------------------------------------------------------------------------- #
# 领域模块自动发现（触发 register_block / register_fact）
# --------------------------------------------------------------------------- #


def iter_domain_module_names(package: str = BLOCKS_PACKAGE) -> list[str]:
    """列 ``package`` 下的领域模块名（跳过包入口/loader/测试/私有）。"""
    module = importlib.import_module(package)
    paths = getattr(module, "__path__", None)
    if not paths:
        return []
    names: list[str] = []
    for info in pkgutil.iter_modules(list(paths)):
        name = info.name
        if name in NON_DOMAIN_MODULES:
            continue
        if name.startswith(NON_DOMAIN_PREFIXES):
            continue
        names.append(name)
    return sorted(names)


def register_domain_modules(
    package: str = BLOCKS_PACKAGE,
    *,
    strict: bool = True,
) -> RegisterReport:
    """import ``package`` 下每个领域模块，触发其 ``register_block``/``register_fact``。

    - 已注册的 id 重复注册（``Duplicate``）视为「幂等成功」，不记失败；
    - 领域模块可选暴露 ``register()``：若存在则**再调一次**（实现须自身幂等，
      以便测试 ``clear_registries()`` 之后能重新注册）；
    - 其余异常记入 ``RegisterReport.failed``；``strict=True`` 时抛
      :class:`DeclarationError`（**不静默跳过**某个块库模块）。
    """
    report = RegisterReport()
    for name in iter_domain_module_names(package):
        full = f"{package}.{name}"
        try:
            module = importlib.import_module(full)
        except Exception as exc:  # noqa: BLE001 - 汇总成报告，由 strict 决定是否抛
            report.failed.append((full, f"导入失败 {type(exc).__name__}: {exc}"))
            continue
        register = getattr(module, "register", None)
        if callable(register):
            try:
                register()
            except Duplicate:
                pass  # 导入时已注册过，幂等
            except Exception as exc:  # noqa: BLE001
                report.failed.append((full, f"register() 失败 {type(exc).__name__}: {exc}"))
                continue
        report.imported.append(full)
    if strict and report.failed:
        raise DeclarationError(report.problems(), headline="领域模块注册未通过")
    return report


# --------------------------------------------------------------------------- #
# 便捷入口
# --------------------------------------------------------------------------- #


def registered_declarations(report: LoadReport | None = None) -> dict[str, Any]:
    """一行摘要：已注册块/事实 + 本次加载到的声明 id（CLI/日志用）。"""
    data = report or LoadReport()
    return {
        "registered_blocks": list_blocks(),
        "registered_facts": list_facts(),
        "declared_blocks": [spec.id for spec in data.blocks],
        "declared_facts": [spec.id for spec in data.facts],
    }


__all__ = [
    "DATA_DIR",
    "BLOCKS_DIRNAME",
    "FACTS_DIRNAME",
    "CASES_DIRNAME",
    "SUITES_DIRNAME",
    "BLOCKS_PACKAGE",
    "METER_ADDR_KEY",
    "METER_ADDR_LIST_KEY",
    "DeclarationError",
    "LoadReport",
    "RegisterReport",
    "resolve_dir",
    "iter_declarations",
    "resolve_declaration_defaults",
    "load_blocks",
    "load_facts",
    "load_cases",
    "load_suites",
    "load_all",
    "check_fact_refs",
    "check_provides_parameterized",
    "check_case_refs",
    "check_block_metadata",
    "check_procedure_judgement",
    "REQUIRED_BLOCK_METADATA",
    "required_params",
    "is_parameterized",
    "iter_domain_module_names",
    "register_domain_modules",
    "registered_declarations",
]
