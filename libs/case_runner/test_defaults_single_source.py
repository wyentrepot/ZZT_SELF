"""默认地址单一来源（REQS-0042 / S1.6）：用例默认 ↔ 模拟电表默认 ↔ 档案默认。

现场口径（用户原话）：
    「000000000001 这个默认地址，应该与 CCO 档案默认表地址一致才对，
      这个复位就恢复 000000000001 不应该」

落地后的解析链（自上而下，只有最下面一层是「现场改一处」）：

    1. ``config/case_defaults.json``（现场层，**唯一来源**）
         ├─ meter_addr               → 用例/档案默认表地址（core/defaults.py）
         └─ simeter.addr / version   → 模拟电表默认（shared/bench_defaults.py
                                        → shared/simeter_settings.default_settings）
    2. ``libs/case_runner/data/case_defaults.json``（出厂默认，随代码走）
    3. ``MeterBrain.__init__`` 类默认（引擎层兜底；服务层不再用它当默认）

本文件守的是**跨层一致**：``case_runner.core.defaults`` 与 ``shared.bench_defaults``
读同一个现场文件，取到的地址必须一样；``config/`` 存在后来源诊断应指向它。

改动前预期（TDD）：``defaults_sources()["meter_addr"]`` 还指出厂文件 → 第一条用例即红。
"""

from __future__ import annotations

import pathlib

import pytest

from case_runner.core import defaults
from shared import bench_defaults

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / "config" / "case_defaults.json"


@pytest.fixture(autouse=True)
def _clean_cache():
    defaults.load_defaults(refresh=True)
    bench_defaults.load(refresh=True)
    yield
    defaults.load_defaults(refresh=True)
    bench_defaults.load(refresh=True)


def test_case_default_addr_unchanged() -> None:
    """加现场层后用例默认地址仍是 ``123456789012``（值相同、行为不变）。"""

    assert defaults.default_meter_addr() == "123456789012"


def test_config_layer_exists_and_is_read() -> None:
    """现场层文件存在，且用例默认值的来源已指向它（不是出厂文件）。"""

    assert CONFIG_FILE.is_file(), "config/case_defaults.json 应作为现场唯一来源存在"
    sources = defaults.defaults_sources()
    assert sources["meter_addr"] == CONFIG_FILE.as_posix()
    assert defaults.CONFIG_OVERRIDE_FILE == CONFIG_FILE


def test_simeter_default_matches_case_default() -> None:
    """模拟电表默认地址 = 用例/档案默认表地址（两边不再一个 000000000001 一个 123456789012）。"""

    bench_addr = bench_defaults.meter_addr()
    assert bench_addr == defaults.default_meter_addr()
    assert bench_defaults.simeter_addr() == bench_addr
    assert bench_defaults.simeter_addr() != "000000000001"


def test_simeter_section_declares_protocol_version() -> None:
    """现场层顺手声明模拟电表 645 版本（服务层直接取，不在别处再写一份）。"""

    assert bench_defaults.simeter_version() in ("2007", "1997")
    assert bench_defaults.simeter_version() == "2007"


def test_mapping_roles_still_resolved() -> None:
    """现场层不写 ``mapping_id`` 时，三个逻辑口名仍取出厂值（按子键合并的语义不变）。"""

    assert defaults.default_mapping_id("simcon") == "simcon"
    assert defaults.default_mapping_id("simeter") == "simeter"
    assert defaults.default_mapping_id("sta") == "sta-main"
