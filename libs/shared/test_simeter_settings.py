"""模拟电表设置持久化（``data/runtime/simeter_settings.json``）单测（REQS-0042 / S1.6）。

现场缺陷（用户现场）：
    模拟电表**没有持久化设置**（对比 simcon 有 ``data/runtime/simcon_settings.json``），
    ``MeterBrain.__init__(addr="000000000001")`` 是硬编码默认值。工作台重启后，
    现场配好的表地址（``123456789012``）直接丢回 ``000000000001``，STA「搜表」阶段
    取不到表，链路失败。

本层只做「存/取/归一」，默认值一律从 ``shared.bench_defaults`` 取，**绝不硬编码**
``000000000001``。全部走 ``tmp_path``。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from shared import bench_defaults
from shared import simeter_settings
from shared.simeter_settings import (
    SimeterSettingsStore,
    default_settings,
    load_settings,
    save_settings,
)


def _bump_mtime(path: pathlib.Path) -> None:
    """把 mtime 往后推 1 秒（同长度 JSON 在同一 tick 内先后写入也能被缓存识别）。"""

    import os

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


# --------------------------------------------------------------------------- #
# 1. 默认值来自 bench_defaults（不是 000000000001）
# --------------------------------------------------------------------------- #
def test_default_settings_follow_bench_defaults(tmp_path: pathlib.Path) -> None:
    """``default_settings()`` 与 ``bench_defaults.simeter_addr()`` 同源。"""

    bench_defaults.load(refresh=True)
    settings = default_settings()
    assert settings["addr"] == bench_defaults.simeter_addr()
    assert settings["addr"] == "123456789012"
    assert settings["addr"] != "000000000001"
    assert settings["version"] == "2007"


def test_default_settings_follow_config_change(tmp_path, monkeypatch) -> None:
    """改 ``config/case_defaults.json`` 的 simeter.addr → 默认设置跟着变。"""

    config = tmp_path / "case_defaults.json"
    config.write_text(json.dumps({"meter_addr": "123456789012",
                                  "simeter": {"addr": "888888888888"}}),
                      encoding="utf-8")
    monkeypatch.setattr(bench_defaults, "DEFAULTS_FILE", config)
    bench_defaults.load(refresh=True)
    assert default_settings()["addr"] == "888888888888"


def test_missing_file_returns_defaults(tmp_path: pathlib.Path) -> None:
    store = SimeterSettingsStore(tmp_path)
    assert store.load() == default_settings()
    assert not store.path.exists(), "只读不应落盘"


# --------------------------------------------------------------------------- #
# 2. 写 → 读往返（原子替换，不留临时文件）
# --------------------------------------------------------------------------- #
def test_save_and_reload(tmp_path: pathlib.Path) -> None:
    store = SimeterSettingsStore(tmp_path)
    store.save({"addr": "123456789012", "version": "1997"})
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["addr"] == "123456789012"
    assert payload["version"] == "1997"

    # 新实例（模拟重启）读到的是持久化值
    assert SimeterSettingsStore(tmp_path).load() == {"addr": "123456789012",
                                                     "version": "1997"}
    assert load_settings(tmp_path)["addr"] == "123456789012"
    # 原子替换不留临时文件
    assert sorted(p.name for p in tmp_path.iterdir()) == ["simeter_settings.json"]


def test_save_fills_missing_keys_with_defaults(tmp_path: pathlib.Path) -> None:
    store = SimeterSettingsStore(tmp_path)
    store.save({"addr": "999999999999"})
    settings = store.load()
    assert settings["addr"] == "999999999999"
    assert settings["version"] == default_settings()["version"]


def test_save_creates_runtime_dir(tmp_path: pathlib.Path) -> None:
    runtime = tmp_path / "runtime" / "nested"
    save_settings({"addr": "123456789012"}, runtime)
    assert (runtime / "simeter_settings.json").is_file()


# --------------------------------------------------------------------------- #
# 3. 坏 JSON / 非法字段 → 回默认，不抛异常
# --------------------------------------------------------------------------- #
def test_corrupt_json_returns_defaults(tmp_path: pathlib.Path) -> None:
    store = SimeterSettingsStore(tmp_path)
    store.path.write_text("{ 这不是 JSON", encoding="utf-8")
    assert store.load() == default_settings()
    assert load_settings(tmp_path) == default_settings()


@pytest.mark.parametrize("bad_addr", ["", "abc", "12345", "0000000000012", None, 123, []])
def test_illegal_addr_falls_back_to_default(tmp_path: pathlib.Path, bad_addr) -> None:
    store = SimeterSettingsStore(tmp_path)
    store.runtime_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"addr": bad_addr, "version": "2007"}),
                          encoding="utf-8")
    assert store.load()["addr"] == default_settings()["addr"]


def test_illegal_version_falls_back_to_default(tmp_path: pathlib.Path) -> None:
    store = SimeterSettingsStore(tmp_path)
    store.runtime_dir.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"addr": "123456789012", "version": "25"}),
                          encoding="utf-8")
    assert store.load()["version"] == default_settings()["version"]


# --------------------------------------------------------------------------- #
# 4. 默认运行目录 = <repo>/data/runtime（冻结态 exe 同级 runtime）
# --------------------------------------------------------------------------- #
def test_default_runtime_dir_is_repo_data_runtime(monkeypatch) -> None:
    """默认目录由 ``default_runtime_dir()`` 决定（测试可用 monkeypatch 指走）。"""

    repo_runtime = simeter_settings.default_runtime_dir()
    assert repo_runtime == pathlib.Path(simeter_settings.__file__).resolve().parents[2] \
        / "data" / "runtime"
    assert SimeterSettingsStore().path == repo_runtime / "simeter_settings.json"


def test_no_hardcoded_engine_default() -> None:
    """本层代码里不得出现 ``000000000001``（引擎类默认值只能在 MeterBrain 里）。

    只查**代码里的字符串常量**（文档字符串可以引用现场缺陷，不算硬编码）。
    """

    import ast

    tree = ast.parse(pathlib.Path(simeter_settings.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            assert "000000000001" not in node.value, node.value
    # 默认值确实来自 bench_defaults
    assert default_settings()["addr"] == bench_defaults.simeter_addr()
