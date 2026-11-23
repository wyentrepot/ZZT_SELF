"""块/事实执行体注册表（PLAN P1-2）。

声明是 JSON（``data/blocks/*.json``、``data/facts/*.json``），执行体是 Python 可调用，
两者**按 id 对应**。注册表只存执行体，不含任何声明或领域逻辑。

语义（已由 ``test_registry.py`` 固定）：
- 重复注册同一 id → :class:`Duplicate`（**不静默覆盖**，避免并行开发时块互相盖掉）
- 未注册 id → :class:`NotFound`，消息含该 id
- ``clear_registries()`` 供测试隔离

调用约定：
- 块执行体：``fn(params: dict, ctx) -> BlockResult | AssertionResult | dict | bool | str | None``
- 事实探测体：``fn(params: dict) -> FactValue | "yes"|"no"|"unknown" | bool | dict | None``
"""

from __future__ import annotations

from typing import Any, Callable

#: 注册的**执行体**类型（不做 runtime 校验，只用别名表达意图）
BlockExec = Callable[..., Any]
FactProbe = Callable[..., Any]


class NotFound(LookupError):
    """按 id 找不到已注册的执行体。"""


class Duplicate(ValueError):
    """同一 id 重复注册。"""


BLOCK_REGISTRY: dict[str, BlockExec] = {}
FACT_REGISTRY: dict[str, FactProbe] = {}


def _register(
    registry: dict[str, Callable[..., Any]],
    kind: str,
    ident: str,
    fn: Callable[..., Any],
) -> None:
    if not isinstance(ident, str) or not ident.strip():
        raise ValueError(f"{kind} id 必须是非空字符串，收到 {ident!r}")
    if not callable(fn):
        raise TypeError(f"{kind} {ident!r} 的执行体必须可调用，收到 {type(fn).__name__}")
    if ident in registry:
        raise Duplicate(
            f"{kind} 已注册：{ident!r}"
            "（重复注册默认报错；测试/重载场景请先调用 clear_registries()）"
        )
    registry[ident] = fn


def register_block(block_id: str, fn: BlockExec) -> None:
    """注册块执行体 ``fn(params, ctx)``。"""
    _register(BLOCK_REGISTRY, "块", block_id, fn)


def register_fact(fact_id: str, fn: FactProbe) -> None:
    """注册事实探测体 ``fn(params)``。"""
    _register(FACT_REGISTRY, "事实", fact_id, fn)


def get_block(block_id: str) -> BlockExec:
    try:
        return BLOCK_REGISTRY[block_id]
    except KeyError:
        raise NotFound(f"块未注册：{block_id!r}（已注册：{list_blocks()}）") from None


def get_fact(fact_id: str) -> FactProbe:
    try:
        return FACT_REGISTRY[fact_id]
    except KeyError:
        raise NotFound(f"事实未注册：{fact_id!r}（已注册：{list_facts()}）") from None


def has_block(block_id: str) -> bool:
    return block_id in BLOCK_REGISTRY


def has_fact(fact_id: str) -> bool:
    return fact_id in FACT_REGISTRY


def list_blocks() -> list[str]:
    """已注册块 id（注册顺序）。"""
    return list(BLOCK_REGISTRY)


def list_facts() -> list[str]:
    """已注册事实 id（注册顺序）。"""
    return list(FACT_REGISTRY)


def unregister_block(block_id: str) -> None:
    """移除块执行体（不存在则无操作）。"""
    BLOCK_REGISTRY.pop(block_id, None)


def unregister_fact(fact_id: str) -> None:
    """移除事实探测体（不存在则无操作）。"""
    FACT_REGISTRY.pop(fact_id, None)


def clear_registries() -> None:
    """清空两张注册表（测试隔离用）。"""
    BLOCK_REGISTRY.clear()
    FACT_REGISTRY.clear()


__all__ = [
    "BlockExec",
    "FactProbe",
    "NotFound",
    "Duplicate",
    "BLOCK_REGISTRY",
    "FACT_REGISTRY",
    "register_block",
    "register_fact",
    "unregister_block",
    "unregister_fact",
    "get_block",
    "get_fact",
    "has_block",
    "has_fact",
    "list_blocks",
    "list_facts",
    "clear_registries",
]
