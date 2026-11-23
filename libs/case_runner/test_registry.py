"""P1-2 注册表测试（PLAN P1-2）。

注册的是**执行体**（可调用），声明是 JSON（``data/blocks/*.json``），两者按 id 对应。
语义定死：重复注册 → ``Duplicate``（不静默覆盖），未注册 → ``NotFound`` 且消息含 id。
"""

from __future__ import annotations

import pytest

from case_runner.core.registry import (
    BLOCK_REGISTRY,
    FACT_REGISTRY,
    Duplicate,
    NotFound,
    clear_registries,
    get_block,
    get_fact,
    has_block,
    has_fact,
    list_blocks,
    list_facts,
    register_block,
    register_fact,
)


@pytest.fixture(autouse=True)
def _isolate() -> None:
    clear_registries()
    yield
    clear_registries()


def test_register_and_get_block_roundtrip() -> None:
    def runner(params, ctx):
        return {"ok": True}

    register_block("nwk.onnet_count", runner)
    assert get_block("nwk.onnet_count") is runner
    assert has_block("nwk.onnet_count") is True
    assert "nwk.onnet_count" in BLOCK_REGISTRY


def test_register_and_get_fact_roundtrip() -> None:
    def probe(params):
        return "yes"

    register_fact("archive.contains", probe)
    assert get_fact("archive.contains") is probe
    assert has_fact("archive.contains") is True
    assert "archive.contains" in FACT_REGISTRY


def test_get_unknown_block_raises_not_found_with_id() -> None:
    with pytest.raises(NotFound) as exc:
        get_block("does.not.exist")
    assert "does.not.exist" in str(exc.value)


def test_get_unknown_fact_raises_not_found_with_id() -> None:
    with pytest.raises(NotFound) as exc:
        get_fact("does.not.exist")
    assert "does.not.exist" in str(exc.value)


def test_duplicate_block_registration_raises() -> None:
    register_block("dup.block", lambda params, ctx: None)
    with pytest.raises(Duplicate) as exc:
        register_block("dup.block", lambda params, ctx: None)
    assert "dup.block" in str(exc.value)
    # 首次注册的仍是生效的那个
    assert get_block("dup.block") is not None


def test_duplicate_fact_registration_raises() -> None:
    register_fact("dup.fact", lambda params: "yes")
    with pytest.raises(Duplicate):
        register_fact("dup.fact", lambda params: "yes")


def test_empty_id_and_non_callable_rejected() -> None:
    with pytest.raises(ValueError):
        register_block("", lambda params, ctx: None)
    with pytest.raises(TypeError):
        register_block("bad.block", "not callable")  # type: ignore[arg-type]


def test_block_and_fact_namespaces_are_separate() -> None:
    register_block("same.name", lambda params, ctx: None)
    register_fact("same.name", lambda params: "yes")
    assert get_block("same.name") is not get_fact("same.name")


def test_list_and_clear() -> None:
    register_block("b.one", lambda params, ctx: None)
    register_block("b.two", lambda params, ctx: None)
    register_fact("f.one", lambda params: "yes")
    assert list_blocks() == ["b.one", "b.two"]
    assert list_facts() == ["f.one"]
    clear_registries()
    assert list_blocks() == [] and list_facts() == []
    assert has_block("b.one") is False
