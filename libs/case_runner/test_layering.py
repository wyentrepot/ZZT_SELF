"""P1-5 分层门禁（REQS v3 §10.4 三条铁律，用 ``ast`` 解析，不靠人记）。

铁律 1：``libs/case_runner/core/`` 下任何 ``.py`` 不得 import
        ``capabilities`` / ``case_runner.capabilities`` / ``parser_lib`` /
        ``sim_concentrator`` / ``sim_meter`` / ``loghooks`` / ``minute_assert`` /
        ``apps`` / ``workbench``（含函数内 import、含相对 import）。
铁律 2：``libs/case_runner/capabilities/`` 下各适配器文件之间互不 import。
        该目录由能力层并行开发：目录不存在/无适配器文件时本断言 **skip**（不是 fail）。
        两类文件不计入「适配器互相依赖」检查（它们不是适配器，且理应 import 适配器）：
        包的聚合入口 ``__init__.py``、测试文件 ``test_*.py`` / ``conftest.py``。
铁律 3：``libs/case_runner/`` 下不得出现 ``from apps`` / ``import apps``
        （并顺带守住整个 ``libs/`` 目录的同一口径）。

每条铁律都配一个「自检」用例：在临时目录造违规文件，确认断言逻辑真的会报错，
证明门禁不是摆设（PLAN P1-5 要求「人为加一条违规 import 时对应断言必须变红」）。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

CASE_RUNNER_ROOT = Path(__file__).resolve().parent
LIBS_ROOT = CASE_RUNNER_ROOT.parent
CORE_DIR = CASE_RUNNER_ROOT / "core"
CAPABILITIES_DIR = CASE_RUNNER_ROOT / "capabilities"

#: 铁律 1 的禁用前缀（任一命中即违规）
CORE_FORBIDDEN = (
    "capabilities",
    "case_runner.capabilities",
    "parser_lib",
    "sim_concentrator",
    "sim_meter",
    "loghooks",
    "minute_assert",
    "apps",
    "workbench",
)
#: 铁律 3 的禁用前缀
APPS_FORBIDDEN = ("apps",)

#: 包入口不算「适配器互相依赖」
_PACKAGE_ENTRY = "__init__.py"
#: 测试文件必须 import 被测适配器，不算「适配器互相依赖」
_TEST_FILE_PREFIXES = ("test_", "conftest")


def is_adapter_file(path: Path) -> bool:
    """是否算「能力适配器」（包入口与测试文件不算）。"""
    if path.name == _PACKAGE_ENTRY:
        return False
    return not path.name.startswith(_TEST_FILE_PREFIXES)


@dataclass(frozen=True)
class ImportRef:
    """一条 import 记录（AST 原样，相对 import 保留 level）。"""

    path: Path
    lineno: int
    module: str
    level: int
    names: tuple[str, ...]

    def render(self) -> str:
        dots = "." * self.level
        if self.level and self.module:
            return f"from {dots}{self.module} import {', '.join(self.names) or '*'}"
        if self.level:
            return f"from {dots or '.'} import {', '.join(self.names)}"
        if self.names:
            return f"from {self.module} import {', '.join(self.names)}"
        return f"import {self.module}"

    def where(self) -> str:
        return f"{self.path.as_posix()}:{self.lineno}: {self.render()}"


def iter_py_files(root: Path) -> list[Path]:
    """递归列 ``.py``（跳过 ``__pycache__``）。"""
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts
    )


def iter_imports(path: Path) -> list[ImportRef]:
    """解析单文件的全部 ``Import`` / ``ImportFrom``（含函数体内的）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - 并行写文件时的瞬时态
        if CASE_RUNNER_ROOT in path.parents and CORE_DIR in path.parents:
            pytest.fail(f"core 文件无法解析：{path}: {exc}")
        pytest.skip(f"非本任务范围的文件正在被并行写入，无法解析：{path}: {exc}")
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            refs.append(
                ImportRef(
                    path=path,
                    lineno=node.lineno,
                    module=node.module or "",
                    level=node.level,
                    names=tuple(alias.name for alias in node.names),
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(
                    ImportRef(
                        path=path,
                        lineno=node.lineno,
                        module=alias.name,
                        level=0,
                        names=(),
                    )
                )
    return refs


def _match_prefix(module: str, forbidden: tuple[str, ...]) -> str | None:
    for pref in forbidden:
        if module == pref or module.startswith(pref + "."):
            return pref
    return None


def _candidate_modules(ref: ImportRef) -> list[str]:
    """一条 import 可能指向的模块名（``from . import X`` 里的 X 也是一个模块名）。"""
    candidates: list[str] = []
    if ref.module:
        candidates.append(ref.module)
    if ref.level and not ref.module:
        # 相对 import 的 ``from . import capabilities`` 等价于 import 子包 capabilities
        candidates.extend(ref.names)
    return candidates


def find_forbidden_imports(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    """铁律 1 / 3 的检查体：返回违规描述列表（空 = 合规）。"""
    violations: list[str] = []
    for path in iter_py_files(root):
        for ref in iter_imports(path):
            for candidate in _candidate_modules(ref):
                hit = _match_prefix(candidate, forbidden)
                if hit is not None:
                    violations.append(f"{ref.where()}  ← 命中禁用前缀 {hit!r}")
                    break
    return violations


def _sibling_targets(ref: ImportRef) -> set[str]:
    """从一条 import 里抽出「被 import 的模块名（首段）」。"""
    first = ref.module.split(".")[0] if ref.module else ""
    targets: set[str] = set()
    if first:
        targets.add(first)
    # from case_runner.capabilities.simcon import X / import case_runner.capabilities.simcon
    parts = ref.module.split(".") if ref.module else []
    if "capabilities" in parts:
        idx = parts.index("capabilities")
        if idx + 1 < len(parts):
            targets.add(parts[idx + 1])
    # from . import simcon
    if ref.level and not ref.module:
        targets.update(ref.names)
    return targets


def find_sibling_imports(capabilities_dir: Path) -> list[str]:
    """铁律 2 的检查体：适配器之间互相 import 即违规。"""
    files = [p for p in iter_py_files(capabilities_dir) if is_adapter_file(p)]
    siblings = {p.stem for p in files}
    violations: list[str] = []
    for path in files:
        own = path.stem
        for ref in iter_imports(path):
            for target in sorted(_sibling_targets(ref)):
                if target in siblings and target != own:
                    violations.append(
                        f"{ref.where()}  ← 适配器之间互相 import：{own} → {target}"
                    )
    return violations


# --- 铁律前置：目录与文件确实存在（否则断言是空的） ---------------------------


def test_core_dir_has_expected_modules() -> None:
    names = {p.name for p in iter_py_files(CORE_DIR)}
    assert {
        "__init__.py",
        "models.py",
        "registry.py",
        "planner.py",
        "executor.py",
    } <= names, f"core 目录内容不符合预期：{sorted(names)}"


def test_core_dir_exists() -> None:
    assert CORE_DIR.is_dir(), f"缺少核心目录 {CORE_DIR}"
    assert iter_py_files(CORE_DIR), "core 目录下没有任何 .py，铁律 1 会变成空断言"


# --- 铁律 1 ------------------------------------------------------------------


def test_law1_core_imports_no_capabilities_and_no_domain_libs() -> None:
    violations = find_forbidden_imports(CORE_DIR, CORE_FORBIDDEN)
    assert violations == [], (
        "铁律 1 破坏：core/ 必须零领域依赖（只允许 stdlib / pydantic / test_automation 模型契约）\n"
        + "\n".join(violations)
    )


# --- 铁律 2 ------------------------------------------------------------------


def test_law2_capabilities_do_not_import_each_other() -> None:
    if not CAPABILITIES_DIR.is_dir():
        pytest.skip("capabilities/ 尚未创建（由能力层并行开发），铁律 2 暂无可查对象")
    adapters = [p for p in iter_py_files(CAPABILITIES_DIR) if is_adapter_file(p)]
    if not adapters:
        pytest.skip("capabilities/ 目前没有适配器文件（并行开发中），铁律 2 暂无可查对象")
    violations = find_sibling_imports(CAPABILITIES_DIR)
    assert violations == [], (
        "铁律 2 破坏：capabilities/*.py 之间必须零 import\n" + "\n".join(violations)
    )


# --- 铁律 3 ------------------------------------------------------------------


def test_law3_case_runner_imports_no_apps() -> None:
    violations = find_forbidden_imports(CASE_RUNNER_ROOT, APPS_FORBIDDEN)
    assert violations == [], (
        "铁律 3 破坏：libs/case_runner/ 下不得出现 from apps / import apps\n"
        + "\n".join(violations)
    )


def test_law3_libs_imports_no_apps() -> None:
    """沿用 v2 铁律 1：整个 libs/ 不得 import apps/。"""
    violations = find_forbidden_imports(LIBS_ROOT, APPS_FORBIDDEN)
    assert violations == [], (
        "libs/ 整体不得 import apps/\n" + "\n".join(violations)
    )


# --- 自检：门禁不是摆设（临时目录造违规文件，断言逻辑必须报错） ---------------


def test_selfcheck_law1_detects_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad_core.py"
    bad.write_text(
        "from parser_lib import facade\n"
        "import sim_concentrator.api\n"
        "from ..capabilities import simcon\n"
        "def later():\n"
        "    import apps.workbench\n",
        encoding="utf-8",
    )
    (tmp_path / "rel_pkg.py").write_text(
        "from . import capabilities\n", encoding="utf-8"
    )
    good = tmp_path / "good_core.py"
    good.write_text(
        "import json\n"
        "from dataclasses import dataclass\n"
        "from test_automation.models import AssertionResult\n"
        "from case_runner.core.models import BlockSpec\n"
        "from . import registry\n",
        encoding="utf-8",
    )
    violations = find_forbidden_imports(tmp_path, CORE_FORBIDDEN)
    assert len(violations) == 5, f"应命中 5 条违规（含函数内 import、含相对 import），实际：{violations}"
    assert sum(1 for v in violations if bad.as_posix() in v) == 4
    assert any("rel_pkg.py" in v and "capabilities" in v for v in violations)
    assert not any("good_core.py" in v for v in violations), "合规文件不得误报"
    assert find_forbidden_imports(tmp_path / "good_only", CORE_FORBIDDEN) == []


def test_selfcheck_law2_detects_sibling_import(tmp_path: Path) -> None:
    (tmp_path / "simcon.py").write_text("NAME = 'simcon'\n", encoding="utf-8")
    (tmp_path / "simeter.py").write_text("NAME = 'simeter'\n", encoding="utf-8")
    clean = find_sibling_imports(tmp_path)
    assert clean == [], f"零 import 的适配器不得报违规：{clean}"

    (tmp_path / "frames.py").write_text(
        "from .simcon import NAME\n", encoding="utf-8"
    )
    violations = find_sibling_imports(tmp_path)
    assert len(violations) == 1, f"同级 import 必须被抓住：{violations}"
    assert "frames" in violations[0] and "simcon" in violations[0]

    (tmp_path / "frames.py").write_text(
        "import case_runner.capabilities.simeter as m\n", encoding="utf-8"
    )
    violations = find_sibling_imports(tmp_path)
    assert len(violations) == 1, f"绝对路径同级 import 必须被抓住：{violations}"

    (tmp_path / "frames.py").write_text(
        "from . import simcon, simeter\n", encoding="utf-8"
    )
    violations = find_sibling_imports(tmp_path)
    assert len(violations) == 2, f"from . import a, b 必须被抓住：{violations}"

    # 测试文件与包入口 import 适配器是正常的，不得误报
    (tmp_path / "frames.py").write_text("NAME = 'frames'\n", encoding="utf-8")
    (tmp_path / "test_capabilities.py").write_text(
        "from .simcon import NAME\n", encoding="utf-8"
    )
    (tmp_path / "__init__.py").write_text("from . import simeter\n", encoding="utf-8")
    assert find_sibling_imports(tmp_path) == []


def test_selfcheck_law2_skips_missing_or_empty_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert find_sibling_imports(missing) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_sibling_imports(empty) == []
    (empty / "__init__.py").write_text("", encoding="utf-8")
    assert find_sibling_imports(empty) == []


def test_selfcheck_law3_detects_apps_import(tmp_path: Path) -> None:
    (tmp_path / "leak.py").write_text(
        "import apps.workbench.app\n", encoding="utf-8"
    )
    assert len(find_forbidden_imports(tmp_path, APPS_FORBIDDEN)) == 1
    (tmp_path / "leak.py").write_text(
        "from apps import workbench\n", encoding="utf-8"
    )
    assert len(find_forbidden_imports(tmp_path, APPS_FORBIDDEN)) == 1
    (tmp_path / "leak.py").write_text(
        "import json\nfrom case_runner.core import models\n", encoding="utf-8"
    )
    assert find_forbidden_imports(tmp_path, APPS_FORBIDDEN) == []
