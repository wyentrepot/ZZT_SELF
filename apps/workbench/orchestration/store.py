"""Canonical Run/Report persistence with a legacy-compatible read projection."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from test_automation.models import (
    Artifact,
    AssertionResult,
    Report,
    Run,
    StepResult,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  status TEXT NOT NULL,
  firmware_ver TEXT,
  firmware_commit TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  report_path TEXT,
  case_id TEXT,
  case_version TEXT,
  case_fingerprint TEXT,
  parameters_json TEXT,
  resource_leases_json TEXT,
  started_at TEXT,
  finished_at TEXT,
  firmware_json TEXT,
  error TEXT,
  steps_json TEXT
);
CREATE TABLE IF NOT EXISTS run_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT,
  result TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id);
"""


def _base_dir() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data"
    return Path(__file__).resolve().parent.parent.parent.parent / "data"


class RunStore:
    """Store writes accept only canonical Run and Report instances."""

    def __init__(self, db_path: Path | None = None, reports_dir: Path | None = None):
        self._lock = threading.Lock()
        base = Path(db_path) if db_path else _base_dir()
        self.db_path = base / "runs.sqlite" if db_path is None else base
        self.reports_dir = reports_dir or (self.db_path.parent / "reports")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._migrate_runs_schema()
        self._conn.commit()

    def _migrate_runs_schema(self) -> None:
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(runs)")}
        columns = {
            "case_id": "TEXT",
            "case_version": "TEXT",
            "case_fingerprint": "TEXT",
            "parameters_json": "TEXT",
            "resource_leases_json": "TEXT",
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "firmware_json": "TEXT",
            "error": "TEXT",
            "steps_json": "TEXT",
        }
        for name, type_ in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {type_}")
        self._conn.execute(
            "UPDATE runs SET case_id=COALESCE(case_id, scenario_id), "
            "case_version=COALESCE(case_version, 'legacy'), "
            "case_fingerprint=COALESCE(case_fingerprint, 'legacy-unavailable'), "
            "parameters_json=COALESCE(parameters_json, '{}'), "
            "resource_leases_json=COALESCE(resource_leases_json, '[]'), "
            "steps_json=COALESCE(steps_json, '[]')"
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_run(self, run: Run) -> Run:
        if not isinstance(run, Run):
            raise TypeError("Store writes require canonical Run")
        self.update_canonical_run(run, create=True)
        return run

    def update_canonical_run(self, run: Run, *, create: bool = False) -> None:
        if not isinstance(run, Run):
            raise TypeError("Store writes require canonical Run")
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        firmware = dict(run.parameters.get("firmware") or {})
        now = datetime.now().isoformat(timespec="milliseconds")
        values = (
            run.id,
            run.case_id,
            status,
            firmware.get("version"),
            firmware.get("commit"),
            run.created_at.isoformat() if run.created_at else now,
            now,
            None,
            run.case_id,
            run.case_version,
            run.case_fingerprint,
            json.dumps(run.parameters, ensure_ascii=False),
            json.dumps([item.to_dict() for item in run.resource_leases], ensure_ascii=False),
            run.started_at.isoformat() if run.started_at else None,
            run.finished_at.isoformat() if run.finished_at else None,
            json.dumps(firmware, ensure_ascii=False),
            run.error,
            json.dumps([item.to_dict() for item in run.steps], ensure_ascii=False),
        )
        with self._lock:
            if create:
                self._conn.execute(
                    "INSERT OR REPLACE INTO runs "
                    "(run_id, scenario_id, status, firmware_ver, firmware_commit, created_at, "
                    "updated_at, report_path, case_id, case_version, case_fingerprint, "
                    "parameters_json, resource_leases_json, started_at, finished_at, "
                    "firmware_json, error, steps_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            else:
                self._conn.execute(
                    "UPDATE runs SET scenario_id=?, status=?, firmware_ver=?, firmware_commit=?, "
                    "created_at=?, updated_at=?, case_id=?, case_version=?, case_fingerprint=?, "
                    "parameters_json=?, resource_leases_json=?, started_at=?, finished_at=?, "
                    "firmware_json=?, error=?, steps_json=? WHERE run_id=?",
                    (
                        values[1], values[2], values[3], values[4], values[5], values[6],
                        values[8], values[9], values[10], values[11], values[12], values[13],
                        values[14], values[15], values[16], values[17], run.id,
                    ),
                )
            self._conn.commit()

    def get_canonical_run(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return None
            columns = [item[0] for item in self._conn.execute("SELECT * FROM runs LIMIT 0").description]
        data = dict(zip(columns, row))
        return Run.from_dict(
            {
                "id": data["run_id"],
                "case_id": data.get("case_id") or data["scenario_id"],
                "case_version": data.get("case_version") or "legacy",
                "case_fingerprint": data.get("case_fingerprint") or "legacy-unavailable",
                "status": data["status"],
                "parameters": json.loads(data.get("parameters_json") or "{}"),
                "resource_leases": json.loads(data.get("resource_leases_json") or "[]"),
                "error": data.get("error"),
                "created_at": data.get("created_at"),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "steps": json.loads(data.get("steps_json") or "[]"),
                "report_path": data.get("report_path"),
            }
        )

    def save_report(self, run_id: str, report: Report) -> Path:
        if not isinstance(report, Report):
            raise TypeError("Store writes require canonical Report")
        legacy = dict(report.summary)
        legacy.update(
            {
                "run_id": report.run_id,
                "assertions": [
                    {
                        "id": item.assertion_id,
                        "expected": item.expected,
                        "actual": item.actual,
                        "result": item.outcome,
                        "evidence_ids": list(item.evidence_ids),
                        "message": item.message,
                    }
                    for item in report.assertions
                ],
                "artifacts": [item.to_dict() for item in report.artifacts],
                "evidence_index": report.evidence_index,
                "steps": [
                    {
                        **item.to_dict(),
                        "kind": item.stage,
                        "result": item.result,
                        "detail": item.detail,
                    }
                    for item in report.steps
                ],
                "_canonical": report.to_dict(),
            }
        )
        path = self.reports_dir / f"{run_id}.json"
        with self._lock:
            path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
            self._conn.execute(
                "UPDATE runs SET report_path=?, updated_at=? WHERE run_id=?",
                (str(path), datetime.now().isoformat(timespec="seconds"), run_id),
            )
            self._conn.commit()
        return path

    def get_canonical_report(self, run_id: str) -> Report | None:
        payload = self.get_report(run_id)
        if not payload:
            return None
        canonical = payload.get("_canonical")
        if canonical:
            return Report.from_dict(canonical)

        def status(value: str) -> str:
            return {"pass": "ok", "fail": "error", "skipped": "skipped"}.get(value, "error")

        steps = [
            StepResult(
                stage=item.get("stage") or item.get("kind") or "",
                adapter="legacy",
                status=status(item.get("result") or "fail"),
                error=item.get("detail") if status(item.get("result") or "fail") == "error" else None,
            )
            for item in payload.get("steps") or []
        ]
        assertions = [
            AssertionResult(
                run_id=run_id,
                assertion_id=item.get("assertion_id") or item.get("id") or "",
                outcome=item.get("outcome") or item.get("result") or "fail",
                expected=item.get("expected"),
                actual=item.get("actual"),
                evidence_ids=list(item.get("evidence_ids") or []),
                message=item.get("message") or "",
            )
            for item in payload.get("assertions") or []
        ]
        artifacts = [
            Artifact(
                run_id=item.get("run_id") or run_id,
                type=item.get("type") or "",
                name=item.get("name") or "",
                sha256=item.get("sha256") or "",
                id=item.get("id") or "",
                path=item.get("path"),
                size=int(item.get("size") or 0),
            )
            for item in payload.get("artifacts") or []
        ]
        summary = {
            key: value
            for key, value in payload.items()
            if key not in {"run_id", "steps", "assertions", "artifacts", "evidence_index", "_canonical"}
        }
        return Report(
            run_id=run_id,
            summary=summary,
            steps=steps,
            assertions=assertions,
            evidence_index=dict(payload.get("evidence_index") or {}),
            artifacts=artifacts,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_canonical_run(run_id)
        if run is None:
            return None
        data = {
            "run_id": run.id,
            "scenario_id": run.case_id,
            "status": run.status.value,
            "case_id": run.case_id,
            "case_version": run.case_version,
            "case_fingerprint": run.case_fingerprint,
            "parameters_json": json.dumps(run.parameters, ensure_ascii=False),
            "resource_leases_json": json.dumps(
                [item.to_dict() for item in run.resource_leases], ensure_ascii=False
            ),
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error": run.error,
            "steps": [
                {
                    "seq": index,
                    "kind": item.stage,
                    "detail": item.detail,
                    "result": item.result,
                }
                for index, item in enumerate(run.steps, 1)
            ],
        }
        return data

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, scenario_id, status, firmware_ver, firmware_commit, "
                "created_at, updated_at, report_path FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ["run_id", "scenario_id", "status", "firmware_ver", "firmware_commit",
                "created_at", "updated_at", "report_path"]
        return [dict(zip(keys, row)) for row in rows]

    def get_report(self, run_id: str) -> dict[str, Any] | None:
        path = self.reports_dir / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
