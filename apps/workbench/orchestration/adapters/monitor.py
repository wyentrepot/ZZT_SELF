from __future__ import annotations

from pathlib import Path
from typing import Any

from test_automation.ports import MonitorPort, MonitorRequest, MonitorResult, PortError


class LoghooksMonitorAdapter:
    """唯一负责把 loghooks 离线扫描能力接入 MonitorPort。"""

    def scan(self, request: MonitorRequest) -> MonitorResult:
        try:
            from loghooks.engine import Engine, Event
            from loghooks.output import build_drift_list, build_summary
            from loghooks.rules import RuleLoader
            from loghooks.sources import iter_lines

            loader = RuleLoader()
            try:
                loader.load_all()
            except Exception:
                pass
            rule_objs: list[Any] = loader.rules
            if request.rules:
                wanted_scope = {r for r in request.rules if "/" not in r}
                wanted_prov = {r.split("/")[-1] for r in request.rules if "/" in r}
                rule_objs = [r for r in rule_objs if r.scope in wanted_scope or r.province in wanted_prov]

            parsed_all: list[Any] = []
            files: list[str] = []
            for path in sorted(request.log_dir.glob("*")):
                if not (path.is_file() and path.suffix.lower() in (".txt", ".log", ".jsonl", ".dat")):
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = path.read_text(encoding="gbk", errors="ignore")
                for line in content.splitlines():
                    parsed_all.extend(iter_lines("module_log", [line]))
                files.append(str(path))

            evidence: list[Any] = []

            def on_event(event: Event) -> None:
                evidence.append(event.to_evidence(run_id=request.run_id))

            result = Engine(rule_objs, source="module_log", on_event=on_event)
            for line in parsed_all:
                result.feed(line)
            finalized = result.finalize()
            events = [
                {"type": e.type, "label": e.label, "message": e.message, "time": e.time,
                 "rule_id": e.rule_id, "category": e.category, "source": e.source}
                for e in finalized.events
            ]
            return MonitorResult(
                files=files, events=events, evidence=evidence,
                summary=build_summary(finalized), drift=bool(finalized.drifts),
                drift_list=build_drift_list(finalized), total_lines=finalized.total_lines,
                unmatched=finalized.unmatched,
            )
        except Exception as exc:
            raise PortError("monitor_failed", "monitor port failed", {"type": type(exc).__name__}) from exc