"""workbench.orchestration.store —— RunStore：SQLite 元数据 + 报告 JSON 归档。

数据目录：data/runs.sqlite + data/reports/{run_id}.json。
frozen（PyInstaller）模式下落在 exe 同目录 runtime/（沿用 _base_dir /
_runtime_dir 约定，ADR-2/3）。

归档策略：报告 JSON 不可变、run 元数据可更新（status 流转）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Run, RunStep

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id       TEXT PRIMARY KEY,
  scenario_id  TEXT NOT NULL,
  status       TEXT NOT NULL,
  firmware_ver TEXT,
  firmware_commit TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  report_path  TEXT
);
CREATE TABLE IF NOT EXISTS run_steps (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL REFERENCES runs(run_id),
  seq        INTEGER NOT NULL,
  kind       TEXT NOT NULL,
  detail     TEXT,
  result     TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id);
"""


def _base_dir() -> Path:
    """数据根目录：仓库根 data/；frozen 时 exe 同目录 data/。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data"
    return Path(__file__).resolve().parent.parent.parent.parent / "data"


class RunStore:
    """Run 元数据持久化 + 报告归档。"""

    def __init__(self, db_path: Optional[Path] = None, reports_dir: Optional[Path] = None):
        self._lock = threading.Lock()
        base = Path(db_path) if db_path else _base_dir()
        self.db_path = base / "runs.sqlite" if db_path is None else base
        self.reports_dir = reports_dir or (self.db_path.parent / "reports")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 写 ----------------

    def create_run(self, run: Run) -> Run:
        with self._lock:
            # 毫秒级时间戳：list_runs 按 created_at 倒序需稳定排序（同秒多条时）
            now = datetime.now().isoformat(timespec="milliseconds")
            self._conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(run_id, scenario_id, status, firmware_ver, firmware_commit, created_at, updated_at, report_path) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    run.scenario_id,
                    run.status,
                    run.firmware.version,
                    run.firmware.commit,
                    now,
                    now,
                    run.report_path,
                ),
            )
            self._conn.commit()
        return run

    def update_status(self, run_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                (status, datetime.now().isoformat(timespec="milliseconds"), run_id),
            )
            self._conn.commit()

    def add_step(self, run_id: str, step: RunStep) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO run_steps (run_id, seq, kind, detail, result) VALUES (?,?,?,?,?)",
                (run_id, step.seq, step.kind, step.detail, step.result),
            )
            self._conn.commit()

    def save_report(self, run_id: str, report: Dict[str, Any]) -> Path:
        """报告 JSON 不可变归档，返回路径。"""
        path = self.reports_dir / f"{run_id}.json"
        with self._lock:
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._conn.execute(
                "UPDATE runs SET report_path=?, updated_at=? WHERE run_id=?",
                (str(path), datetime.now().isoformat(timespec="seconds"), run_id),
            )
            self._conn.commit()
        return path

    # ---------------- 读 ----------------

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                return None
            cols = [d[0] for d in self._conn.execute("SELECT * FROM runs LIMIT 0").description]
            run = dict(zip(cols, row))
            run["steps"] = self._get_steps(run_id)
            return run

    def list_runs(self, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, scenario_id, status, firmware_ver, firmware_commit, "
                "created_at, updated_at, report_path FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            cols = ["run_id", "scenario_id", "status", "firmware_ver",
                    "firmware_commit", "created_at", "updated_at", "report_path"]
            return [dict(zip(cols, r)) for r in rows]

    def get_report(self, run_id: str) -> Optional[dict]:
        path = self.reports_dir / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _get_steps(self, run_id: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT seq, kind, detail, result FROM run_steps WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [{"seq": r[0], "kind": r[1], "detail": r[2], "result": r[3]} for r in rows]
