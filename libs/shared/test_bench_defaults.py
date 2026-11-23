"""台架默认值单一来源（``libs/shared/bench_defaults.py``）单测（REQS-0042 / S1.6）。

现场缺陷（用户原话）：
    「000000000001 这个默认地址，应该与 CCO 档案默认表地址一致才对，
      这个复位就恢复 000000000001 不应该」

所以默认地址只有一个来源：``config/case_defaults.json``（现场层）。
用例默认（``case_runner.core.defaults`` → 同文件的 ``meter_addr``）与模拟电表
默认（``simeter_settings.default_settings``）都从这里取，不允许各自写死。

本文件只测这一层：读盘/缓存/取键/回落。全部走 ``tmp_path``，不碰仓库真 config/。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from shared import bench_defaults

#: 仓库根（``libs/shared/test_bench_defaults.py`` 上溯 3 层）
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture
def defaults_file(tmp_path, monkeypatch) -> pathlib.Path:
    """把 ``DEFAULTS_FILE`` 指到 ``tmp_path``（不碰仓库真 config/case_defaults.json）。"""

    path = tmp_path / "case_defaults.json"
    monkeypatch.setattr(bench_defaults, "DEFAULTS_FILE", path)
    bench_defaults.load(refresh=True)
    yield path
    bench_defaults.load(refresh=True)


def _write(path: pathlib.Path, payload: dict) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _bump_mtime(path: pathlib.Path) -> None:
    """把 mtime 往后推 1 秒，模拟「两个同长度 JSON 在同一 tick 内先后写入」。"""

    stat = path.stat()
    path.touch()
    path.stat()
    import os

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


# --------------------------------------------------------------------------- #
# 1. 路径与分层
# --------------------------------------------------------------------------- #
def test_defaults_file_is_repo_config() -> None:
    """唯一来源 = ``<repo>/config/case_defaults.json``（目录层级 parents[2]）。"""

    assert bench_defaults.REPO_ROOT == REPO_ROOT
    assert bench_defaults.DEFAULTS_FILE == REPO_ROOT / "config" / "case_defaults.json"


def test_module_imports_only_stdlib() -> None:
    """本层只允许标准库：不得 import apps.* / sim_meter.* / case_runner.*。"""

    text = (pathlib.Path(bench_defaults.__file__)).read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for forbidden in ("apps", "sim_meter", "case_runner", "sim_concentrator"):
            assert forbidden not in stripped.split()[1], stripped
        assert "from shared" not in stripped, stripped   # 本模块是 shared 的最底层


# --------------------------------------------------------------------------- #
# 2. 读不到是空，不抛异常
# --------------------------------------------------------------------------- #
def test_missing_file_returns_empty(defaults_file: pathlib.Path) -> None:
    assert not defaults_file.exists()
    assert bench_defaults.load() == {}
    assert bench_defaults.meter_addr() == ""
    assert bench_defaults.meter_addr("兜底") == "兜底"
    assert bench_defaults.simeter_addr() == ""
    assert bench_defaults.simeter_addr("兜底") == "兜底"


def test_broken_json_returns_empty(defaults_file: pathlib.Path) -> None:
    defaults_file.write_text("{ 这不是 JSON", encoding="utf-8")
    assert bench_defaults.load(refresh=True) == {}
    assert bench_defaults.meter_addr("fallback") == "fallback"


def test_non_object_json_returns_empty(defaults_file: pathlib.Path) -> None:
    defaults_file.write_text('["不是对象"]', encoding="utf-8")
    assert bench_defaults.load(refresh=True) == {}


# --------------------------------------------------------------------------- #
# 3. 改 config → 取值跟着变（缓存按 mtime + size 失效，不需要手动 refresh）
# --------------------------------------------------------------------------- #
def test_meter_addr_follows_file_change(defaults_file: pathlib.Path) -> None:
    _write(defaults_file, {"meter_addr": "123456789012"})
    assert bench_defaults.meter_addr() == "123456789012"

    # 同一长度、紧随其后写入：靠 mtime 失效（_bump_mtime 保证签名不同）
    _write(defaults_file, {"meter_addr": "999999999999"})
    _bump_mtime(defaults_file)
    assert bench_defaults.meter_addr() == "999999999999"

    # 删文件 → 回到空
    defaults_file.unlink()
    assert bench_defaults.meter_addr() == ""


def test_refresh_forces_reread(defaults_file: pathlib.Path) -> None:
    _write(defaults_file, {"meter_addr": "111111111111"})
    assert bench_defaults.load()["meter_addr"] == "111111111111"
    _write(defaults_file, {"meter_addr": "222222222222"})
    assert bench_defaults.load(refresh=True)["meter_addr"] == "222222222222"


# --------------------------------------------------------------------------- #
# 4. simeter 段优先，缺了回落 meter_addr
# --------------------------------------------------------------------------- #
def test_simeter_addr_prefers_simeter_block(defaults_file: pathlib.Path) -> None:
    _write(defaults_file, {"meter_addr": "123456789012",
                           "simeter": {"addr": "999999999999", "version": "1997"}})
    assert bench_defaults.simeter_addr() == "999999999999"
    assert bench_defaults.meter_addr() == "123456789012"    # 顶层键不受影响


def test_simeter_addr_falls_back_to_meter_addr(defaults_file: pathlib.Path) -> None:
    _write(defaults_file, {"meter_addr": "123456789012"})
    assert bench_defaults.simeter_addr() == "123456789012"

    _write(defaults_file, {"meter_addr": "123456789012", "simeter": {"addr": ""}})
    assert bench_defaults.simeter_addr() == "123456789012"

    # 顶层也没有 → 兜底值
    _write(defaults_file, {"simeter": {}})
    assert bench_defaults.simeter_addr("兜底") == "兜底"


def test_simeter_version(defaults_file: pathlib.Path) -> None:
    _write(defaults_file, {"simeter": {"addr": "123456789012", "version": "97"}})
    assert bench_defaults.simeter_version() == "1997"
    _write(defaults_file, {"simeter": {"addr": "123456789012", "version": "2007"}})
    assert bench_defaults.simeter_version() == "2007"
    _write(defaults_file, {"simeter": {"addr": "123456789012", "version": "25"}})
    assert bench_defaults.simeter_version() == "2007"       # 非法版本回落
    _write(defaults_file, {"simeter": {"addr": "123456789012"}})
    assert bench_defaults.simeter_version() == "2007"


# --------------------------------------------------------------------------- #
# 5. 真 config（现场层）：模拟电表默认地址 = 用例默认地址
# --------------------------------------------------------------------------- #
def test_real_config_is_the_bench_addr() -> None:
    """仓库真 config：模拟电表默认地址与档案/用例默认表地址一致（不是 000000000001）。"""

    bench_defaults.load(refresh=True)
    real = bench_defaults.meter_addr()
    assert real == "123456789012"
    assert bench_defaults.simeter_addr() == real
    assert bench_defaults.simeter_version() == "2007"
    # 缺省回落到 MeterBrain 类默认（引擎层）只在 config 读不到时才发生
    assert bench_defaults.simeter_addr() != "000000000001"


def test_load_returns_copy(defaults_file: pathlib.Path) -> None:
    """``load()`` 返回副本：调用方改它不能污染缓存。"""

    _write(defaults_file, {"meter_addr": "123456789012", "simeter": {"addr": "123456789012"}})
    data = bench_defaults.load(refresh=True)
    data["meter_addr"] = "000000000000"
    data["simeter"]["addr"] = "000000000000"
    assert bench_defaults.meter_addr() == "123456789012"
    assert bench_defaults.simeter_addr() == "123456789012"
