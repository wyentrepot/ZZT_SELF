"""autotest_api：/api/autotest/* 的清单、运行 job 生命周期与降级测试。

用 stub autotest 根（临时 runner.py + cases/ + chains.json）隔离真实框架，
验证 UI 门面与 runner CLI 的同源语义：subprocess 调用、单并发 409、结果解析。
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workbench.autotest_api import create_autotest_router

_STUB_RUNNER = '''#!/usr/bin/env python3
import argparse, json, os, sys, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

def load_cases():
    cases = {}
    for fn in sorted(os.listdir(os.path.join(HERE, "cases"))):
        if fn.endswith(".py") and not fn.startswith("_"):
            cid = fn[:-3]
            ns = {"run": lambda ctx: (True, "stub")}
            exec(compile(open(os.path.join(HERE, "cases", fn)).read(), fn, "exec"), ns)
            cases[cid] = types.SimpleNamespace(**ns)
    return cases

def load_chains():
    p = os.path.join(HERE, "chains.json")
    if os.path.exists(p):
        return json.load(open(p))
    return {}

def run_case(cid, meta, fail):
    os.makedirs(RESULTS, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    time.sleep(float(os.environ.get("STUB_SLEEP", "0.05")))
    detail = "stub ok" if not fail else "stub fail"
    res = {"ts": ts, "case_id": cid, "passed": not fail, "detail": detail}
    with open(os.path.join(RESULTS, f"{ts}_{cid}.json"), "w") as f:
        json.dump(res, f)
    with open(os.path.join(RESULTS, "index.jsonl"), "a") as f:
        f.write(json.dumps(res) + "\\n")
    print(f"  {'PASS' if not fail else 'FAIL'}  {cid}  {detail}")
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--case", nargs="+")
    ap.add_argument("--chain")
    ap.add_argument("--group")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    cases = load_cases()
    if args.chain:
        chains = json.load(open(os.path.join(HERE, "chains.json")))
        ch = chains.get(args.chain)
        if not ch:
            print(f"链路不存在: {args.chain};可用: {list(chains)}")
            sys.exit(2)
        ids = ch["cases"]
    elif args.case:
        ids = args.case
    elif args.group:
        ids = [c for c, m in cases.items() if args.group in getattr(m, "CASE", {}).get("tags", [])]
    elif args.all:
        ids = list(cases)
    else:
        ids = []
    results = [run_case(c, getattr(cases.get(c), "CASE", {}), c.endswith("fail")) for c in ids]
    npass = sum(1 for r in results if r["passed"])
    print(f"\\n===== 汇总: {npass}/{len(results)} PASS =====")
    sys.exit(0 if npass == len(results) else 1)

if __name__ == "__main__":
    main()
'''


@pytest.fixture()
def autotest_env(tmp_path, monkeypatch):
    root = tmp_path / "autotest"
    (root / "cases").mkdir(parents=True)
    (root / "runner.py").write_text(_STUB_RUNNER, encoding="utf-8")
    (root / "cases" / "stub_ok.py").write_text(
        'CASE = {"id": "stub_ok", "title": "桩通过例", "supported": "yes", "tags": ["stub"]}\n', encoding="utf-8")
    (root / "cases" / "stub_fail.py").write_text(
        'CASE = {"id": "stub_fail", "title": "桩失败例", "supported": "partial", "tags": ["stub"]}\n', encoding="utf-8")
    (root / "chains.json").write_text(json.dumps({
        "stub-chain": {"desc": "桩链路", "stop_on_fail": False,
                        "cases": ["stub_ok", "stub_fail"]}}), encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_AUTOTEST_ROOT", str(root))

    app = FastAPI()
    app.include_router(create_autotest_router(storage_dir=tmp_path / "storage"))
    return {"client": TestClient(app), "root": root}


def _wait_done(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/autotest/job/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("job 未在超时内结束")


def test_list_returns_meta_and_last_result(autotest_env):
    client, root = autotest_env["client"], autotest_env["root"]
    # 预置一条历史结果
    res_dir = root / "results"
    res_dir.mkdir()
    (res_dir / "20260101_000000_stub_ok.json").write_text(json.dumps(
        {"ts": "20260101_000000", "case_id": "stub_ok", "passed": True, "detail": "旧结果"}), encoding="utf-8")
    with open(res_dir / "index.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "20260101_000000", "case_id": "stub_ok",
                            "passed": True, "detail": "旧结果"}) + "\n")
    data = client.get("/api/autotest/list").json()
    assert data["available"] is True
    ids = {c["id"] for c in data["cases"]}
    assert {"stub_ok", "stub_fail"} <= ids
    ok = next(c for c in data["cases"] if c["id"] == "stub_ok")
    assert ok["title"] == "桩通过例" and ok["supported"] == "yes"
    assert ok["last"]["passed"] is True
    assert data["chains"][0]["name"] == "stub-chain"


def test_run_case_job_lifecycle(autotest_env):
    client = autotest_env["client"]
    resp = client.post("/api/autotest/run", json={"mode": "case", "ids": ["stub_ok"]})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    job = _wait_done(client, job_id)
    assert job["status"] == "done" and job["exit_code"] == 0
    assert job["summary"] == {"pass": 1, "total": 1}
    assert job["results"][0]["case_id"] == "stub_ok" and job["results"][0]["passed"] is True


def test_run_chain_mixed_exit_code(autotest_env):
    client = autotest_env["client"]
    resp = client.post("/api/autotest/run", json={"mode": "chain", "name": "stub-chain"})
    job = _wait_done(client, resp.json()["job_id"])
    # 一过一败：exit=1（done 但非零，页面按汇总展示）
    assert job["status"] == "done"
    assert job["exit_code"] == 1
    assert job["summary"] == {"pass": 1, "total": 2}


def test_busy_conflict_409(autotest_env, monkeypatch):
    monkeypatch.setenv("STUB_SLEEP", "1.2")
    client = autotest_env["client"]
    first = client.post("/api/autotest/run", json={"mode": "case", "ids": ["stub_ok"]})
    second = client.post("/api/autotest/run", json={"mode": "all"})
    assert second.status_code == 409
    _wait_done(client, first.json()["job_id"])


def test_validation_errors(autotest_env):
    client = autotest_env["client"]
    assert client.post("/api/autotest/run", json={"mode": "case"}).status_code == 400
    assert client.post("/api/autotest/run", json={"mode": "wat"}).status_code == 400
    assert client.post("/api/autotest/run", json={"mode": "chain", "name": "nope"}).status_code == 404
    assert client.get("/api/autotest/job/never").status_code == 404


def test_result_and_doc_404_when_absent(autotest_env):
    client = autotest_env["client"]
    assert client.get("/api/autotest/result/stub_ok/latest").status_code == 404
    assert client.get("/api/autotest/doc/stub_ok").status_code == 404
