"""cases_api：``/api/cases/*`` 十个端点的契约测试（不依赖真台架）。

覆盖口径（REQS-0042 §3.5 / §3.6 / 变更 2 D2-4·D2-8 / 变更 4 D4-1..D4-6 / 变更 10 D10-2）：

1. **四态**：``blocked`` 能序列化；块态 ``skipped`` → ``blocked``、``unknown`` → ``inconclusive``；
   方案级聚合 ``fail+blocked`` → ``fail``、``blocked+inconclusive`` → ``blocked``（只信
   ``core.executor.aggregate_verdict`` 这一份实现）。
2. **``GET /api/cases/bench``**：逻辑口可用性 + 全部事实三态（本文件的假声明根是 8 个事实，
   假桥快照是 5 个口，与台架同形），并验证「台架正被运行占用时不探测事实」。
3. **``POST /api/cases/run`` 预检**（D2-8）：只探所选用例 ``needs`` 并集；
   不满足 → 整轮 ``blocked`` + ``unmet_facts``、**不建 job**、<1s、一个块都不跑；
   满足 → 直接返回 ``job_id``。
4. **落盘**：``<storage_dir>/case_runs/<run_id>/result.json`` 含
   verdict/params（启动瞬间快照）/decisions/interventions（恒空）/self_healed/
   started_at/finished_at/台架快照，``GET /runs/{run_id}`` 能读回。
5. **分类筛选排序**（D10-2）：无 ``category`` 的用例仍能列出；``category`` 的 dict 形态与
   扁平形态都认；``usage``/``domain``/``scene``/``region``/``protocol`` 过滤与 ``sort=usage|name``。
6. **挂载守卫**：``/api/cases`` 挂上后 ``/api/autotest/*`` 不回归；``app.py`` 里紧跟 autotest 挂载。

隔离手段
--------
- **假声明根**：临时目录里放 4 类 JSON（块/事实/用例/方案），``register_domain=False``
  不导入真领域模块，块执行体由本文件用 ``register_block`` 注册假实现。
- **假事实探测源**：``register_fact`` 注册 ``fake.*`` 探测体（含 ``yes`` / ``no`` / ``unknown``），
  探测入参记到 ``CALLS``，用来验证「``params`` 一路传到事实层」。
- **假台架快照源**：``bridge_snapshot_provider`` 注入一份 5 口的 ``/api/serial-bridge/status``
  形状快照——**不读真配置、不开串口、不依赖端口**。
- **假能力/传输**：真声明用例跑「台架不可达」路径时，把 ``capabilities.*.default_transport``
  换成记录器（记 URL 后抛 ``OSError``），从而断言「请求打到哪」——这是「不得写死 8790」
  的可执行证据。
"""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from case_runner.cli import blocked_outcome, skipped_outcome
from case_runner.core.models import CaseSpec
from case_runner.core.registry import has_block, has_fact, register_block, register_fact
from workbench.cases_api import (
    CaseRunView,
    CasesService,
    create_cases_router,
    resolve_job_base_url,
)

# --------------------------------------------------------------------------- #
# 假声明：块 / 事实 / 用例 / 方案
# --------------------------------------------------------------------------- #

_FAKE_BLOCKS = {
    "fake.ok": {
        "id": "fake.ok",
        "title": "假块·通过",
        "needs": [],
        "provides": [],
        "action": {"semantic": "fake.ok"},
        "assert": [{"type": "fake_state", "detail": "假判据，仅展示用"}],
        "tags": ["fake"],
    },
    "fake.ok2": {
        "id": "fake.ok2",
        "title": "假块·通过（第二块）",
        "needs": [],
        "provides": [],
        "action": {"afn": "02", "fn": 1, "params": {"meter_addr": "000000000001"}},
        "assert": [{"type": "fake_frame"}],
        "tags": ["fake", "frame"],
    },
    "fake.fail": {
        "id": "fake.fail",
        "title": "假块·失败",
        "needs": [],
        "provides": [],
        "action": {"semantic": "fake.fail"},
        "tags": ["fake"],
    },
    "fake.skip": {
        "id": "fake.skip",
        "title": "假块·因前置事实不满足被跳过",
        "needs": ["fake.bench"],
        "provides": [],
        "action": {"semantic": "fake.skip"},
        "tags": ["fake"],
    },
    "fake.unknown": {
        "id": "fake.unknown",
        "title": "假块·返回无法识别结论（应为 unknown）",
        "needs": [],
        "provides": [],
        "action": {"semantic": "fake.unknown"},
        "tags": ["fake"],
    },
    "fake.slow": {
        "id": "fake.slow",
        "title": "假块·慢（验证异步 job 有中间态）",
        "needs": [],
        "provides": [],
        "action": {"semantic": "fake.slow"},
        "tags": ["fake", "slow"],
    },
    "fake.env": {
        "id": "fake.env",
        "title": "假块·记录执行期 WORKBENCH_BASE",
        "needs": [],
        "provides": [],
        "action": {"semantic": "fake.env"},
        "tags": ["fake"],
    },
}

#: 刚好 **8 个事实**（与台架的 8 个事实同数），三态都有：
#: yes × 5 / no × 1 / unknown × 2 —— 覆盖 `/api/cases/bench` 的全量与预检的按需。
_FAKE_FACTS = {
    "fake.bench": {
        "id": "fake.bench",
        "title": "假台架在位（永远 unknown）",
        "probe": "fake.bench",
        "params_schema": {"type": "object", "properties": {}},
    },
    "fake.addr": {
        "id": "fake.addr",
        "title": "假地址事实（参数化：必需 meter_addr，永远 yes）",
        "probe": "fake.addr",
        "params_schema": {
            "meter_addr": {
                "type": "string",
                "pattern": "^[0-9]{12}$",
                "required": True,
                "description": "12 位 BCD 表地址",
            }
        },
    },
    "fake.ready": {
        "id": "fake.ready",
        "title": "假前置事实·满足（yes）",
        "probe": "fake.ready",
        "params_schema": {"type": "object", "properties": {}},
    },
    "fake.missing": {
        "id": "fake.missing",
        "title": "假前置事实·不满足（no）",
        "probe": "fake.missing",
        "params_schema": {"type": "object", "properties": {}},
    },
    "fake.simcon": {
        "id": "fake.simcon",
        "title": "假模拟集中器在位（yes）",
        "probe": "fake.simcon",
        "params_schema": {"type": "object", "properties": {}},
    },
    "fake.simeter": {
        "id": "fake.simeter",
        "title": "假模拟电表在位（yes）",
        "probe": "fake.simeter",
        "params_schema": {"type": "object", "properties": {}},
    },
    "fake.sta": {
        "id": "fake.sta",
        "title": "假 STA 控制台口可用（yes）",
        "probe": "fake.sta",
        "params_schema": {"type": "object", "properties": {}},
    },
    "fake.nwk": {
        "id": "fake.nwk",
        "title": "假组网事实（unknown：说不清）",
        "probe": "fake.nwk",
        "params_schema": {"type": "object", "properties": {}},
    },
}

_FAKE_CASES = {
    "case_pass": {
        "id": "case_pass",
        "title": "假用例·两块全通过",
        "steps": [
            {"no": 1, "do": "跑假块一", "expect": "通过"},
            {"no": 2, "do": "跑假块二", "expect": "通过"},
        ],
        "needs": [],
        "blocks": ["fake.ok", "fake.ok2"],
        "asserts": [],
        "tags": ["fake", "smoke"],
    },
    "case_fail": {
        "id": "case_fail",
        "title": "假用例·必失败",
        "steps": [{"no": 1, "do": "跑假块失败", "expect": "失败（真实判据在块里）"}],
        "needs": [],
        "blocks": ["fake.fail"],
        "asserts": [],
        "tags": ["fake"],
    },
    "case_skip": {
        "id": "case_skip",
        "title": "假用例·前置事实 unknown 必须 blocked",
        "steps": [{"no": 1, "do": "等台架事实", "expect": "事实 unknown 时块 skipped → 用例 blocked"}],
        "needs": [],
        "blocks": ["fake.skip"],
        "asserts": [],
        "tags": ["fake", "breakpoint"],
    },
    "case_unknown": {
        "id": "case_unknown",
        "title": "假用例·块没给显式结论（unknown → inconclusive）",
        "steps": [{"no": 1, "do": "跑无结论假块", "expect": "unknown → inconclusive，绝不算 pass"}],
        "needs": [],
        "blocks": ["fake.unknown"],
        "asserts": [],
        "tags": ["fake", "breakpoint"],
    },
    "case_slow": {
        "id": "case_slow",
        "title": "假用例·慢（异步观测）",
        "steps": [{"no": 1, "do": "慢块", "expect": "通过"}],
        "needs": [],
        "blocks": ["fake.slow"],
        "asserts": [],
        "tags": ["fake", "slow"],
    },
    "case_addr": {
        "id": "case_addr",
        "title": "假用例·需要必填参数 meter_addr",
        "steps": [{"no": 1, "do": "用 meter_addr 探测事实", "expect": "yes 后跑块"}],
        "needs": ["fake.addr"],
        "blocks": ["fake.ok"],
        "asserts": [],
        "tags": ["fake", "params"],
    },
    "case_env": {
        "id": "case_env",
        "title": "假用例·记录执行期工作台地址",
        "steps": [{"no": 1, "do": "记录 WORKBENCH_BASE", "expect": "等于本次注入地址"}],
        "needs": [],
        "blocks": ["fake.env"],
        "asserts": [],
        "tags": ["fake", "base-url"],
    },
    # ---- 预检（D2-8）：needs 满足 → 起 job；needs 不满足 → 整轮 blocked ----
    "case_ready": {
        "id": "case_ready",
        "title": "假用例·前置事实满足（预检通过）",
        "steps": [{"no": 1, "do": "探前置事实", "expect": "yes 后跑块"}],
        "needs": ["fake.ready"],
        "blocks": ["fake.ok"],
        "asserts": [],
        "tags": ["fake", "preflight"],
    },
    "case_missing": {
        "id": "case_missing",
        "title": "假用例·前置事实不满足（预检阻断）",
        "steps": [{"no": 1, "do": "探前置事实", "expect": "no → 整轮 blocked"}],
        "needs": ["fake.missing"],
        "blocks": ["fake.ok"],
        "asserts": [],
        "tags": ["fake", "preflight"],
    },
    # ---- 分类（D10-2）：dict 形态 ----
    "case_cco": {
        "id": "case_cco",
        "title": "假用例·组网前置（CCO / 常用 / 组网 / 国网通用）",
        "steps": [{"no": 1, "do": "跑假块一", "expect": "通过"}],
        "needs": ["fake.ready"],
        "blocks": ["fake.ok"],
        "asserts": [],
        "tags": ["fake", "categorized"],
        "category": {
            "usage": "common",
            "domain": "CCO",
            "scene": "组网",
            "region": "国网通用",
        },
        "protocols": ["1376.2"],
    },
    # ---- 分类（D10-2）：扁平形态 ----
    "case_flat": {
        "id": "case_flat",
        "title": "假用例·抄表（扁平字段形态，STA / normal）",
        "steps": [{"no": 1, "do": "跑假块一", "expect": "通过"}],
        "needs": [],
        "blocks": ["fake.ok"],
        "asserts": [],
        "tags": ["fake", "categorized"],
        "usage": "normal",
        "domain": "STA",
        "scene": "抄表",
        "region": "重庆",
        "protocols": "645",
    },
}

_FAKE_SUITES = {
    "suite_pass": {
        "id": "suite_pass",
        "name": "假方案·单例通过",
        "case_ids": ["case_pass"],
        "stop_on_fail": True,
        "notes": "冒烟方案",
    },
    "suite_fail_first": {
        "id": "suite_fail_first",
        "name": "假方案·首例失败后停",
        "case_ids": ["case_fail", "case_pass"],
        "stop_on_fail": True,
        "notes": "验证跨用例 stop_on_fail",
    },
    "suite_nostop": {
        "id": "suite_nostop",
        "name": "假方案·失败后继续",
        "case_ids": ["case_pass", "case_fail"],
        "stop_on_fail": False,
        "notes": "",
    },
    # 四态聚合（§3.5 优先级）：fail > blocked
    "suite_fail_blocked": {
        "id": "suite_fail_blocked",
        "name": "假方案·fail + blocked",
        "case_ids": ["case_fail", "case_skip"],
        "stop_on_fail": False,
        "notes": "",
    },
    # 四态聚合（§3.5 优先级）：blocked > inconclusive
    "suite_blocked_inconclusive": {
        "id": "suite_blocked_inconclusive",
        "name": "假方案·blocked + inconclusive",
        "case_ids": ["case_skip", "case_unknown"],
        "stop_on_fail": False,
        "notes": "",
    },
}

#: 台架 5 个启用口（与 ``config/serial_bridge.json`` 的启用口同形，但**不读真配置**）
_BENCH_PORT_IDS = ("listener", "sta-main", "cco-main", "simeter", "simcon")


def _bridge_snapshot() -> dict:
    """假 ``/api/serial-bridge/status`` 快照：5 个启用口全部 usable。"""
    ports = []
    for index, port_id in enumerate(_BENCH_PORT_IDS):
        ports.append(
            {
                "id": port_id,
                "label": "假口·%s" % port_id,
                "enabled": True,
                "windows_com": "COM%d" % (index + 1),
                "link": "/tmp/fake-%s" % port_id,
                "mapping": {"mapping_id": port_id, "enabled": True},
                "channel_ok": True,
                "workbench_ready": True,
                "usable": True,
                "errors": [],
                "warnings": [],
            }
        )
    return {
        "ok": True,
        "config_error": "",
        "mapping_config_error": None,
        "status_file": "/tmp/fake-bridge-status.json",
        "supervisor": {"running": True, "pid": 4242, "stale": False, "age_seconds": 0.5},
        "summary": {"total": len(ports), "enabled": len(ports),
                    "channel_ok": len(ports), "workbench_ready": len(ports)},
        "all_enabled_ready": True,
        "ready_ports": [port["id"] for port in ports],
        "unusable_ports": [],
        "ports": ports,
    }


#: 假块/假事实执行期的观测记录（每个用例前清空）
CALLS: dict[str, list] = {"blocks": [], "probes": [], "env": []}


def _reset_calls() -> None:
    for value in CALLS.values():
        value.clear()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture()
def fake_root(tmp_path) -> Path:
    """假声明根（四类子目录齐全，避免 loader 把根目录当成某个子目录）。"""
    root = tmp_path / "data"
    for sub in ("blocks", "facts", "cases", "case_suites"):
        (root / sub).mkdir(parents=True)
    for block_id, payload in _FAKE_BLOCKS.items():
        _write_json(root / "blocks" / ("%s.json" % block_id.replace(".", "_")), payload)
    for fact_id, payload in _FAKE_FACTS.items():
        _write_json(root / "facts" / ("%s.json" % fact_id.replace(".", "_")), payload)
    for case_id, payload in _FAKE_CASES.items():
        _write_json(root / "cases" / ("%s.json" % case_id), payload)
    for suite_id, payload in _FAKE_SUITES.items():
        _write_json(root / "case_suites" / ("%s.json" % suite_id), payload)
    return root


# ---- 假块/假事实执行体（注册到内核注册表，API 侧零改动的注入方式） ---------- #


def _block_ok(params, ctx):
    CALLS["blocks"].append("fake.ok")
    return {"state": "pass", "detail": "假块通过"}


def _block_ok2(params, ctx):
    CALLS["blocks"].append("fake.ok2")
    return {"state": "pass", "detail": "假块二通过"}


def _block_fail(params, ctx):
    CALLS["blocks"].append("fake.fail")
    return {"state": "fail", "detail": "假块失败（真实判据由块给出，API 只透传）"}


def _block_unknown(params, ctx):
    CALLS["blocks"].append("fake.unknown")
    # 没有显式 state/outcome：内核只能判 unknown（ok=True 不代表测试通过）
    return {"ok": True, "detail": "只声明服务处理成功"}


def _block_slow(params, ctx):
    time.sleep(0.4)
    CALLS["blocks"].append("fake.slow")
    return {"state": "pass", "detail": "慢块通过"}


def _block_env(params, ctx):
    CALLS["env"].append(os.environ.get("WORKBENCH_BASE"))
    return {"state": "pass", "detail": "已记录 WORKBENCH_BASE"}


def _probe(state: str):
    """造一个固定三态的事实探测体（顺带记录探测入参）。"""

    def probe(params):
        CALLS["probes"].append((state, dict(params or {})))
        return {"state": state, "detail": "假事实：%s" % state}

    return probe


_PROBE_STATES = {
    "fake.bench": "unknown",
    "fake.addr": "yes",
    "fake.ready": "yes",
    "fake.missing": "no",
    "fake.simcon": "yes",
    "fake.simeter": "yes",
    "fake.sta": "yes",
    "fake.nwk": "unknown",
}

_EXECUTORS = {
    "fake.ok": _block_ok,
    "fake.ok2": _block_ok2,
    "fake.fail": _block_fail,
    "fake.unknown": _block_unknown,
    "fake.slow": _block_slow,
    "fake.env": _block_env,
}


def _ensure_registered() -> None:
    """幂等注册假执行体（别的测试可能清空过注册表）。"""
    for block_id, fn in _EXECUTORS.items():
        if not has_block(block_id):
            register_block(block_id, fn)
    for fact_id, state in _PROBE_STATES.items():
        if not has_fact(fact_id):
            register_fact(fact_id, _probe(state))


def _make_client(tmp_path, fake_root, *, provider=_bridge_snapshot) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_cases_router(
            storage_dir=tmp_path / "storage",
            data_dir=fake_root,
            register_domain=False,
            bridge_snapshot_provider=provider,
        )
    )
    return TestClient(app)


@pytest.fixture()
def client(tmp_path, fake_root):
    """假声明根 + 假执行体 + 假台架快照 → ``/api/cases/*`` 客户端（不连台架）。"""
    _reset_calls()
    _ensure_registered()
    return _make_client(tmp_path, fake_root)


def _wait_done(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get("/api/cases/jobs/%s" % job_id).json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job 未在 %.1fs 内结束：%s" % (timeout, job_id))


def _run(client: TestClient, body: dict) -> dict:
    resp = client.post("/api/cases/run", json=body)
    assert resp.status_code == 202, resp.text
    accepted = resp.json()
    assert accepted["accepted"] is True and accepted["job_id"]
    return _wait_done(client, accepted["job_id"])


# --------------------------------------------------------------------------- #
# 1. GET /api/cases（含 D10-2 分类筛选排序）
# --------------------------------------------------------------------------- #


def test_list_cases_returns_steps_blocks_needs_tags(client):
    resp = client.get("/api/cases")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == len(_FAKE_CASES)
    assert data["problems"] == [], data["problems"]
    case = next(item for item in data["cases"] if item["id"] == "case_pass")
    assert case["title"] == "假用例·两块全通过"
    assert case["block_count"] == 2 and case["blocks"] == ["fake.ok", "fake.ok2"]
    assert case["needs"] == [] and "smoke" in case["tags"]
    assert [step["no"] for step in case["steps"]] == [1, 2]
    assert case["steps"][0]["do"] and case["steps"][0]["expect"]


def test_list_cases_tolerates_missing_category_lists_them_unclassified(client):
    """分类字段缺失（现在还没有）→ 未分类，但**仍能列出**（D10-2 过渡期口径）。"""
    data = client.get("/api/cases").json()
    plain = next(item for item in data["cases"] if item["id"] == "case_pass")
    assert plain["category"] == {"usage": "", "domain": "", "scene": "", "region": ""}
    assert plain["protocols"] == []
    assert plain["classified"] is False
    assert plain["tolerated_fields"] == []
    assert data["unclassified"] >= 1
    assert data["classified"] == 2  # case_cco（dict 形态）+ case_flat（扁平形态）


def test_list_cases_reads_dict_category_and_filters(client):
    """``category`` 为 dict 形态：读得出、筛得动（domain=CCO / scene / protocol）。"""
    data = client.get("/api/cases").json()
    cco = next(item for item in data["cases"] if item["id"] == "case_cco")
    assert cco["category"] == {
        "usage": "common",
        "domain": "CCO",
        "scene": "组网",
        "region": "国网通用",
    }
    assert cco["protocols"] == ["1376.2"]
    assert cco["classified"] is True
    assert cco["tolerated_fields"] == ["category", "protocols"]

    by_domain = client.get("/api/cases", params={"domain": "CCO"}).json()
    assert [item["id"] for item in by_domain["cases"]] == ["case_cco"]
    assert by_domain["filters"] == {"domain": "CCO"}

    by_scene = client.get("/api/cases", params={"scene": "组网"}).json()
    assert [item["id"] for item in by_scene["cases"]] == ["case_cco"]

    by_region = client.get("/api/cases", params={"region": "国网通用"}).json()
    assert [item["id"] for item in by_region["cases"]] == ["case_cco"]

    by_protocol = client.get("/api/cases", params={"protocol": "1376.2"}).json()
    assert [item["id"] for item in by_protocol["cases"]] == ["case_cco"]

    # 大小写不敏感（知识库里的取值是 CCO/STA/ECU，调用方可能写小写）
    assert [item["id"] for item in client.get("/api/cases", params={"domain": "cco"}).json()["cases"]] == ["case_cco"]


def test_list_cases_reads_flat_category_form(client):
    """``category`` 为扁平字段形态（usage/domain/... 直接挂用例上）也要认。"""
    flat = next(
        item for item in client.get("/api/cases").json()["cases"] if item["id"] == "case_flat"
    )
    assert flat["category"] == {
        "usage": "normal",
        "domain": "STA",
        "scene": "抄表",
        "region": "重庆",
    }
    assert flat["protocols"] == ["645"]
    assert flat["classified"] is True
    assert flat["tolerated_fields"] == ["usage", "domain", "scene", "region", "protocols"]

    by_domain = client.get("/api/cases", params={"domain": "STA"}).json()
    assert [item["id"] for item in by_domain["cases"]] == ["case_flat"]


def test_list_cases_sort_by_usage_then_name(client):
    """缺省按常用度降序（common → normal → 未分类），``sort=name`` 按标题。"""
    data = client.get("/api/cases").json()
    assert data["sort"] == "usage"
    ids = [item["id"] for item in data["cases"]]
    assert ids[0] == "case_cco"       # usage=common
    assert ids[1] == "case_flat"      # usage=normal
    assert set(ids[2:]) == set(_FAKE_CASES) - {"case_cco", "case_flat"}

    by_name = client.get("/api/cases", params={"sort": "name"}).json()
    titles = [item["title"] for item in by_name["cases"]]
    assert titles == sorted(titles)


def test_categorized_case_is_runnable(client):
    """带 ``category`` 的用例不只「列得出来」，还得**跑得起来**（容忍不是只读装饰）。"""
    _reset_calls()
    job = _run(client, {"case_ids": ["case_cco"]})
    assert job["state"] == "done" and job["verdict"] == "pass"
    assert CALLS["blocks"] == ["fake.ok"]


def test_broken_case_does_not_gut_the_catalog(tmp_path, fake_root):
    """一个坏声明不该让整套用例消失（loader 的 strict 分支会整类丢弃，本模块补回来）。"""
    _reset_calls()
    _ensure_registered()
    _write_json(
        fake_root / "cases" / "case_broken.json",
        {"id": "case_broken", "title": "坏用例（缺 steps）", "blocks": ["fake.ok"]},
    )
    client = _make_client(tmp_path, fake_root)

    data = client.get("/api/cases").json()
    ids = {item["id"] for item in data["cases"]}
    assert "case_broken" not in ids            # 坏的不许冒充正常用例
    assert "case_pass" in ids and "case_cco" in ids  # 好的一个不少
    assert data["problems"]                    # 出什么事要如实报
    assert any("case_broken" in item for item in data["problems"])

    # 声明带病时拒绝运行（会在真实台架上产生激励，宁可不跑）
    resp = client.post("/api/cases/run", json={"case_ids": ["case_pass"]})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "declaration_error"


def test_list_cases_filters_exclude_unclassified(client):
    """筛选某一维度时，未分类的用例不出现（它不是「碰巧匹配」）。"""
    data = client.get("/api/cases", params={"usage": "common"}).json()
    assert [item["id"] for item in data["cases"]] == ["case_cco"]
    # 逗号分隔多值
    multi = client.get("/api/cases", params={"usage": "common,normal"}).json()
    assert {item["id"] for item in multi["cases"]} == {"case_cco", "case_flat"}


# --------------------------------------------------------------------------- #
# 2. GET /api/cases/blocks
# --------------------------------------------------------------------------- #


def test_list_blocks_returns_action_summary_and_assert_metadata(client):
    resp = client.get("/api/cases/blocks")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == len(_FAKE_BLOCKS)
    blocks = {item["id"]: item for item in data["blocks"]}

    ok = blocks["fake.ok"]
    assert ok["action"]["kind"] == "semantic"
    assert ok["action"]["semantic"] == "fake.ok"
    assert ok["assert"][0]["type"] == "fake_state"  # 别名 assert 进 JSON
    assert ok["assert_types"] == ["fake_state"]
    assert ok["runnable"] is True and ok["needs"] == [] and ok["provides"] == []

    frame = blocks["fake.ok2"]
    assert frame["action"]["kind"] == "frame"
    assert frame["action"]["afn"] == "02" and frame["action"]["fn"] == 1
    assert frame["action"]["params"] == {"meter_addr": "000000000001"}
    assert "AFN=02" in frame["action"]["summary"]

    assert blocks["fake.skip"]["needs"] == ["fake.bench"]
    assert blocks["fake.skip"]["runnable"] is False  # 只有声明、没有执行体


# --------------------------------------------------------------------------- #
# 3. GET /api/cases/facts
# --------------------------------------------------------------------------- #


def test_list_facts_returns_probe_and_params_schema(client):
    resp = client.get("/api/cases/facts")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == len(_FAKE_FACTS) == 8
    facts = {item["id"]: item for item in data["facts"]}
    addr = facts["fake.addr"]
    assert addr["probe"] == "fake.addr"
    assert addr["required"] == ["meter_addr"]
    assert addr["params_schema"]["meter_addr"]["pattern"] == "^[0-9]{12}$"
    assert addr["registered"] is True
    assert facts["fake.bench"]["params_schema"]["type"] == "object"


# --------------------------------------------------------------------------- #
# 4. GET /api/cases/bench（D2-8 / UI-2）
# --------------------------------------------------------------------------- #


def test_bench_snapshot_has_ports_and_all_facts(client):
    """/bench = 逻辑口可用性（5 口）+ 全部事实三态（8 个），结构给 UI 置灰用。"""
    resp = client.get("/api/cases/bench")
    assert resp.status_code == 200, resp.text
    bench = resp.json()

    # 口：5 个启用口全部 usable，不写死端口（数量/名字来自快照）
    assert bench["ports_total"] == 5
    assert bench["ports_usable"] == 5
    assert bench["ports_ok"] is True
    assert bench["ports_disabled"] == 0
    assert [port["mapping_id"] for port in bench["ports"]] == list(_BENCH_PORT_IDS)
    assert all(port["usable"] for port in bench["ports"])
    assert bench["bridge_ok"] is True
    assert bench["bridge_supervisor"]["running"] is True
    assert bench["bridge_error"] == ""

    # 事实：全量 8 个三态（yes×5 / no×1 / unknown×2）
    assert bench["facts_total"] == 8
    assert bench["fact_counts"] == {"yes": 5, "no": 1, "unknown": 2}
    states = {fact["fact_id"]: fact["state"] for fact in bench["facts"]}
    assert states["fake.ready"] == "yes" and states["fake.missing"] == "no"
    assert states["fake.bench"] == "unknown"

    # 缺项清单：no/unknown 都要点名（unknown 绝不当就绪）
    assert set(bench["unmet_facts"]) == {"fake.bench", "fake.missing", "fake.nwk"}
    assert bench["unmet_facts"]["fake.missing"]["state"] == "no"
    assert bench["unmet_facts"]["fake.nwk"]["reason"]
    assert bench["facts_ok"] is False and bench["ready"] is False
    assert bench["probe_skipped"] == ""
    assert bench["base_url_source"] == "http"
    assert bench["duration_s"] >= 0

    # 事实探测确实走了注册表（不是凭空造的三态）
    assert CALLS["probes"], "台架快照应真的探测过事实"


def test_default_bridge_snapshot_provider_reads_real_serial_bridge_config():
    """默认口快照源（同进程读 ``/api/serial-bridge/status`` 的实现）必须真能读。

    这里 intentionally 读仓库真实配置：`/api/cases/bench` 不注入 provider 时走的就是它，
    只读、不开串口、不依赖任何端口。
    """
    from workbench.cases_api import _default_bridge_snapshot

    snapshot = _default_bridge_snapshot()
    assert isinstance(snapshot, dict)
    assert isinstance(snapshot.get("ports"), list) and snapshot["ports"]
    assert any(port.get("id") for port in snapshot["ports"])
    assert "supervisor" in snapshot


def test_bench_snapshot_reports_unreadable_bridge_but_keeps_facts(tmp_path, fake_root):
    """桥状态读不到 → 如实报错、口列表为空，但事实三态照给（不整页 500）。"""
    _reset_calls()
    _ensure_registered()

    def boom():
        raise OSError("bridge down (fake)")

    client = _make_client(tmp_path, fake_root, provider=boom)
    resp = client.get("/api/cases/bench")
    assert resp.status_code == 200, resp.text
    bench = resp.json()
    assert bench["ports"] == [] and bench["ports_total"] == 0
    assert bench["ports_ok"] is False
    assert "bridge down (fake)" in bench["bridge_error"]
    assert bench["facts_total"] == 8  # 事实照给


def test_bench_skips_fact_probe_while_another_run_holds_env_lock(tmp_path, fake_root):
    """台架正被一次运行占用时**不探事实**：绝不用别人的地址去探自己的结论。"""
    _reset_calls()
    _ensure_registered()
    service = CasesService(
        tmp_path / "storage",
        data_dir=fake_root,
        register_domain=False,
        bridge_snapshot_provider=_bridge_snapshot,
    )
    # 锁是「每个线程」的（RLock）：必须真在**另一个线程**持锁，才模拟得出「运行中」
    holding = threading.Event()
    release = threading.Event()

    def holder() -> None:
        service.env_lock.acquire()
        holding.set()
        release.wait(5.0)
        service.env_lock.release()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert holding.wait(5.0), "持锁线程没起来"
    try:
        bench = service.bench(base_url="http://other:1", base_source="body")
    finally:
        release.set()
        thread.join(5.0)
    assert bench.probe_skipped
    assert all(fact.state == "unknown" for fact in bench.facts)
    assert bench.facts_total == 8
    assert bench.ports_usable == 5  # 口快照不依赖地址注入，照给
    assert CALLS["probes"] == []    # 一个事实都没探


# --------------------------------------------------------------------------- #
# 5. GET /api/cases/suites
# --------------------------------------------------------------------------- #


def test_list_suites_returns_case_ids_and_stop_on_fail(client):
    resp = client.get("/api/cases/suites")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    suites = {item["id"]: item for item in data["suites"]}
    assert set(_FAKE_SUITES) <= set(suites)
    assert suites["suite_fail_first"]["case_ids"] == ["case_fail", "case_pass"]
    assert suites["suite_fail_first"]["stop_on_fail"] is True
    assert suites["suite_nostop"]["stop_on_fail"] is False
    assert suites["suite_pass"]["name"] == "假方案·单例通过"


# --------------------------------------------------------------------------- #
# 6. POST /api/cases/suites
# --------------------------------------------------------------------------- #


def test_save_suite_persists_json_and_upserts(client, fake_root, tmp_path):
    body = {
        "id": "suite_new",
        "name": "新方案",
        "case_ids": ["case_pass", "case_skip"],
        "stop_on_fail": False,
        "notes": "UI 保存",
    }
    resp = client.post("/api/cases/suites", json=body)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["replaced"] is False
    assert saved["suite"]["id"] == "suite_new"
    path = Path(saved["path"])
    # D2-12：用户方案落 <storage>/case_suites，**绝不写进声明根**（内置方案只读）
    assert path.is_file() and path.parent == (tmp_path / "storage" / "case_suites").resolve()
    assert path.parent != (fake_root / "case_suites").resolve(), "用户方案不许写进仓库声明根"
    assert not (fake_root / "case_suites" / "suite_new.json").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == body

    listed = {item["id"] for item in client.get("/api/cases/suites").json()["suites"]}
    assert "suite_new" in listed

    again = client.post("/api/cases/suites", json=body)
    assert again.status_code == 200
    assert again.json()["replaced"] is True  # 同名覆盖


def test_save_suite_rejects_unknown_case_ids(client):
    resp = client.post(
        "/api/cases/suites",
        json={"id": "bad", "name": "坏方案", "case_ids": ["nope"]},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "case_ids_unknown"
    assert "nope" in detail["message"] and "不存在的用例" in detail["message"]
    assert detail["problems"]


def test_save_suite_rejects_path_like_id(client):
    resp = client.post(
        "/api/cases/suites",
        json={"id": "../evil", "name": "越界", "case_ids": ["case_pass"]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "suite_id_invalid"


def test_save_suite_body_shape_is_validated(client):
    resp = client.post("/api/cases/suites", json={"id": "x", "name": "缺 case_ids"})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# 7. DELETE /api/cases/suites/{suite_id}
# --------------------------------------------------------------------------- #


def test_builtin_suite_is_readonly_and_user_suite_is_deletable(client, fake_root, tmp_path):
    """内置方案只读（D2-12）：删内置 → 409；用户方案可保存、可删除、可覆盖内置。

    2026-11-23 真机回归：早先实现把用户方案写进了声明根（`libs/case_runner/data/case_suites`），
    既污染仓库又让声明校验测试变红；此用例把「内置只读 + 用户可写」钉死。
    """
    # 1) 删内置 → 409 suite_readonly，且文件仍在
    builtin = fake_root / "case_suites" / "suite_nostop.json"
    assert builtin.is_file()
    denied = client.delete("/api/cases/suites/suite_nostop")
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["code"] == "suite_readonly"
    assert builtin.is_file(), "内置方案文件不得被删除"

    # 2) 用户另存同名 → 覆盖内置，且落 storage（不是声明根）
    body = {"id": "suite_nostop", "name": "用户覆盖", "case_ids": ["case_pass"],
            "stop_on_fail": True, "notes": "覆盖内置"}
    saved = client.post("/api/cases/suites", json=body)
    assert saved.status_code == 200, saved.text
    user_path = Path(saved.json()["path"])
    assert user_path.parent == (tmp_path / "storage" / "case_suites").resolve()
    listed = {item["id"]: item for item in client.get("/api/cases/suites").json()["suites"]}
    assert listed["suite_nostop"]["name"] == "用户覆盖", "同 id 时用户方案覆盖内置"

    # 3) 删用户方案 → 200；列表回落成内置版本
    ok = client.delete("/api/cases/suites/suite_nostop")
    assert ok.status_code == 200, ok.text
    assert not user_path.exists()
    listed = {item["id"]: item for item in client.get("/api/cases/suites").json()["suites"]}
    assert listed["suite_nostop"]["name"] != "用户覆盖"

    # 4) 再删已经没有用户覆盖 → 409（内置只读，不是 404）
    again = client.delete("/api/cases/suites/suite_nostop")
    assert again.status_code == 409, again.text

    # 5) 删不存在的 → 404
    missing = client.delete("/api/cases/suites/suite_never")
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"]["code"] == "suite_not_found"


# --------------------------------------------------------------------------- #
# 8. POST /api/cases/run —— 预检（D2-8）
# --------------------------------------------------------------------------- #


def test_run_preflight_unmet_returns_blocked_without_job(client, tmp_path):
    """事实不满足 → 整轮 blocked + unmet_facts，**不建 job**，<1s，一个块都不跑。"""
    started = time.monotonic()
    resp = client.post("/api/cases/run", json={"case_ids": ["case_missing"]})
    elapsed = time.monotonic() - started

    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["accepted"] is False
    assert result["state"] == "blocked"
    assert result["verdict"] == "blocked"
    assert result["job_id"] == ""          # 没有 job（不是「起 job 再报 blocked」）
    assert result["job_url"] == ""
    assert result["run_id"] and result["result_url"] == "/api/cases/runs/%s" % result["run_id"]
    assert elapsed < 1.0, "预检阻断必须 <1s 返回，实际 %.2fs" % elapsed
    assert CALLS["blocks"] == [], "预检不通过时一个块都不许跑"

    unmet = result["unmet_facts"]
    assert set(unmet) == {"fake.missing"}
    assert unmet["fake.missing"]["state"] == "no"
    assert unmet["fake.missing"]["reason"]
    assert result["preflight"]["ok"] is False
    assert result["preflight"]["unmet_facts"]
    assert result["problems"]

    # 证据照样落盘，可按 run_id 读回（历史里能查「这轮为什么没跑」）
    assert result["result_path"] and Path(result["result_path"]).is_file()
    history = client.get("/api/cases/runs/%s" % result["run_id"])
    assert history.status_code == 200, history.text
    record = history.json()
    assert record["state"] == "blocked" and record["accepted"] is False
    assert record["verdict"] == "blocked"
    assert record["interventions"] == []
    assert record["results"][0]["case_id"] == "case_missing"
    assert record["results"][0]["verdict"] == "blocked"
    assert record["results"][0]["unmet_facts"]["fake.missing"]["state"] == "no"


def test_run_preflight_ok_returns_job_id_and_runs(client):
    """事实满足 → 直接起异步 job（立即返回 job_id），跑完是 pass。"""
    resp = client.post("/api/cases/run", json={"case_ids": ["case_ready"]})
    assert resp.status_code == 202, resp.text
    accepted = resp.json()
    assert accepted["accepted"] is True
    assert accepted["state"] == "queued"
    assert accepted["job_id"].startswith("job-")
    assert accepted["unmet_facts"] == {}
    assert accepted["preflight"]["ok"] is True

    job = _wait_done(client, accepted["job_id"])
    assert job["state"] == "done" and job["verdict"] == "pass"
    # 预检探过的事实进了 job 视图（UI 能看见「这台架当时是什么状态」）
    assert job["preflight"]["facts"]["fake.ready"]["state"] == "yes"
    assert job["bench"]["facts"]["fake.ready"]["state"] == "yes"


def test_run_preflight_only_probes_needs_union(client):
    """预检只探所选用例 ``needs`` 的并集（不是全量台架快照，D2-8）。"""
    _reset_calls()
    job = _run(client, {"case_ids": ["case_ready", "case_pass"]})
    assert job["verdict"] == "pass"
    probed = {state for state, _params in CALLS["probes"]}
    assert probed == {"yes"}
    assert set(job["preflight"]["facts"]) == {"fake.ready"}


def test_run_no_preflight_skips_preflight_and_runs(client):
    """``no_preflight=true``（诊断用）：跳过预检，让用例自己跑出真实断点。"""
    _reset_calls()
    job = _run(client, {"case_ids": ["case_missing"], "no_preflight": True})
    assert job["state"] == "done"
    assert job["preflight"]["enabled"] is False
    assert job["preflight"]["facts"] == {} and job["preflight"]["unmet_facts"] == {}
    # 预检关掉了，但用例自己的 needs 仍在执行期被求值：事实 no → 全部步骤 skipped
    # → 整条用例 blocked（关预检只是不再「整轮阻断」，不是把台架问题说成通过）
    assert job["verdict"] == "blocked"
    assert CALLS["blocks"] == []
    case = job["results"][0]
    assert case["verdict"] == "blocked"
    assert case["unmet_facts"]["fake.missing"]["state"] == "no"


# --------------------------------------------------------------------------- #
# 9. 四态（D2-4）：blocked 可序列化 + 方案级聚合优先级
# --------------------------------------------------------------------------- #


def test_blocked_verdict_is_serializable_end_to_end():
    """``blocked`` 能进读模型 / JSON（旧骨架的三态 Literal 会直接拒收）。"""
    case = CaseSpec(
        id="case_x",
        title="四态序列化",
        steps=[{"no": 1, "do": "探台架", "expect": "blocked"}],
        blocks=["fake.ok"],
    )
    outcome = blocked_outcome(
        case,
        run_id="run-x",
        reason="台架不具备：端口被占",
        unmet_facts={"fake.bench": {"state": "no", "reason": "端口被占", "detail": "COM4 被占用"}},
    )
    view = CaseRunView.from_outcome(outcome)
    assert view.verdict == "blocked"
    payload = view.model_dump(mode="json")
    assert payload["verdict"] == "blocked"
    assert payload["blocked_block"] == "" or payload["blocked_block"] is not None
    assert payload["interventions"] == []
    assert payload["unmet_facts"]["fake.bench"]["state"] == "no"
    assert payload["unmet_facts"]["fake.bench"]["reason"] == "端口被占"
    assert json.loads(json.dumps(payload))["verdict"] == "blocked"

    # 未执行（stop_on_fail / 用户取消）仍是 inconclusive，不许冒充 pass
    skipped = CaseRunView.from_outcome(
        skipped_outcome(case, run_id="run-y", reason="stop_on_fail：前序用例未通过")
    )
    assert skipped.verdict == "inconclusive"


def test_skipped_block_maps_to_blocked_and_unknown_to_inconclusive(client):
    """块态 ``skipped`` → 用例 ``blocked``；块态 ``unknown`` → 用例 ``inconclusive``。"""
    job = _run(client, {"case_ids": ["case_skip", "case_unknown"]})
    assert job["state"] == "done"
    states = {item["case_id"]: item["blocks"][0]["state"] for item in job["results"]}
    assert states == {"case_skip": "skipped", "case_unknown": "unknown"}
    verdicts = {item["case_id"]: item["verdict"] for item in job["results"]}
    assert verdicts == {"case_skip": "blocked", "case_unknown": "inconclusive"}
    assert job["counts"]["pass"] == 0          # 防假 PASS 的核心断言
    assert job["counts"]["skipped"] == 1 and job["counts"]["unknown"] == 1
    assert job["case_counts"] == {
        "total": 2, "pass": 0, "fail": 0, "blocked": 1, "inconclusive": 1,
    }
    # 方案级：blocked 比 inconclusive 严重 → blocked
    assert job["verdict"] == "blocked"


def test_suite_verdict_fail_beats_blocked(client):
    job = _run(client, {"suite_id": "suite_fail_blocked"})
    assert job["verdict"] == "fail"
    assert job["case_counts"] == {
        "total": 2, "pass": 0, "fail": 1, "blocked": 1, "inconclusive": 0,
    }
    assert sorted(item["verdict"] for item in job["results"]) == ["blocked", "fail"]


def test_suite_verdict_blocked_beats_inconclusive(client):
    job = _run(client, {"suite_id": "suite_blocked_inconclusive"})
    assert job["verdict"] == "blocked"
    assert job["case_counts"] == {
        "total": 2, "pass": 0, "fail": 0, "blocked": 1, "inconclusive": 1,
    }


# --------------------------------------------------------------------------- #
# 10. job 生命周期（异步 / 409 / 404）
# --------------------------------------------------------------------------- #


def test_run_case_ids_returns_job_id_then_polls_to_done(client):
    resp = client.post("/api/cases/run", json={"case_ids": ["case_pass"]})
    assert resp.status_code == 202, resp.text
    started = resp.json()
    assert started["job_id"].startswith("job-")
    assert started["run_id"]
    assert started["case_ids"] == ["case_pass"]
    assert started["job_url"] == "/api/cases/jobs/%s" % started["job_id"]

    job = _wait_done(client, started["job_id"])
    assert job["state"] == "done"
    assert job["verdict"] == "pass"
    assert job["exit_code"] == 0
    assert job["case_counts"] == {
        "total": 1, "pass": 1, "fail": 0, "blocked": 0, "inconclusive": 0,
    }
    assert job["counts"] == {"total": 2, "pass": 2, "fail": 0, "skipped": 0, "unknown": 0}
    assert job["progress"] == {
        "total": 1, "done": 1, "current_case_id": "", "current_case_title": "", "percent": 100,
    }
    assert job["error"] == "" and job["duration_seconds"] >= 0
    assert job["interventions"] == []

    case = job["results"][0]
    assert case["case_id"] == "case_pass" and case["title"] == "假用例·两块全通过"
    assert case["verdict"] == "pass"
    assert [blk["block_id"] for blk in case["blocks"]] == ["fake.ok", "fake.ok2"]
    assert all(blk["state"] == "pass" for blk in case["blocks"])
    assert case["failed_block"] == "" and case["blocked_block"] == ""
    assert case["unmet_facts"] == {}
    assert [step["block_id"] for step in case["steps"]] == ["fake.ok", "fake.ok2"]


def test_run_by_suite_honours_stop_on_fail(client):
    job = _run(client, {"suite_id": "suite_fail_first"})
    assert job["state"] == "done"
    assert job["suite_id"] == "suite_fail_first"
    assert job["stop_on_fail"] is True
    assert job["verdict"] == "fail"
    assert job["case_counts"] == {
        "total": 2, "pass": 0, "fail": 1, "blocked": 0, "inconclusive": 1,
    }
    failed, skipped = job["results"]
    assert failed["case_id"] == "case_fail" and failed["verdict"] == "fail"
    assert failed["failed_block"] == "fake.fail"
    assert skipped["case_id"] == "case_pass" and skipped["verdict"] == "inconclusive"
    assert "stop_on_fail" in skipped["skipped_reason"]
    assert skipped["blocks"] == []  # 未执行


def test_run_by_suite_without_stop_on_fail_runs_both(client):
    job = _run(client, {"suite_id": "suite_nostop"})
    assert [item["verdict"] for item in job["results"]] == ["pass", "fail"]
    assert job["case_counts"]["fail"] == 1 and job["case_counts"]["pass"] == 1


def test_job_is_async_so_running_state_is_observable(client):
    resp = client.post("/api/cases/run", json={"case_ids": ["case_slow"]})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    # 慢块 0.4s：受理后应能看到 running + 当前用例
    deadline = time.time() + 5.0
    seen_running = False
    while time.time() < deadline:
        job = client.get("/api/cases/jobs/%s" % job_id).json()
        if job["state"] == "running" and job["progress"]["current_case_id"] == "case_slow":
            seen_running = True
            break
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.02)
    final = _wait_done(client, job_id)
    assert final["state"] == "done" and final["verdict"] == "pass"
    assert seen_running, "POST 必须立即返回（异步），running 中间态应可观测"


def test_job_404_for_unknown_id(client):
    resp = client.get("/api/cases/jobs/job-never")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "job_not_found"


def test_concurrent_run_conflicts_409(client):
    first = client.post("/api/cases/run", json={"case_ids": ["case_slow"]})
    assert first.status_code == 202
    second = client.post("/api/cases/run", json={"case_ids": ["case_pass"]})
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "busy"
    assert first.json()["job_id"] in detail["message"]
    _wait_done(client, first.json()["job_id"])


# --------------------------------------------------------------------------- #
# 11. GET /api/cases/runs/{run_id} + 落盘证据（D4-1 / D4-4 / D4-6）
# --------------------------------------------------------------------------- #

#: ``result.json`` 必须齐备的字段（D4-1 参数冻结 / D4-4 留痕 / D4-6 证据）
_REQUIRED_RESULT_KEYS = {
    "job_id", "run_id", "state", "suite_id", "case_ids",
    "base_url", "base_url_source", "params", "resolved_params",
    "stop_on_fail", "timeout_s", "created_at", "started_at", "finished_at",
    "duration_seconds", "progress", "verdict", "exit_code", "counts", "case_counts",
    "results", "decisions", "interventions", "self_healed", "bench", "preflight",
    "error", "result_path",
}


def test_result_json_has_all_evidence_fields(client, tmp_path):
    job = _run(
        client,
        {"case_ids": ["case_addr"], "params": {"meter_addr": "123456789012"},
         "timeout": 123},
    )
    assert job["verdict"] == "pass"
    path = Path(job["result_path"])
    assert path.is_file()
    assert path == tmp_path / "storage" / "case_runs" / job["run_id"] / "result.json"
    document = json.loads(path.read_text(encoding="utf-8"))

    missing = _REQUIRED_RESULT_KEYS - set(document)
    assert not missing, "result.json 缺字段：%s" % sorted(missing)
    # D4-1：入参是启动瞬间快照，逐字落盘
    assert document["params"] == {"meter_addr": "123456789012"}
    assert document["resolved_params"] == document["params"]
    # D4-4：干预恒空；自愈与自动决策是数组（本次没有自愈，但字段在）
    assert document["interventions"] == []
    assert document["self_healed"] == []
    assert isinstance(document["decisions"], list) and document["decisions"]
    assert any(item.get("kind") == "preflight" for item in document["decisions"])
    # 时间戳与结论
    assert document["started_at"] and document["finished_at"]
    assert document["verdict"] == "pass" and document["exit_code"] == 0
    assert document["timeout_s"] == 123
    # 台架快照：启动瞬间的口快照 + 预检探到的事实三态
    bench = document["bench"]
    assert bench["ports"]["ports_total"] == 5
    assert bench["ports"]["ports_usable"] == 5
    assert "facts" in bench and "unmet_facts" in bench
    assert document["preflight"]["ok"] is True
    # 逐用例结论也在（含断点字段与内层 interventions 恒空）
    assert document["results"][0]["case_id"] == "case_addr"
    assert document["results"][0]["interventions"] == []


def test_runs_endpoint_reads_persisted_result(client):
    job = _run(client, {"case_ids": ["case_pass"]})
    resp = client.get("/api/cases/runs/%s" % job["run_id"])
    assert resp.status_code == 200, resp.text
    record = resp.json()
    assert record["run_id"] == job["run_id"]
    assert record["state"] == "done" and record["accepted"] is True
    assert record["counts"] == job["counts"]
    assert record["case_counts"] == job["case_counts"]
    assert record["results"][0]["verdict"] == "pass"
    assert record["interventions"] == []
    assert record["verdict"] == job["verdict"] == "pass"


def test_runs_endpoint_404_for_unknown_and_traversal(client):
    assert client.get("/api/cases/runs/nope").status_code == 404
    # 路径穿越：%2E%2E 解成 ".." 后必须被 run_id 白名单挡住
    traversal = client.get("/api/cases/runs/%2E%2E")
    assert traversal.status_code == 404, traversal.text
    assert traversal.json()["detail"]["code"] == "run_not_found"


# --------------------------------------------------------------------------- #
# 入参/目标选定的错误路径（422 / 404 / 409）
# --------------------------------------------------------------------------- #


def test_run_rejects_both_or_neither_target(client):
    neither = client.post("/api/cases/run", json={})
    assert neither.status_code == 422, neither.text
    assert neither.json()["detail"]["code"] == "run_target_invalid"

    both = client.post(
        "/api/cases/run", json={"suite_id": "suite_pass", "case_ids": ["case_pass"]}
    )
    assert both.status_code == 422, both.text
    assert both.json()["detail"]["code"] == "run_target_invalid"

    empty = client.post("/api/cases/run", json={"case_ids": []})
    assert empty.status_code == 422, empty.text


def test_run_404_for_unknown_suite_and_case(client):
    unknown_suite = client.post("/api/cases/run", json={"suite_id": "nope"})
    assert unknown_suite.status_code == 404, unknown_suite.text
    assert unknown_suite.json()["detail"]["code"] == "suite_not_found"

    unknown_case = client.post("/api/cases/run", json={"case_ids": ["case_pass", "nope"]})
    assert unknown_case.status_code == 404, unknown_case.text
    assert "nope" in unknown_case.json()["detail"]["message"]


def test_run_rejects_unknown_body_field(client):
    resp = client.post("/api/cases/run", json={"case_ids": ["case_pass"], "wat": 1})
    assert resp.status_code == 422, resp.text


def test_run_rejects_negative_timeout(client):
    resp = client.post("/api/cases/run", json={"case_ids": ["case_pass"], "timeout": -1})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "timeout_invalid"


def test_run_params_validation_is_chinese_and_reuses_cli_rules(client):
    """``params`` 多余键 / 类型非法 / 缺必填 / 不匹配 pattern → 422 + 中文原因。"""
    missing = client.post("/api/cases/run", json={"case_ids": ["case_addr"]})
    assert missing.status_code == 422, missing.text
    detail = missing.json()["detail"]
    assert detail["code"] == "params_invalid"
    assert any("缺少必填参数" in item for item in detail["problems"])

    extra = client.post(
        "/api/cases/run",
        json={"case_ids": ["case_addr"], "params": {"meter_addr": "000000000001", "bogus": 1}},
    )
    assert extra.status_code == 422, extra.text
    assert any("多余参数" in item for item in extra.json()["detail"]["problems"])

    wrong_type = client.post(
        "/api/cases/run", json={"case_ids": ["case_addr"], "params": {"meter_addr": 123}}
    )
    assert wrong_type.status_code == 422, wrong_type.text
    assert any("类型非法" in item for item in wrong_type.json()["detail"]["problems"])

    bad_pattern = client.post(
        "/api/cases/run", json={"case_ids": ["case_addr"], "params": {"meter_addr": "abc"}}
    )
    assert bad_pattern.status_code == 422, bad_pattern.text
    assert any("不匹配" in item for item in bad_pattern.json()["detail"]["problems"])


def test_run_params_are_propagated_to_blocks_and_facts(client):
    job = _run(
        client,
        {"case_ids": ["case_addr"], "params": {"meter_addr": "123456789012"}},
    )
    assert job["state"] == "done" and job["verdict"] == "pass"
    assert job["params"] == {"meter_addr": "123456789012"}
    # 参数确实传到了事实探测体（不是只存在于 API 的字段里）
    assert "yes" in {state for state, _params in CALLS["probes"]}
    assert any(params == {"meter_addr": "123456789012"}
               for state, params in CALLS["probes"] if state == "yes")


# --------------------------------------------------------------------------- #
# 断点字段（UI-5）
# --------------------------------------------------------------------------- #


def test_breakpoint_fields_are_complete(client):
    """必须能一眼看出「断在哪个块、哪个事实没满足」。"""
    job = _run(client, {"case_ids": ["case_skip"]})
    case = job["results"][0]
    assert case["failed_block"] == ""  # 没有 fail（跳过不是失败）
    assert case["blocked_block"] == "fake.skip"  # 断点块
    assert case["blocked_step"] == "fake.skip"
    unmet = case["unmet_facts"]
    assert "fake.bench" in unmet
    # 每项必须同时带 state 与 detail（UI 直接展示「哪个事实没满足、为什么」）
    assert {"state", "detail", "evidence"} <= set(unmet["fake.bench"])
    assert unmet["fake.bench"]["state"] == "unknown"
    # 块的 facts 里也留着该事实的三态，UI 展开块即可见
    block = case["blocks"][0]
    assert block["facts"]["fake.bench"]["state"] == "unknown"
    assert block["facts"]["fake.bench"]["detail"]
    assert "fake.bench" in block["detail"]  # 跳过原因点名了未满足的事实


def test_failed_case_reports_failed_block(client):
    job = _run(client, {"case_ids": ["case_fail"]})
    case = job["results"][0]
    assert case["verdict"] == "fail"
    assert case["failed_block"] == "fake.fail"
    assert case["blocks"][0]["outcome"] == "fail"
    assert job["counts"]["pass"] == 0 and job["counts"]["fail"] == 1
    assert job["exit_code"] == 1


# --------------------------------------------------------------------------- #
# 工作台地址：注入 > 环境变量 > 请求 origin；永不写死 8790
# --------------------------------------------------------------------------- #


def _recording_transport(monkeypatch) -> list:
    """把三个能力适配器的默认传输换成「记 URL + 立即不可达」的桩（不连任何真台架）。"""

    def fake_transport(method, url, body, timeout):
        urls.append(url)
        raise OSError("connection refused (fake transport)")

    urls: list = []
    for name in ("simcon", "simeter", "module"):
        module = importlib.import_module("case_runner.capabilities.%s" % name)
        monkeypatch.setattr(module, "default_transport", fake_transport)
    return urls


@pytest.fixture()
def real_client(tmp_path, monkeypatch):
    """真声明根（15 块 / 8 事实 / 多用例 / 2 方案）+ 假传输：不连任何真台架。

    返回 ``(client, urls)``，``urls`` 是本次测试里能力层实际请求过的地址。
    """
    monkeypatch.delenv("WORKBENCH_BASE", raising=False)
    urls = _recording_transport(monkeypatch)
    app = FastAPI()
    app.include_router(
        create_cases_router(
            storage_dir=tmp_path / "storage",
            bridge_snapshot_provider=_bridge_snapshot,
        )
    )
    return TestClient(app), urls


def test_resolve_job_base_url_precedence(monkeypatch):
    monkeypatch.setenv("WORKBENCH_BASE", "http://from-env:1")
    assert resolve_job_base_url("http://body:2/", "http://origin:3") == ("http://body:2", "body")
    assert resolve_job_base_url(None, "http://origin:3") == ("http://from-env:1", "env")
    monkeypatch.delenv("WORKBENCH_BASE", raising=False)
    assert resolve_job_base_url(None, "http://origin:3/") == ("http://origin:3", "http")
    # 三者都没有才是空串（能力层报 no_base_url），绝不回退到任何写死端口
    assert resolve_job_base_url(None, "") == ("", "none")


def test_body_base_url_reaches_blocks_then_env_is_restored(client, monkeypatch):
    monkeypatch.delenv("WORKBENCH_BASE", raising=False)
    job = _run(
        client,
        {"case_ids": ["case_env"], "base_url": "http://127.0.0.1:9/"},
    )
    assert job["verdict"] == "pass"
    assert job["base_url"] == "http://127.0.0.1:9"
    assert job["base_url_source"] == "body"
    # 块执行期确实读到注入地址（能力层自建实例只认这个环境变量）
    assert CALLS["env"][-1] == "http://127.0.0.1:9"
    # 结束后原样还原，不污染进程
    assert os.environ.get("WORKBENCH_BASE") is None


def test_bench_unreachable_real_declarations_preflight_blocks_at_origin(real_client):
    """真声明（bench_ready 需要 bench.sta_online）+ 不可达台架 → 预检直接 blocked。"""
    client, urls = real_client
    resp = client.post(
        "/api/cases/run",
        json={"case_ids": ["bench_ready"], "base_url": "http://127.0.0.1:9"},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["accepted"] is False and result["state"] == "blocked"
    assert result["job_id"] == ""
    assert set(result["unmet_facts"]) == {"bench.sta_online"}
    assert result["unmet_facts"]["bench.sta_online"]["state"] == "unknown"
    # 预检确实发出过台架探测请求（被假传输拦下），且只打注入的地址
    assert urls and all(url.startswith("http://127.0.0.1:9") for url in urls)
    assert not any("8790" in url for url in urls)


def test_real_declarations_without_preflight_all_skipped(real_client):
    """关掉预检后逐用例跑：块全 skipped/unknown → 用例 blocked，``counts.pass == 0``。"""
    client, urls = real_client
    resp = client.post(
        "/api/cases/run",
        json={
            "case_ids": ["bench_ready"],
            "base_url": "http://127.0.0.1:9",
            "no_preflight": True,
        },
    )
    assert resp.status_code == 202, resp.text
    job = _wait_done(client, resp.json()["job_id"], timeout=60)

    assert job["state"] == "done"
    assert job["verdict"] == "blocked"
    assert job["counts"]["pass"] == 0  # 防假 PASS 的核心断言
    assert job["counts"]["fail"] == 0  # 不可达不是设备失败
    assert job["counts"]["total"] > 0
    states = {blk["state"] for case in job["results"] for blk in case["blocks"]}
    assert states and states <= {"skipped", "unknown"}
    assert urls and all(url.startswith("http://127.0.0.1:9") for url in urls)

    case = job["results"][0]
    assert case["verdict"] == "blocked"
    assert case["blocked_block"]  # 断点块可读
    assert case["unmet_facts"]  # 未满足事实可读，且带 state/detail
    for value in case["unmet_facts"].values():
        assert value["state"] in ("no", "unknown")
        assert value["detail"]


def test_no_base_url_injected_never_targets_8790(real_client):
    """未注入 base_url + 无环境变量 → 打到本次请求的 origin，绝不写死 8790。"""
    client, urls = real_client
    resp = client.post("/api/cases/run", json={"case_ids": ["bench_ready"]})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["state"] == "blocked"
    assert result["base_url_source"] == "http"
    assert result["base_url"] == "http://testserver"
    assert urls, "台架探测应真的发出过请求（被假传输拦下）"
    assert all(url.startswith("http://testserver") for url in urls)
    assert not any("8790" in url for url in urls)


def test_env_base_url_is_used_when_body_absent(real_client, monkeypatch):
    client, urls = real_client
    monkeypatch.setenv("WORKBENCH_BASE", "http://bench.invalid:1234")
    resp = client.post("/api/cases/run", json={"case_ids": ["bench_ready"]})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["state"] == "blocked"
    assert result["base_url_source"] == "env"
    assert result["base_url"] == "http://bench.invalid:1234"
    assert urls and all(url.startswith("http://bench.invalid:1234") for url in urls)
    assert not any("8790" in url for url in urls)


# --------------------------------------------------------------------------- #
# 挂载守卫：/api/cases 挂上后 /api/autotest/* 不回归
# --------------------------------------------------------------------------- #


def test_cases_router_coexists_with_autotest_router(tmp_path):
    """两个路由挂同一个 app：``/api/autotest/*`` 的端点一个都没被顶掉。"""
    from workbench.autotest_api import create_autotest_router

    app = FastAPI()
    app.include_router(create_autotest_router(storage_dir=tmp_path / "storage"))
    app.include_router(
        create_cases_router(storage_dir=tmp_path / "storage", bridge_snapshot_provider=_bridge_snapshot)
    )
    client = TestClient(app)
    autotest_paths = [
        "/api/autotest/status",
        "/api/autotest/list",
    ]
    for path in autotest_paths:
        resp = client.get(path)
        # autotest 框架根在此环境可能不存在（available=false），但**端点必须在**
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
    # 两个前缀都还在（404 而不是 405/被覆盖）
    assert client.get("/api/autotest/nonexistent").status_code == 404
    assert client.get("/api/cases").status_code == 200


def test_app_py_mounts_cases_router_right_after_autotest():
    """``app.py`` 必须紧跟 autotest 之后挂载，且删掉「未挂载」的旧注释。"""
    source = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
    assert "create_cases_router(storage_dir=storage_dir)" in source
    assert "未挂载" not in source
    assert source.index("create_autotest_router") < source.index("create_cases_router")


# --------------------------------------------------------------------------- #
# 台架快照预算守卫（2026-11-23 真机回归）
# --------------------------------------------------------------------------- #
def test_bench_snapshot_budget_is_separate_from_preflight_budget() -> None:
    """``/api/cases/bench`` 必须有自己的、明显更大的探测预算。

    真机回归：快照要串行探完 8 条事实（实测 8–12s），若沿用预检的 5s 预算，后 3 条会被截成
    ``unknown`` → ``ready=false`` → UI 误把运行按钮置灰（台架其实可用，实际需要的 5 条事实全是 yes）。
    """
    from workbench import cases_api

    assert cases_api.BENCH_SNAPSHOT_BUDGET_S >= 15.0, (
        "台架快照预算过小：8 条事实串行探测会被截断成 unknown，导致 ready=false"
    )
    assert cases_api.BENCH_SNAPSHOT_BUDGET_S > cases_api.PREFLIGHT_BUDGET_S, (
        "快照预算必须大于预检预算（预检只探 needs 并集，快照要探全部事实）"
    )
