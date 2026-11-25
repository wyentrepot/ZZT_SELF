"""workbench.autotest_api —— 用例自动测试端点（/api/autotest/*）。

把独立 autotest/ 框架（runner.py + cases/ + chains.json，wb-casetest 技能沉淀）
以 UI 门面暴露：列出用例/链路、运行（case/chain/group/all）、job 进度、结果与文档。
与 AI 脚本同源：本路由只以 subprocess 调同一 runner CLI，不复制任何测试逻辑。

autotest 根目录按序解析（与 <WORKBENCH_ROOT> 同思路，单点命中即停）：
1. 环境变量 WORKBENCH_AUTOTEST_ROOT；
2. 配置文件 ~/.config/workbench/autotest-root（一行绝对路径）；
3. 仓库父目录下的 autotest/（<repo>/../autotest）。
解析不到 → available=false，页面显示引导而非报错。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_SUMMARY_RE = re.compile(r"汇总:\s*(\d+)/(\d+)\s*PASS")
_RUN_MODES = ("case", "chain", "group", "all")


class RunBody(BaseModel):
    mode: str
    ids: Optional[list[str]] = None
    name: Optional[str] = None


def resolve_autotest_root() -> Optional[Path]:
    env = os.environ.get("WORKBENCH_AUTOTEST_ROOT", "").strip()
    if env:
        p = Path(env)
        if (p / "runner.py").is_file():
            return p
    cfg = Path.home() / ".config" / "workbench" / "autotest-root"
    if cfg.is_file():
        p = Path(cfg.read_text(encoding="utf-8").strip())
        if (p / "runner.py").is_file():
            return p
    repo_parent = Path(__file__).resolve().parents[2].parent
    p = repo_parent / "autotest"
    if (p / "runner.py").is_file():
        return p
    return None


class AutotestRunner:
    """单并发 job 执行器：subprocess 调 autotest/runner.py，同 AI 脚本一条路。"""

    def __init__(self, storage_dir: Path):
        self.root = resolve_autotest_root()
        self.runs_dir = Path(storage_dir) / "autotest_runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._specs_cache: Optional[dict] = None
        self._specs_key: Optional[tuple] = None

    # ---- 规格加载（进程内 import，只读 CASE 元数据，不执行用例） ----
    def _runner_module(self):
        if self.root is None:
            return None
        spec = importlib.util.spec_from_file_location("wb_autotest_runner", self.root / "runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def specs(self) -> dict:
        if self.root is None:
            return {"available": False, "reason": "autotest 根目录未找到（env WORKBENCH_AUTOTEST_ROOT / ~/.config/workbench/autotest-root / <repo>/../autotest）"}
        try:
            runner_mod = self._runner_module()
            cases_mods = runner_mod.load_cases()
            chains = runner_mod.load_chains()
        except Exception as exc:  # noqa: BLE001 - 框架损坏则降级，不拖垮工作台
            return {"available": False, "reason": f"autotest 框架加载失败：{exc}"}

        idx = self._last_index()
        cases = []
        for cid, mod in cases_mods.items():
            meta = getattr(mod, "CASE", {}) or {}
            last = idx.get(cid)
            cases.append({
                "id": cid,
                "title": meta.get("title", cid),
                "supported": meta.get("supported", "?"),
                "supported_reason": meta.get("supported_reason", ""),
                "tags": meta.get("tags", []),
                "desc": meta.get("desc", ""),
                "how": meta.get("how", ""),
                "runnable": bool(getattr(mod, "run", None)) and meta.get("supported") != "error",
                "load_error": getattr(mod, "load_error", ""),
                "last": ({"ts": last["ts"], "passed": last["passed"], "detail": last.get("detail", "")}
                         if last else None),
            })
        chain_list = [{"name": name, "desc": ch.get("desc", ""),
                       "cases": ch.get("cases", []),
                       "stop_on_fail": ch.get("stop_on_fail", True)}
                      for name, ch in chains.items()]
        return {"available": True, "root": str(self.root), "cases": cases, "chains": chain_list}

    def _last_index(self) -> dict:
        idx: dict[str, dict] = {}
        if self.root is None:
            return idx
        ip = self.root / "results" / "index.jsonl"
        if ip.is_file():
            for line in ip.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    idx[r["case_id"]] = r
                except Exception:  # noqa: BLE001 - 坏行跳过
                    pass
        return idx

    # ---- job 生命周期 ----
    def start(self, mode: str, ids: list[str], name: str) -> dict:
        with self._lock:
            busy = next((j for j in self.jobs.values() if j["status"] == "running"), None)
            if busy:
                raise HTTPException(status_code=409,
                                    detail=f"已有任务在跑：{busy['job_id']}（{busy['mode']} {busy['target']}），等它结束再提")
            if self.root is None:
                raise HTTPException(status_code=409, detail="autotest 框架不可用（根目录未解析到）")
            if mode == "case":
                if not ids:
                    raise HTTPException(status_code=400, detail="mode=case 需要 ids")
                argv = [sys.executable, "runner.py", "--case", *ids]
                target = ",".join(ids)
            elif mode == "chain":
                chains = self._chains_or_404(name)
                argv = [sys.executable, "runner.py", "--chain", name]
                target = f"{name}({len(chains['cases'])}例)"
            elif mode == "group":
                if not name:
                    raise HTTPException(status_code=400, detail="mode=group 需要 name(tag)")
                argv = [sys.executable, "runner.py", "--group", name]
                target = f"tag:{name}"
            elif mode == "all":
                argv = [sys.executable, "runner.py", "--all"]
                target = "全部用例"
            else:
                raise HTTPException(status_code=400, detail=f"mode 只支持 {_RUN_MODES}")

            job_id = time.strftime("%Y%m%d_%H%M%S")
            log_path = self.runs_dir / f"{job_id}.log"
            log_f = open(log_path, "w", encoding="utf-8")
            proc = subprocess.Popen(argv, cwd=str(self.root), stdout=log_f,
                                    stderr=subprocess.STDOUT)
            job = {"job_id": job_id, "mode": mode, "target": target, "argv": argv[2:],
                   "status": "running", "exit_code": None, "started": time.time(),
                   "start_ts": time.strftime("%Y%m%d_%H%M%S"), "finished": None,
                   "log_path": str(log_path), "proc": proc}
            self.jobs[job_id] = job
            threading.Thread(target=self._wait, args=(job, log_f), daemon=True).start()
            return {"job_id": job_id, "mode": mode, "target": target}

    def _chains_or_404(self, name: str) -> dict:
        try:
            runner_mod = self._runner_module()
            chains = runner_mod.load_chains()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"chains.json 读取失败：{exc}") from exc
        if name not in chains:
            raise HTTPException(status_code=404, detail=f"链路不存在：{name}；可用：{list(chains)}")
        return chains[name]

    def _wait(self, job: dict, log_f) -> None:
        proc = job["proc"]
        code = proc.wait()
        log_f.close()
        # exit_code 非零 =有用例 FAIL（runner 约定），属正常终态；status 只区分跑完/异常退出
        job["exit_code"] = code
        job["status"] = "done"
        job["finished"] = time.time()

    def status_of(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job 不存在：{job_id}")
        return self._job_view(job)

    def current(self) -> Optional[dict]:
        for job in reversed(list(self.jobs.values())):
            if job["status"] == "running":
                return self._job_view(job)
        return None

    def _job_view(self, job: dict) -> dict:
        tail: list[str] = []
        log_path = Path(job["log_path"])
        if log_path.is_file():
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
        results = [r for r in self._last_index().values()
                   if r.get("ts", "") >= job.get("start_ts", "9999")]
        summary = None
        for line in reversed(tail):
            m = _SUMMARY_RE.search(line)
            if m:
                summary = {"pass": int(m.group(1)), "total": int(m.group(2))}
                break
        return {"job_id": job["job_id"], "mode": job["mode"], "target": job["target"],
                "status": job["status"], "exit_code": job["exit_code"],
                "has_fail": bool(job["exit_code"]),
                "started": job["started"], "finished": job["finished"],
                "summary": summary, "results": results, "log_tail": tail}

    # ---- 结果与文档 ----
    def latest_result(self, case_id: str) -> dict:
        if self.root is None:
            raise HTTPException(status_code=409, detail="autotest 框架不可用")
        cands = sorted((self.root / "results").glob(f"*_{case_id}.json"))
        if not cands:
            raise HTTPException(status_code=404, detail=f"用例 {case_id} 无历史结果")
        return json.loads(cands[-1].read_text(encoding="utf-8"))

    def doc(self, case_id: str) -> str:
        if self.root is None:
            raise HTTPException(status_code=409, detail="autotest 框架不可用")
        p = self.root / "docs" / f"{case_id}.md"
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"用例 {case_id} 无文档")
        return p.read_text(encoding="utf-8")


def create_autotest_router(storage_dir: Path) -> APIRouter:
    """创建 /api/autotest/* 路由（storage_dir：job 日志目录的父目录）。"""
    runner = AutotestRunner(storage_dir=storage_dir)

    rt = APIRouter(prefix="/api/autotest")

    @rt.get("/status")
    def autotest_status():
        cur = runner.current()
        return {"available": runner.root is not None,
                "root": str(runner.root) if runner.root else None,
                "busy": cur is not None, "current_job": cur}

    @rt.get("/list")
    def autotest_list():
        return runner.specs()

    @rt.post("/run")
    def autotest_run(body: RunBody):
        return runner.start(body.mode, body.ids or [], body.name or "")

    @rt.get("/job/{job_id}")
    def autotest_job(job_id: str):
        return runner.status_of(job_id)

    @rt.get("/result/{case_id}/latest")
    def autotest_result(case_id: str):
        return runner.latest_result(case_id)

    @rt.get("/doc/{case_id}")
    def autotest_doc(case_id: str):
        return {"case_id": case_id, "doc": runner.doc(case_id)}

    return rt
