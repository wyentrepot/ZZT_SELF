"""原子块包（REQS-0041 §10.2 Block 层）：领域块模块 + JSON 声明加载器。

本 ``__init__`` 只有一个职责：**自动发现并导入同目录下的领域模块**，让各模块在 import
时完成 ``register_block`` / ``register_fact``。这样新增 ``archive.py`` / ``nwk.py`` /
``meter.py`` 时**无需改本文件**（并行开发互不阻塞）。

三条设计约束：

1. **容错**：某个领域模块 import 失败只发 :class:`ModuleImportWarning`，不影响其他模块
   （并行开发期别的模块可能正在被写，语法瞬时错误不该让整包不可用）；失败清单留在
   :data:`IMPORT_FAILURES` 里，:func:`reload_domain_modules` / :func:`import_failures`
   可取。
2. **目录为空不报错**：``iter_modules`` 返回空列表时直接返回。
3. **不导入 ``loader``**：加载器是工具模块而非领域模块，由调用方显式
   ``from case_runner.blocks import loader``；其对外函数在本模块用 ``__getattr__``
   懒导出（``case_runner.blocks.load_blocks(...)`` 同样可用），避免包初始化时就被
   pydantic / 数据目录拖进来。

注册幂等约定：领域模块除 import 时注册外，建议再暴露一个**幂等**的 ``register()``
（内部先 ``has_block``/``has_fact`` 判存在），便于测试在 ``clear_registries()`` 之后
重新注册（见 ``blocks/fix.py``）。
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import warnings
from typing import Any

__all__ = [
    "IMPORT_FAILURES",
    "ModuleImportWarning",
    "iter_domain_modules",
    "import_domain_modules",
    "reload_domain_modules",
    "import_failures",
    "loader",
]

#: ``(模块名, 异常描述)``：最近一次自动导入失败的领域模块
IMPORT_FAILURES: list[tuple[str, str]] = []

#: 不算领域模块的文件名 / 前缀（包入口不会被 ``iter_modules`` 列出）
_NON_DOMAIN = frozenset({"loader", "conftest"})
_NON_DOMAIN_PREFIXES = ("test_", "_")

#: 懒导出的加载器对外函数
_LOADER_EXPORTS = frozenset(
    {
        "DATA_DIR",
        "DeclarationError",
        "LoadReport",
        "RegisterReport",
        "load_blocks",
        "load_facts",
        "load_cases",
        "load_suites",
        "load_all",
        "register_domain_modules",
        "iter_declarations",
        "resolve_dir",
    }
)


class ModuleImportWarning(UserWarning):
    """领域模块导入失败（已降级，不影响其他模块）。"""


def iter_domain_modules(package: str = __name__) -> list[str]:
    """列 ``package`` 下的领域模块名（跳过 ``__init__``/``loader``/``test_*``/私有模块）。"""
    module = sys.modules.get(package) or importlib.import_module(package)
    paths = getattr(module, "__path__", None)
    if not paths:
        return []
    names: list[str] = []
    for info in pkgutil.iter_modules(list(paths)):
        name = info.name
        if name in _NON_DOMAIN or name.startswith(_NON_DOMAIN_PREFIXES):
            continue
        names.append(name)
    return sorted(names)


def import_domain_modules(*, package: str = __name__, warn: bool = True) -> list[str]:
    """导入全部领域模块，返回导入成功的模块全名。

    单个模块失败 → 记入 :data:`IMPORT_FAILURES` 并告警（``warn=True``），不抛出。
    """
    imported: list[str] = []
    for name in iter_domain_modules(package):
        full = f"{package}.{name}"
        try:
            importlib.import_module(full)
        except Exception as exc:  # noqa: BLE001 - 契约：容错，不影响其他模块
            reason = f"{type(exc).__name__}: {exc}"
            IMPORT_FAILURES.append((full, reason))
            if warn:
                warnings.warn(
                    f"领域模块导入失败，已跳过：{full}（{reason}）",
                    ModuleImportWarning,
                    stacklevel=2,
                )
            continue
        imported.append(full)
    return imported


def reload_domain_modules(*, package: str = __name__, warn: bool = False) -> list[str]:
    """重新 import 已加载的领域模块（``clear_registries()`` 后重新注册用）。"""
    IMPORT_FAILURES.clear()
    return import_domain_modules(package=package, warn=warn)


def import_failures() -> list[tuple[str, str]]:
    """最近一次自动导入失败的 ``(模块名, 原因)`` 列表（成功则为空）。"""
    return list(IMPORT_FAILURES)


def __getattr__(name: str) -> Any:
    if name == "loader" or name in _LOADER_EXPORTS:
        module = importlib.import_module(f"{__name__}.loader")
        if name == "loader":
            return module
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | _LOADER_EXPORTS)


# 包导入即发现领域模块（容错：失败只告警）
import_domain_modules()
