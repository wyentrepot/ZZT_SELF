"""simcon 全局设置（data/runtime/simcon_settings.json）单测。

现场口径（2026-11-19，10H-F2 查询从节点信息）：

    请求 start=0 → 68 12 00 43 ... 10 02 00 | 00 00 10 | 6D 16
    响应        → 68 12 00 83 ... 10 02 00 | 03 00 00 | A0 16   ← 总数量3、本次应答0（空壳）
    请求 start=1 → 68 12 00 43 ... 10 02 00 | 01 00 10 | 6F 16
    响应        → 68 2A 00 83 ... 10 02 00 | 03 00 03 | <3 条记录> | ED 16

唯一差别是「从节点起始序号」2 字节小端。故引入 query_start_base 全局开关（默认 1）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.simcon_settings import (
    DEFAULTS,
    SimconSettingsStore,
    load_settings,
    query_start_base,
)


def test_default_is_base_one(tmp_path: Path):
    """无文件时默认 1——本场模块是 1 起编号。"""
    store = SimconSettingsStore(tmp_path)
    assert store.load() == DEFAULTS
    assert store.query_start_base() == 1
    assert not store.path.exists(), "只读不应落盘"


def test_save_and_reload(tmp_path: Path):
    store = SimconSettingsStore(tmp_path)
    store.save({"query_start_base": 0})
    assert json.loads(store.path.read_text(encoding="utf-8"))["query_start_base"] == 0
    assert SimconSettingsStore(tmp_path).query_start_base() == 0
    store.save({"query_start_base": 1})
    assert query_start_base(tmp_path) == 1


@pytest.mark.parametrize("bad", [99, -1, "x", None, [], {}])
def test_illegal_base_falls_back_to_default(tmp_path: Path, bad):
    """非法基址（越界/非数字）退回默认 1，不抛异常、不写坏文件。"""
    store = SimconSettingsStore(tmp_path)
    store.runtime_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"query_start_base": bad}), encoding="utf-8")
    assert store.query_start_base() == DEFAULTS["query_start_base"]


def test_corrupt_file_degrades_silently(tmp_path: Path):
    """文件损坏 → 默认值，不阻塞业务（设置层永远是增强）。"""
    store = SimconSettingsStore(tmp_path)
    store.runtime_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() == DEFAULTS
    assert load_settings(tmp_path) == DEFAULTS


def test_public_has_labels_for_ui(tmp_path: Path):
    pub = SimconSettingsStore(tmp_path).public()
    assert pub["settings"]["query_start_base"] == 1
    assert "query_start_base" in pub["labels"]
    assert pub["defaults"] == DEFAULTS


def test_pagination_start_offsets():
    """recipe 分页起点：base=1 → 1,17,33…；base=0 → 0,16,32…"""
    from sim_concentrator.recipes import MAX_BATCH

    def starts(base):
        return [off + (1 if base == 1 else 0) for off in range(0, 64, MAX_BATCH)]

    assert starts(1) == [1, 17, 33, 49]
    assert starts(0) == [0, 16, 32, 48]
