"""dict_api 检测用例库端点测试（REQS-0025 G1）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workbench.dict_api import router


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_dict_list_contains_cases(client):
    r = client.get("/api/dict")
    r.raise_for_status()
    dicts = {d["id"]: d for d in r.json()}
    assert set(dicts) == {"oad", "di", "afn-fn", "rules", "cases"}
    cases = dicts["cases"]
    assert cases["count"] > 200
    assert cases["case_count"] >= 200
    assert "269" in cases["desc"]


def test_cases_list_and_filters(client):
    r = client.get("/api/dict/cases")
    r.raise_for_status()
    data = r.json()
    assert data["dict"] == "cases"
    assert data["declared_total"] == 269
    assert data["count"] == len(data["items"])
    cat_ids = {c["id"] for c in data["categories"]}
    assert {"hplc-perf", "hplc-consistency", "interop", "params"} <= cat_ids

    r2 = client.get("/api/dict/cases", params={"category": "interop"})
    assert r2.json()["count"] == 11
    r3 = client.get("/api/dict/cases", params={"type": "param_table"})
    assert r3.json()["count"] == 42
    r4 = client.get("/api/dict/cases", params={"q": "信标"})
    assert r4.json()["count"] > 0


def test_case_detail_and_404(client):
    r = client.get("/api/dict/cases/HPLC-PERF-02")
    r.raise_for_status()
    entry = r.json()
    assert entry["name"] == "抗白噪声"
    assert entry["source"]["section"].startswith("§2.")
    assert client.get("/api/dict/cases/NO-SUCH-ID").status_code == 404
