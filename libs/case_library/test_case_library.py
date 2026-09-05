"""libs.case_library 单测（REQS-0025 G1）。

校验对象是随库分发的 data/cases.json 与加载器：条目结构完整性、id 唯一、
分类合法、来源可追溯、meta 计数与实际一致。不依赖蒸馏库路径（再生成属工具行为）。
"""
from __future__ import annotations

import json

import pytest

from libs.case_library import library


@pytest.fixture(scope="module")
def lib() -> dict:
    return library.load_library()


def test_meta_declared_and_counts(lib):
    meta = lib["meta"]
    assert meta["declared"]["total"] == 269
    assert meta["source"]["distill"] == "蒸馏/06_测试用例.md"
    assert "南网" not in meta["red_line"] or "未引用" in meta["red_line"]
    counts = meta["counts"]
    assert counts["entries"] == len(lib["entries"])
    assert counts["cases"] == sum(1 for e in lib["entries"] if e["entry_type"] == "case")
    assert counts["param_rows"] == sum(1 for e in lib["entries"] if e["entry_type"] == "param_table")


def test_entry_ids_unique_and_schema(lib):
    ids = [e["id"] for e in lib["entries"]]
    assert len(ids) == len(set(ids)), "条目 id 重复"
    required = {"id", "entry_type", "category", "group", "name", "protocol",
                "detail_level", "source"}
    valid_cats = {c["id"] for c in lib["categories"]}
    for e in lib["entries"]:
        assert required <= set(e), f"{e['id']} 缺字段：{required - set(e)}"
        assert e["entry_type"] in ("case", "param_table")
        assert e["category"] in valid_cats, f"{e['id']} 分类非法：{e['category']}"
        assert e["detail_level"] in ("detailed", "framework")
        src = e["source"]
        assert src.get("doc") and src.get("distill") and src.get("section"), f"{e['id']} 来源不可追溯"


def test_declared_perf_counts_match_source(lib):
    """§2.2 清单声明的性能条目数：HPLC 7 / 无线 11 / 互操作 11。"""
    by_cat = {}
    for e in lib["entries"]:
        by_cat[e["category"]] = by_cat.get(e["category"], 0) + 1
    assert by_cat["hplc-perf"] == 7
    assert by_cat["wireless-perf"] == 11
    assert by_cat["interop"] == 11


def test_param_tables_complete(lib):
    """测试模式扩展命令 1~13 与安全测试模式 1~12 参数表完整。"""
    modes = [e for e in lib["entries"] if e["id"].startswith("PARAM-MODE-")]
    secs = [e for e in lib["entries"] if e["id"].startswith("PARAM-SEC-")]
    assert sorted(int(e["fields"]["值"]) for e in modes) == list(range(1, 14))
    assert sorted(int(e["fields"]["值"]) for e in secs) == list(range(1, 13))


def test_loader_filters(lib):
    all_items = library.entries()
    assert len(all_items) == len(lib["entries"])
    assert all(e["category"] == "interop" for e in library.entries(category="interop"))
    assert all(e["entry_type"] == "param_table" for e in library.param_tables())
    hit = library.entries(q="信标")
    assert hit, "关键词过滤应能命中信标相关条目"
    one = library.get_entry("HPLC-DLL-BCN-01")
    assert one and "中央信标" in one["name"]
    assert library.get_entry("NO-SUCH-ID") is None


def test_data_file_is_valid_json_utf8():
    text = library.data_path().read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["categories"]


def test_henan_and_tester_groups_present(lib):
    names = {e["name"] for e in lib["entries"]}
    assert any("载波信道检测阶段" in n for n in names)
    assert any("设置组网方式" in n for n in names)
    assert any("虚拟表地址请求" in n for n in names)
