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
（``params_schema`` 里有必需参数的，如 ``archive.contains`` 的 ``addr``）。
原因：块 ``provides`` 回写事实缓存只按 ``fact_id``（``FactsContext.set``），参数不同也会
命中 —— 给 addr A 建档成功，会让 addr B 的探测直接 ``yes``。删掉 ``archive.add`` 的
``provides`` 是治标，这条校验是防止别人再写回来的治本。

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
            params = required_params(fact)
            if not params:
                continue
            problems.append(
                f"{where}: block {spec.id!r} 的 provides 引用了参数化事实 {fact_id!r}"
                f"（必需参数 {'/'.join(params)}）—— 块回写只按 fact_id 缓存，"
                "不同参数值会命中造成假 PASS；请从 provides 移除，由块内部判据直接断言"
            )
    return problems


def check_case_refs(
    cases: Iterable[CaseSpec],
    blocks: Iterable[BlockSpec],
    *,
    sources: Mapping[str, str] | None = None,
    known_block_ids: Iterable[str] | None = None,
) -> list[str]:
    """用例引用的块必须已声明（``CaseSpec.blocks`` 为空由 pydantic 直接拦掉）。"""
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
    "DeclarationError",
    "LoadReport",
    "RegisterReport",
    "resolve_dir",
    "iter_declarations",
    "load_blocks",
    "load_facts",
    "load_cases",
    "load_suites",
    "load_all",
    "check_fact_refs",
    "check_provides_parameterized",
    "check_case_refs",
    "required_params",
    "iter_domain_module_names",
    "register_domain_modules",
    "registered_declarations",
]
