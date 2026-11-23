"""模拟电表服务层设置持久化 + 默认地址单一来源（REQS-0042 / S1.6）。

现场两个真缺陷：
1. 模拟电表**没有持久化设置**：``POST /api/simeter/config`` 改完表地址，工作台一重启
   就丢回 ``MeterBrain`` 的类默认 ``000000000001``，STA「搜表」取不到表、链路失败。
2. 默认地址**不统一**：用例/档案默认是 ``123456789012``（``config/case_defaults.json``），
   模拟电表默认却是 ``000000000001``，两边对不上。

口径：
- 服务自己装配电表脑时，**先读持久化设置**，没有文件才用 ``bench_defaults`` 的默认；
- ``POST /config`` 改完落盘（落盘失败只 warning，不影响接口返回）；
- ``MeterBrain`` 类默认值不动（``libs/sim_meter`` 大量测试拿它当夹具）。

全部走 ``tmp_path``：``shared.simeter_settings.default_runtime_dir`` 被 monkeypatch 指走，
不读写真台架的 ``data/runtime/``。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from sim_meter.api import SimMeterService, create_simeter_app
from sim_meter.meter import MeterBrain
from shared import bench_defaults
from shared import simeter_settings
from shared.simeter_settings import SimeterSettingsStore


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch) -> pathlib.Path:
    """把模拟电表的持久化目录指到 tmp_path（不碰真 data/runtime/）。"""

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(simeter_settings, "default_runtime_dir", lambda: runtime)
    bench_defaults.load(refresh=True)
    return runtime


def _settings_path(runtime_dir: pathlib.Path) -> pathlib.Path:
    return runtime_dir / "simeter_settings.json"


def _write_settings(runtime_dir: pathlib.Path, payload: dict) -> pathlib.Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = _settings_path(runtime_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 1. 启动：没有持久化文件 → 用 bench_defaults 的默认（不是 000000000001）
# --------------------------------------------------------------------------- #
def test_new_app_uses_bench_default_addr(runtime_dir: pathlib.Path) -> None:
    client = TestClient(create_simeter_app())
    cfg = client.get("/api/simeter/config").json()
    expected = bench_defaults.simeter_addr()
    assert expected == "123456789012"
    assert cfg["addr"] == expected
    assert cfg["addr"] != "000000000001"
    assert cfg["version"] == bench_defaults.simeter_version()


# --------------------------------------------------------------------------- #
# 2. 重启：新建 app 用持久化值初始化
# --------------------------------------------------------------------------- #
def test_new_app_loads_persisted_settings(runtime_dir: pathlib.Path) -> None:
    _write_settings(runtime_dir, {"addr": "999999999999", "version": "1997"})

    client = TestClient(create_simeter_app())
    cfg = client.get("/api/simeter/config").json()
    assert cfg["addr"] == "999999999999"
    assert cfg["version"] == "1997"
    # 状态口与配置口同源
    assert client.get("/api/simeter/status").json()["brain"]["addr"] == "999999999999"


def test_brain_class_default_untouched() -> None:
    """``MeterBrain`` 类默认值不动（既有夹具依赖 000000000001）。"""

    assert MeterBrain().addr_hex == "000000000001"


# --------------------------------------------------------------------------- #
# 3. POST /config 落盘（重启后还在）
# --------------------------------------------------------------------------- #
def test_post_config_persists_addr(runtime_dir: pathlib.Path) -> None:
    client = TestClient(create_simeter_app())
    r = client.post("/api/simeter/config",
                    json={"addr": "123456789012", "version": "2007"})
    assert r.status_code == 200
    assert r.json()["brain"]["addr"] == "123456789012"       # 响应结构不变

    path = _settings_path(runtime_dir)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["addr"] == "123456789012"
    assert payload["version"] == "2007"

    # 「重启」= 新建一个 app，读到的仍是刚落盘的地址
    restarted = TestClient(create_simeter_app())
    assert restarted.get("/api/simeter/config").json()["addr"] == "123456789012"


def test_post_config_persists_after_version_only_change(runtime_dir: pathlib.Path) -> None:
    client = TestClient(create_simeter_app())
    first = client.post("/api/simeter/config", json={"addr": "123456789012"})
    assert first.status_code == 200
    second = client.post("/api/simeter/config", json={"version": "97"})
    assert second.status_code == 200
    payload = json.loads(_settings_path(runtime_dir).read_text(encoding="utf-8"))
    assert payload["addr"] == "123456789012"      # 只改版本不丢地址
    assert payload["version"] == "1997"


def test_post_config_save_failure_is_only_a_warning(runtime_dir: pathlib.Path) -> None:
    """落盘失败（目录位置被一个文件占住）→ 接口照常 200，内存里的电表脑已改。"""

    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir.write_text("我不是目录", encoding="utf-8")

    client = TestClient(create_simeter_app())
    r = client.post("/api/simeter/config", json={"addr": "123456789012"})
    assert r.status_code == 200
    assert r.json()["brain"]["addr"] == "123456789012"


def test_corrupt_persisted_file_does_not_break_startup(runtime_dir: pathlib.Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _settings_path(runtime_dir).write_text("{ 坏 JSON", encoding="utf-8")

    client = TestClient(create_simeter_app())
    cfg = client.get("/api/simeter/config").json()
    assert cfg["addr"] == bench_defaults.simeter_addr()


# --------------------------------------------------------------------------- #
# 4. 显式注入服务/电表脑时不落盘（测试/嵌入场景不污染台架运行目录）
# --------------------------------------------------------------------------- #
def test_injected_brain_does_not_persist(runtime_dir: pathlib.Path) -> None:
    svc = SimMeterService(brain=MeterBrain(), log_dir=None)
    client = TestClient(create_simeter_app(service=svc))
    r = client.post("/api/simeter/config", json={"addr": "111111111111"})
    assert r.status_code == 200
    assert r.json()["brain"]["addr"] == "111111111111"
    assert not _settings_path(runtime_dir).exists()


def test_explicit_settings_store_is_used(tmp_path: pathlib.Path) -> None:
    """显式传 store 时，即便外部给了 brain 也按 store 初始化并落盘。"""

    store = SimeterSettingsStore(tmp_path / "runtime")
    store.save({"addr": "777777777777", "version": "2007"})

    svc = SimMeterService(log_dir=None, settings_store=store)
    assert svc.brain.addr_hex == "777777777777"
    client = TestClient(create_simeter_app(service=svc))
    client.post("/api/simeter/config", json={"addr": "888888888888"})
    assert json.loads(store.path.read_text(encoding="utf-8"))["addr"] == "888888888888"


# --------------------------------------------------------------------------- #
# 5. status / config 响应结构不变（前端与 case_runner 能力层按现结构解析）
# --------------------------------------------------------------------------- #
def test_response_shape_unchanged(runtime_dir: pathlib.Path) -> None:
    client = TestClient(create_simeter_app())
    cfg = client.get("/api/simeter/config").json()
    for key in ("addr", "version", "baudrate", "parity", "bytesize", "stopbits",
                "rx_count", "tx_count", "jzjc", "registers", "jzjc_types"):
        assert key in cfg, key
    status = client.get("/api/simeter/status").json()
    assert set(status) == {"brain", "serial", "session"}
    assert set(status["brain"]) == {"addr", "version", "baudrate", "parity",
                                    "bytesize", "stopbits", "rx_count", "tx_count"}
