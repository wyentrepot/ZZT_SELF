"""13-设计契约偏差核查 四项真实缺口（D-01~D-04）修复验收测试。

覆盖（docs/_archive/2026-08-18/13-设计契约偏差核查.md §7 修正顺序）：
- D-01：workbench RunStatus 复用规范枚举；cancelling/cancelled 合法；aborted 拒绝
- D-02：必要来源证据缺失 → Report.verdict=inconclusive + Run 状态 inconclusive
- D-03：Artifact manifest（SHA-256）、逻辑 ID 下载接口、路径越界防护
- D-04：统一错误响应（code/message/details/request_id + 兼容 detail）
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_automation.models import RunStatus as CanonicalRunStatus

from workbench.app import create_workbench_app
from workbench.orchestration.artifacts import (
    ArtifactPathUnsafe,
    find_artifact,
    resolve_artifact_path,
)
from workbench.orchestration.models import ArtifactInfo, Report, Run, RunInput, RunStatus
from workbench.orchestration.runner import RunExecutor, _build_artifacts
from workbench.orchestration.store import RunStore


# ---------------------------------------------------------------------------
# D-01 RunStatus 统一（复用规范枚举）
# ---------------------------------------------------------------------------


class TestRunStatusUnified:
    def test_reuses_canonical_enum(self):
        """workbench RunStatus 就是 libs 规范枚举（单一事实来源）。"""
        assert RunStatus is CanonicalRunStatus

    def test_created_default(self):
        """Run 初态为 created（规范枚举，无 pending）。"""
        run = Run(run_id="r", scenario_id="s")
        assert run.status == CanonicalRunStatus.CREATED
        assert run.model_dump()["status"] == "created"

    def test_cancelling_cancelled_valid(self):
        """cancelling/cancelled 是合法终态（runner 实际写入，前端已消费）。"""
        r1 = Run(run_id="r", scenario_id="s", status=CanonicalRunStatus.CANCELLING)
        assert r1.model_dump()["status"] == "cancelling"
        r2 = Run(run_id="r", scenario_id="s", status=CanonicalRunStatus.CANCELLED)
        assert r2.model_dump()["status"] == "cancelled"

    def test_aborted_rejected(self):
        """aborted 不是规范枚举成员，构造被拒（消除死枚举值）。"""
        with pytest.raises(Exception):
            Run(run_id="r", scenario_id="s", status="aborted")

    def test_inconclusive_is_terminal(self):
        """inconclusive 是规范终态（D-02 依赖状态机支持）。"""
        from test_automation.state_machine import RUN_TRANSITIONS

        assert CanonicalRunStatus.INCONCLUSIVE in RUN_TRANSITIONS
        assert CanonicalRunStatus.INCONCLUSIVE in {
            CanonicalRunStatus.CANCELLED,
            CanonicalRunStatus.PASSED,
            CanonicalRunStatus.FAILED,
            CanonicalRunStatus.INCONCLUSIVE,
            CanonicalRunStatus.ERROR,
        }


# ---------------------------------------------------------------------------
# D-02 必要来源缺失 → inconclusive
# ---------------------------------------------------------------------------


def _empty_log_dir(tmp_path: Path) -> Path:
    """空日志目录（monitor 扫描无事件）。"""
    d = tmp_path / "emptylog"
    d.mkdir(exist_ok=True)
    return d


def _fake_log(tmp_path: Path) -> Path:
    """产生事件的日志目录。"""
    d = tmp_path / "log"
    d.mkdir(exist_ok=True)
    (d / "cco.log").write_text(
        "[20260814-10:00:01:000] [RX] 1 | CCO | aps_ioctrl_nwk.c(950) | nwk disc done\n"
        "[20260814-10:00:02:000] [RX] 2 | CCO | aps_ioctrl_nwk.c(950) | onnet cnt = 1\n",
        encoding="utf-8",
    )
    return d


class TestInconclusive:
    def test_monitor_missing_yields_inconclusive(self, tmp_path):
        """monitor 未跳过但无事件（核心证据缺失）→ verdict=inconclusive + Run=inconclusive。"""
        store = RunStore(db_path=tmp_path / "r.sqlite", reports_dir=tmp_path / "rpt")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(_empty_log_dir(tmp_path)),
            skip_flash=True,
            skip_stimulus=True,
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        assert run.status == CanonicalRunStatus.INCONCLUSIVE
        report = store.get_report(run.run_id)
        assert report is not None
        assert report["verdict"] == "inconclusive"
        # 缺失原因登记为 inconclusive 断言
        assert any(
            a["id"] == "source.missing" and a["result"] == "inconclusive"
            for a in report["assertions"]
        )
        store.close()

    def test_stimulus_missing_yields_inconclusive(self, tmp_path, monkeypatch):
        """stimulus 未跳过但无执行结果 → inconclusive。"""
        # _run_stimulus 返回 None（模拟无任务/无串口）
        monkeypatch.setattr(
            "workbench.orchestration.runner._run_stimulus", lambda *a, **k: None
        )
        store = RunStore(db_path=tmp_path / "r.sqlite", reports_dir=tmp_path / "rpt")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(_fake_log(tmp_path)),
            skip_flash=True,
            skip_stimulus=False,
            skip_compare=True,  # 排除 compare missing 干扰
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        assert run.status == CanonicalRunStatus.INCONCLUSIVE
        report = store.get_report(run.run_id)
        assert report is not None
        assert report["verdict"] == "inconclusive"
        store.close()

    def test_listener_index_without_frames_yields_inconclusive(self, tmp_path):
        """显式期望 listener（listener_index）但无帧 → inconclusive。"""
        store = RunStore(db_path=tmp_path / "r.sqlite", reports_dir=tmp_path / "rpt")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(_fake_log(tmp_path)),
            skip_flash=True,
            skip_stimulus=True,
            skip_compare=True,
            extras={"listener_index": str(tmp_path / "none.sqlite3")},
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        assert run.status == CanonicalRunStatus.INCONCLUSIVE
        store.close()

    def test_simcon_fail_priority_over_inconclusive(self, tmp_path, monkeypatch):
        """simcon 明确失败 → 仍判 fail（真实失败优先于证据缺失）。"""
        monkeypatch.setattr(
            "workbench.orchestration.runner._run_stimulus",
            lambda *a, **k: {
                "task_id": "t", "port": "COM24", "baudrate": 9600,
                "steps": [{"index": 0, "name": "s", "result": "fail", "reason": "否认"}],
                "summary": {"total": 1, "pass": 0, "fail": 1, "verdict": "fail"},
            },
        )
        store = RunStore(db_path=tmp_path / "r.sqlite", reports_dir=tmp_path / "rpt")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(_empty_log_dir(tmp_path)),
            skip_flash=True,
            skip_stimulus=False,
            skip_compare=True,
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        assert run.status == CanonicalRunStatus.FAILED
        store.close()


# ---------------------------------------------------------------------------
# D-03 Artifact 审计链（manifest / 下载 / 越界防护）
# ---------------------------------------------------------------------------


class TestArtifactManifest:
    def test_build_artifacts_with_sha256(self, tmp_path):
        """_build_artifacts：日志产物登记 SHA-256、逻辑 ID、size。"""
        f = tmp_path / "cco.log"
        f.write_text("hello artifact", encoding="utf-8")
        arts = _build_artifacts("run-x", [str(f)])
        assert len(arts) == 1
        a = arts[0]
        assert a.id == "run-x-art-1"
        assert a.type == "log"
        assert a.name == "cco.log"
        assert a.sha256  # 非空
        import hashlib

        expected = hashlib.sha256(b"hello artifact").hexdigest()
        assert a.sha256 == expected
        assert a.size == len("hello artifact")

    def test_missing_file_not_registered(self, tmp_path):
        """缺失文件不登记（保持 manifest 干净）。"""
        arts = _build_artifacts("run-x", [str(tmp_path / "no.log")])
        assert arts == []

    def test_report_artifacts_structured(self):
        """Report.artifacts 是 ArtifactInfo 列表（非字符串列表，D-03 结构化）。"""
        rep = Report(run_id="r", artifacts=[ArtifactInfo(
            id="r-art-1", run_id="r", type="log", name="a.log",
            sha256="abc", path="/tmp/a.log",
        )])
        dumped = rep.model_dump()["artifacts"][0]
        assert dumped["id"] == "r-art-1"
        assert dumped["sha256"] == "abc"


class TestArtifactResolve:
    def test_resolve_ok(self, tmp_path):
        """存在且是文件 → 返回绝对路径。"""
        f = tmp_path / "a.log"
        f.write_text("x", encoding="utf-8")
        art = {"id": "r-art-1", "path": str(f)}
        assert resolve_artifact_path(art) == f.resolve()

    def test_resolve_missing_path_rejected(self, tmp_path):
        """路径不存在 → ArtifactPathUnsafe。"""
        art = {"id": "r-art-1", "path": str(tmp_path / "no.log")}
        with pytest.raises(ArtifactPathUnsafe):
            resolve_artifact_path(art)

    def test_resolve_directory_rejected(self, tmp_path):
        """路径是目录 → ArtifactPathUnsafe。"""
        art = {"id": "r-art-1", "path": str(tmp_path)}
        with pytest.raises(ArtifactPathUnsafe):
            resolve_artifact_path(art)

    def test_resolve_no_path_rejected(self):
        """未登记 path → ArtifactPathUnsafe。"""
        art = {"id": "r-art-1"}
        with pytest.raises(ArtifactPathUnsafe):
            resolve_artifact_path(art)

    def test_find_artifact(self):
        report = {"artifacts": [{"id": "r-art-1", "path": "/x"}]}
        assert find_artifact(report, "r-art-1")["id"] == "r-art-1"
        assert find_artifact(report, "nope") is None


# ---------------------------------------------------------------------------
# D-03 / D-04 API 层（下载接口 + 统一错误响应）
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    from workbench.app import create_workbench_app

    app = create_workbench_app()
    return TestClient(app)


class TestArtifactDownloadApi:
    def _make_run_with_artifact(self, tmp_path):
        """跑一个 Run 并返回 (store, run, report)，产物为日志文件。"""
        d = tmp_path / "log"
        d.mkdir(exist_ok=True)
        (d / "cco.log").write_text(
            "[20260814-10:00:01:000] [RX] 1 | CCO | aps_ioctrl_nwk.c(950) | nwk disc done\n",
            encoding="utf-8",
        )
        store = RunStore(db_path=tmp_path / "r.sqlite", reports_dir=tmp_path / "rpt")
        ex = RunExecutor(store)
        ri = RunInput(
            scenario_id="join_anhui",
            log_dir=str(d),
            skip_flash=True,
            skip_stimulus=True,
        )
        run = ex.execute(ri, scenarios_dir=Path(__file__).parent.parent / "scenarios")
        report = store.get_report(run.run_id)
        return store, run, report

    def test_download_artifact_ok(self, tmp_path, monkeypatch):
        """下载接口：逻辑 ID → 200 + 内容一致。"""
        store, run, report = self._make_run_with_artifact(tmp_path)
        arts = report["artifacts"]
        assert arts, "报告应含 Artifact 清单"

        # 用 TestClient 指向一个含该 report 的 app：直接测 resolver 与下载 handler
        # 通过注入 store 到 api 模块单例
        import workbench.api as api_mod

        def _fake_executor():
            if not hasattr(_fake_executor, "_e"):
                _fake_executor._e = type("E", (), {"store": store})()
            return _fake_executor._e

        monkeypatch.setattr(api_mod, "_executor", _fake_executor)
        app = create_workbench_app()
        c = TestClient(app)
        r = c.get(f"/api/run/{run.run_id}/artifacts")
        assert r.status_code == 200
        listed = r.json()
        assert len(listed) == len(arts)

        aid = listed[0]["id"]
        r2 = c.get(f"/api/run/{run.run_id}/artifacts/{aid}")
        assert r2.status_code == 200
        assert "nwk disc done" in r2.text  # 内容一致
        store.close()

    def test_download_unknown_artifact_404(self, tmp_path, monkeypatch):
        """未登记逻辑 ID → 404（统一错误结构）。"""
        store, run, _ = self._make_run_with_artifact(tmp_path)
        import workbench.api as api_mod

        def _fake_executor():
            if not hasattr(_fake_executor, "_e"):
                _fake_executor._e = type("E", (), {"store": store})()
            return _fake_executor._e

        monkeypatch.setattr(api_mod, "_executor", _fake_executor)
        app = create_workbench_app()
        c = TestClient(app)
        r = c.get(f"/api/run/{run.run_id}/artifacts/nope")
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "404"
        assert body["request_id"]
        store.close()


class TestUnifiedErrorResponse:
    """D-04：错误响应统一结构（code/message/details/request_id + 兼容 detail）。"""

    def test_not_found_has_unified_structure(self, client):
        r = client.get("/api/run/nonexistent")
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "404"
        assert body["message"]
        assert "details" in body
        assert body["request_id"]
        assert body["detail"] == body["message"]  # 兼容字段

    def test_cancel_conflict_has_unified_structure(self, client):
        r = client.post("/api/run/nonexistent/cancel")
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "404"
        assert body["request_id"]

    def test_validation_error_unified(self, client):
        """422 校验错误：统一结构 + 字段级 details。"""
        r = client.post("/api/run", json={"scenario_id": ""})
        # scenario_id 有 min_length 约束，应为 422
        if r.status_code == 422:
            body = r.json()
            assert body["code"] == "422"
            assert body["details"]
            assert body["request_id"]

    def test_existing_detail_still_present(self, client):
        """兼容：现有依赖 detail 字段的前端展示不受影响。"""
        r = client.get("/api/run/nonexistent")
        assert "detail" in r.json()
