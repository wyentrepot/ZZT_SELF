"""地址角色化换算 + 两层默认配置的单测（REQS-0042 §3.1 / D2-1 / D2-2，S1.1）。

覆盖口径（任务书逐条对应）
==========================

1. **双向换算**：``123456789012 ↔ 129078563412``、``000000000001 ↔ 010000000000``
   两个方向都断言（``archive_addr_of`` 自反：换两次回原值）。
2. **非法输入**：``""`` / ``"abc"`` / ``"12345"`` / ``None`` → ``""`` / ``b""``，**不抛异常**。
3. **线序不反转**：``meter_addr_wire("123456789012") == bytes.fromhex("123456789012")``。
4. **两层覆盖**：``CONFIG_OVERRIDE_FILE`` 指到 ``tmp_path`` 后 ``default_meter_addr()`` 变化，
   删掉文件即恢复出厂值；``mapping_id`` 按**子键**合并（只写 simcon 时其余角色不动）。
5. **来源可诊断**：``defaults_sources()`` 说得出每个键来自哪个文件。
6. **两个文件都读不到**：返回空 dict，``default_meter_addr() == ""``，绝不抛异常。
7. **loader 解析 null**：``"meter_addr": null``（块 ``action.params`` / 事实
   ``params_schema.*.default``）在加载期被解析成真实默认值；``request_hex: null``
   这类**非地址**的 null **不得**被改写。

全部离线：不碰台架、不发 HTTP，配置覆盖一律走 ``tmp_path``。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from case_runner.blocks import common, loader
from case_runner.core import defaults

#: 本文件所在包目录（``libs/case_runner``）
PKG_DIR = pathlib.Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_defaults_cache():
    """每个用例都从干净缓存开始，用例结束也不把覆盖值留给下一个用例。"""

    defaults.load_defaults(refresh=True)
    yield
    defaults.load_defaults(refresh=True)


@pytest.fixture
def override_file(tmp_path, monkeypatch) -> pathlib.Path:
    """把 ``CONFIG_OVERRIDE_FILE`` 挪到 ``tmp_path``（不碰仓库真 config/）。"""

    path = tmp_path / "case_defaults.json"
    monkeypatch.setattr(defaults, "CONFIG_OVERRIDE_FILE", path)
    defaults.load_defaults(refresh=True)
    return path


def _write_json(path: pathlib.Path, payload: dict) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 1. 双向换算
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "meter_addr, archive_addr",
    [
        ("123456789012", "129078563412"),
        ("000000000001", "010000000000"),
    ],
)
def test_archive_addr_of_double_direction(meter_addr: str, archive_addr: str) -> None:
    """``archive_addr_of`` 是 6 字节整体反转，且换两次回到原值（双向都断言）。"""

    assert common.archive_addr_of(meter_addr) == archive_addr
    # 反向：档案侧人读地址换回去必须是电表 645 人读地址
    assert common.archive_addr_of(archive_addr) == meter_addr
    # 自反：两次换回原值
    assert common.archive_addr_of(common.archive_addr_of(meter_addr)) == meter_addr


def test_archive_addr_of_known_bench_truth() -> None:
    """台架实测口径：电表脑地址 123456789012 ↔ CCO 档案 129078563412。"""

    assert common.archive_addr_of("123456789012") == "129078563412"
    assert len(common.archive_addr_of("123456789012")) == common.ADDR_LEN


# --------------------------------------------------------------------------- #
# 2. 非法输入：返回空、绝不抛异常
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["", "abc", "12345", None, "1234567890123", "12345678901a"])
def test_archive_addr_of_invalid(bad) -> None:
    assert common.archive_addr_of(bad) == ""


@pytest.mark.parametrize("bad", ["", "abc", "12345", None, "1234567890123", "12345678901a"])
def test_meter_addr_wire_invalid(bad) -> None:
    assert common.meter_addr_wire(bad) == b""


def test_both_converters_never_raise() -> None:
    """判定/换算函数不抛异常（块层铁律）：任意怪输入都只是返回空。"""

    for weird in (object(), 3.14, [], {}, b"123456789012", True, False):
        assert common.archive_addr_of(weird) == ""
        assert common.meter_addr_wire(weird) == b""


# --------------------------------------------------------------------------- #
# 3. 线序不反转
# --------------------------------------------------------------------------- #


def test_wire_not_reversed() -> None:
    assert common.meter_addr_wire("123456789012") == bytes.fromhex("123456789012")
    assert common.meter_addr_wire("123456789012") == bytes.fromhex("123456789012")
    assert common.meter_addr_wire("000000000001") == bytes.fromhex("000000000001")
    # 与 archive_addr_of 的关系：线序是「不反转」，档案地址是「反转」
    assert common.meter_addr_wire("123456789012") != bytes.fromhex(
        common.archive_addr_of("123456789012")
    )
    assert common.meter_addr_wire("123456789012")[::-1] == bytes.fromhex(
        common.archive_addr_of("123456789012")
    )
    assert len(common.meter_addr_wire("123456789012")) == 6


def test_converters_registered_in_all() -> None:
    """两个换算函数必须进 ``common.__all__``（唯一实现处的公开契约）。"""

    assert "archive_addr_of" in common.__all__
    assert "meter_addr_wire" in common.__all__


# --------------------------------------------------------------------------- #
# 4/5. 两层默认配置：出厂值 + config 覆盖 + 来源
# --------------------------------------------------------------------------- #


def test_factory_defaults() -> None:
    """出厂默认值（0042 D2-2）：台架真实表地址 + 三个逻辑口名。"""

    assert defaults.DATA_DEFAULTS_FILE == PKG_DIR / "data" / "case_defaults.json"
    assert defaults.CONFIG_OVERRIDE_FILE.name == "case_defaults.json"
    assert defaults.default_meter_addr() == "123456789012"
    assert defaults.default_mapping_id("simcon") == "simcon"
    assert defaults.default_mapping_id("simeter") == "simeter"
    assert defaults.default_mapping_id("sta") == "sta-main"
    assert defaults.default_mapping_id("不存在的角色") == ""


def test_layers_override(override_file: pathlib.Path) -> None:
    """``config/case_defaults.json`` 按顶层键覆盖；``mapping_id`` 按子键合并。"""

    assert defaults.default_meter_addr() == "123456789012"

    _write_json(
        override_file,
        {"meter_addr": "000000000088", "mapping_id": {"simcon": "现场口"}},
    )
    assert defaults.default_meter_addr() == "000000000088"
    assert defaults.default_mapping_id("simcon") == "现场口"
    # 只覆盖 simcon，其余角色保持出厂值（按子键合并，不是整块替换）
    assert defaults.default_mapping_id("simeter") == "simeter"
    assert defaults.default_mapping_id("sta") == "sta-main"

    # 删掉覆盖文件 → 恢复出厂值（缓存按文件签名失效，不需要手动 refresh）
    override_file.unlink()
    assert defaults.default_meter_addr() == "123456789012"
    assert defaults.default_mapping_id("simcon") == "simcon"


def test_override_picks_up_after_first_read(override_file: pathlib.Path) -> None:
    """先读过一次再新增覆盖文件，也必须立刻生效（缓存不能只看「读过没」）。"""

    assert defaults.default_meter_addr() == "123456789012"
    _write_json(override_file, {"meter_addr": "111111111111"})
    assert defaults.default_meter_addr() == "111111111111"


def test_default_for_and_fallback(override_file: pathlib.Path) -> None:
    assert defaults.default_for("meter_addr") == "123456789012"
    assert defaults.default_for("查无此键") is None
    assert defaults.default_for("查无此键", "兜底") == "兜底"
    assert defaults.default_for("mapping_id.sta") == "sta-main"

    _write_json(override_file, {"meter_addr": "222222222222"})
    assert defaults.default_for("meter_addr", "兜底") == "222222222222"


def test_defaults_sources(override_file: pathlib.Path) -> None:
    """每个键都说得出自己来自哪个文件（诊断 / ``case_run show`` 用）。"""

    factory = defaults.DATA_DEFAULTS_FILE.as_posix()
    sources = defaults.defaults_sources()
    assert sources["meter_addr"] == factory
    assert sources["mapping_id.simcon"] == factory
    assert sources["mapping_id.simeter"] == factory
    assert sources["mapping_id.sta"] == factory
    # 内部说明键不算配置项
    assert "_note" not in sources

    _write_json(override_file, {"meter_addr": "333333333333", "mapping_id": {"sta": "现场STA"}})
    sources = defaults.defaults_sources()
    assert sources["meter_addr"] == override_file.as_posix()
    assert sources["mapping_id.sta"] == override_file.as_posix()
    # 没被覆盖的键仍记为出厂文件
    assert sources["mapping_id.simcon"] == factory


def test_missing_files_return_empty(tmp_path, monkeypatch) -> None:
    """两个文件都读不到 → 空 dict；``default_meter_addr()`` 返回 ``""``，不抛异常。"""

    monkeypatch.setattr(defaults, "DATA_DEFAULTS_FILE", tmp_path / "nope-data.json")
    monkeypatch.setattr(defaults, "CONFIG_OVERRIDE_FILE", tmp_path / "nope-config.json")
    assert defaults.load_defaults(refresh=True) == {}
    assert defaults.default_meter_addr() == ""
    assert defaults.default_mapping_id("sta") == ""
    assert defaults.default_for("meter_addr") is None
    assert defaults.defaults_sources() == {}


def test_broken_json_is_ignored(tmp_path, monkeypatch) -> None:
    """坏 JSON 不得炸掉加载（返回空 dict，留给 loader 报声明问题）。"""

    bad = tmp_path / "case_defaults.json"
    bad.write_text("{ 不是 JSON", encoding="utf-8")
    monkeypatch.setattr(defaults, "DATA_DEFAULTS_FILE", bad)
    monkeypatch.setattr(defaults, "CONFIG_OVERRIDE_FILE", tmp_path / "absent.json")
    assert defaults.load_defaults(refresh=True) == {}
    assert defaults.default_meter_addr() == ""


def test_invalid_default_addr_is_not_used(tmp_path, monkeypatch) -> None:
    """配置里写了非法表地址 → ``default_meter_addr()`` 返回 ``""``（不把垃圾地址发给台架）。"""

    path = tmp_path / "case_defaults.json"
    _write_json(path, {"meter_addr": "abc"})
    monkeypatch.setattr(defaults, "DATA_DEFAULTS_FILE", path)
    monkeypatch.setattr(defaults, "CONFIG_OVERRIDE_FILE", tmp_path / "absent.json")
    assert defaults.default_meter_addr() == ""


def test_layering_no_apps_import() -> None:
    """``core/defaults.py`` 不得 import ``apps.*``（分层铁律的局部复核）。"""

    text = (PKG_DIR / "core" / "defaults.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("from apps"), stripped
        assert not stripped.startswith("import apps"), stripped


# --------------------------------------------------------------------------- #
# 7. loader：声明里的 null → 默认值
# --------------------------------------------------------------------------- #


def _make_decl_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """造一个最小声明根：块 +（参数化）事实 + 用例，全部用 ``null`` 占位地址。"""

    root = tmp_path / "data"
    _write_json(
        root / "blocks" / "archive_add.json",
        {
            "id": "archive.add",
            "title": "假建档块（只验 loader 解析 null）",
            "needs": [],
            "provides": [],
            "action": {
                "afn": "11",
                "fn": 1,
                "params": {
                    "action": "add",
                    "meter_addrs": [None],
                    "meter_addr": None,
                    "protocol": 2,
                },
            },
            "assert": [{"type": "archive_contains", "meter_addr": None}],
        },
    )
    _write_json(
        root / "blocks" / "meter_brain_local.json",
        {
            "id": "meter.brain_local",
            "title": "假本机脑块（非地址的 null 不得被改写）",
            "needs": [],
            "provides": [],
            "action": {
                "afn": "02",
                "fn": 1,
                "params": {"meter_addr": None, "request_hex": None},
            },
            "assert": [],
        },
    )
    _write_json(
        root / "facts" / "archive_contains.json",
        {
            "id": "archive.contains",
            "title": "假档案事实（params_schema 里 default 为 null）",
            "probe": "archive.contains",
            "params_schema": {
                "meter_addr": {"type": "string", "required": True, "default": None},
                "count": {"type": "integer", "default": 16},
            },
        },
    )
    _write_json(
        root / "cases" / "archive_ready.json",
        {
            "id": "archive_ready",
            "title": "假用例",
            "steps": [{"no": 1, "do": "建档", "expect": "确认"}],
            "blocks": ["archive.add"],
        },
    )
    return root


def test_loader_resolves_null_meter_addr(tmp_path) -> None:
    """``"meter_addr": null`` → 加载后是真实默认值（调用方看到的就是默认值）。"""

    root = _make_decl_root(tmp_path)
    report = loader.load_all(root, strict=True)
    blocks = {spec.id: spec for spec in report.blocks}

    params = blocks["archive.add"].action["params"]
    assert params["meter_addr"] == defaults.default_meter_addr()
    assert params["meter_addrs"] == [defaults.default_meter_addr()]
    assert blocks["archive.add"].assert_[0]["meter_addr"] == defaults.default_meter_addr()
    # 非地址的 null 原样保留（不能把 request_hex 改成地址）
    assert blocks["meter.brain_local"].action["params"]["request_hex"] is None
    assert blocks["meter.brain_local"].action["params"]["meter_addr"] == (
        defaults.default_meter_addr()
    )

    facts = {spec.id: spec for spec in report.facts}
    schema = facts["archive.contains"].params_schema
    assert schema["meter_addr"]["default"] == defaults.default_meter_addr()
    assert schema["count"]["default"] == 16
    # 声明文件本身不被改写（解析只在内存里做）
    assert json.loads((root / "blocks" / "archive_add.json").read_text("utf-8"))[
        "action"
    ]["params"]["meter_addr"] is None


def test_loader_null_follows_config_override(tmp_path, monkeypatch) -> None:
    """null 解析走两层配置：覆盖文件一改，loader 解析出的默认值跟着变。"""

    override = tmp_path / "case_defaults.json"
    _write_json(override, {"meter_addr": "444444444444"})
    monkeypatch.setattr(defaults, "CONFIG_OVERRIDE_FILE", override)
    defaults.load_defaults(refresh=True)

    root = _make_decl_root(tmp_path)
    report = loader.load_all(root, strict=True)
    spec = {item.id: item for item in report.blocks}["archive.add"]
    assert spec.action["params"]["meter_addr"] == "444444444444"


def test_loader_keeps_null_when_no_default(tmp_path, monkeypatch) -> None:
    """默认值读不到时保留 ``null``（宁可让上层报缺参数，也不塞一个空串地址）。"""

    monkeypatch.setattr(defaults, "DATA_DEFAULTS_FILE", tmp_path / "absent-data.json")
    monkeypatch.setattr(defaults, "CONFIG_OVERRIDE_FILE", tmp_path / "absent-config.json")
    defaults.load_defaults(refresh=True)

    report = loader.load_all(_make_decl_root(tmp_path), strict=True)
    spec = {item.id: item for item in report.blocks}["archive.add"]
    assert spec.action["params"]["meter_addr"] is None
    assert spec.action["params"]["meter_addrs"] == [None]


def test_real_declarations_resolve_null_meter_addr() -> None:
    """真声明（``data/blocks/*.json`` / ``data/facts/*.json``）里不再有 null 表地址。"""

    expected = defaults.default_meter_addr()
    assert expected == "123456789012"

    blocks = {spec.id: spec for spec in loader.load_blocks()}
    assert blocks["archive.add"].action["params"]["meter_addr"] == expected
    assert_match = [
        item for item in blocks["archive.add"].assert_ if item.get("type") == "archive_contains"
    ]
    assert assert_match and assert_match[0]["meter_addr"] == expected
    # 非地址的 null 原样保留（request_hex 缺省由块按地址动态构造）
    assert blocks["meter.brain_local"].action["params"]["request_hex"] is None
    assert blocks["meter.brain_local"].action["params"]["meter_addr"] == expected

    facts = {spec.id: spec for spec in loader.load_facts()}
    assert facts["archive.contains"].params_schema["meter_addr"]["default"] == expected
    assert facts["sta.meter_addr_known"].params_schema["meter_addr"]["default"] == expected


# --------------------------------------------------------------------------- #
# 8. 参数化事实的权威判据（REQS-0042：显式标记，不靠 required 反推）
# --------------------------------------------------------------------------- #


def _fact(parameterized: bool):
    from case_runner.core.models import FactSpec

    return FactSpec(
        id="f.x",
        title="假事实",
        probe="f.x",
        params_schema={"meter_addr": {"type": "string", "default": "123456789012"}},
        parameterized=parameterized,
    )


def test_is_parameterized_explicit_marker_beats_required() -> None:
    """参数都有默认值（无必需参数）时，显式 ``parameterized: true`` 仍能识别出来。"""

    assert loader.required_params(_fact(True)) == []      # 老判据此时为空
    assert loader.is_parameterized(_fact(True)) is True   # 新判据必须不为空


def test_loader_gate_catches_explicit_marker() -> None:
    """结构门禁认显式标记：``provides`` 引用它必须报错（不能空转）。"""

    from case_runner.core.models import BlockSpec

    block = BlockSpec(
        id="block.b",
        title="假块",
        action={"afn": "11", "fn": 1},
        provides=["f.x"],
    )
    assert loader.check_provides_parameterized([block], [_fact(False)]) == []
    problems = loader.check_provides_parameterized([block], [_fact(True)])
    assert problems and "参数化事实" in problems[0]
