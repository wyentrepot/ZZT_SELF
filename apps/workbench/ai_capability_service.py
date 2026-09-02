"""P2 read-only task facade over the existing AI control service.

The facade owns a small parent operation (a ``job``) and fans observations out
to the already-tested in-process control service.  It never calls the HTTP
API and never refreshes an underlying operation from a GET request.
"""
from __future__ import annotations

import json
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .ai_contracts import (
    AccessContext,
    CorrelationKeys,
    EvidenceItem,
    EvidenceLevel,
    EvidenceRef,
    EvidenceView,
    InvestigationRequest,
    FlashJobRequest,
    JobEnvelope,
    JobState,
    ModuleActionRequest,
    SourceHealth,
    SourceKind,
    Verdict,
    VerificationRunRequest,
)
from .ai_operations import AIControlService, InvalidObservation, SessionBusy, SourceUnavailable
from .ai_store import TERMINAL_STATES


_JOB_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})

# REQS-0022：证据分层上限与 L3 ref 约束
_L1_MAX_BYTES = 3 * 1024
_L2_MAX_BYTES = 16 * 1024
_L2_MAX_ITEMS = 50
_L3_MAX_REFS = 10
_LISTENER_REF_RE = re.compile(r"^listener:(?P<index_id>[^:]+):(?P<frame_id>\d+)$")


class EvidenceRefForbidden(ValueError):
    """L3 引用越权：ref 不属于该 job（映射为 HTTP 403）。"""


class AICapabilityService:
    """Create and read bounded, read-only investigation jobs."""

    def __init__(self, control: AIControlService):
        self.control = control
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai-v2")

    @staticmethod
    def _job_id(operation_id: str) -> str:
        return "job-" + str(operation_id).removeprefix("op-")

    @staticmethod
    def _operation_id(job_id: str) -> str:
        value = str(job_id or "")
        if not value.startswith("job-"):
            raise KeyError(job_id)
        return "op-" + value[4:]

    @staticmethod
    def _json_request(item) -> dict:
        if hasattr(item, "model_dump"):
            return item.model_dump(exclude_none=True)
        return dict(item or {})

    @staticmethod
    def _request_fingerprint(payload: dict) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _resource_for(control: AIControlService, request: dict) -> str:
        source = str(request.get("source") or "")
        target = request.get("target") or {}
        if source == SourceKind.MODULE_LOG.value:
            session_id = str(target.get("session_id") or "")
            if session_id:
                try:
                    return control.session_resource(session_id)
                except KeyError:
                    return session_id
            return str(target.get("mapping_id") or "")
        if source == SourceKind.LISTENER.value:
            return control.listener_resource(str(target.get("mapping_id") or ""))
        return "simcon"

    def start_investigation(self, request: InvestigationRequest | dict,
                            *, context: AccessContext) -> JobEnvelope:
        payload = request.model_dump(mode="json", exclude_none=True) if hasattr(request, "model_dump") else dict(request)
        observations = [dict(item, _ordinal=index) for index, item in enumerate(payload.get("observations") or [])]
        if not 1 <= len(observations) <= 3:
            raise InvalidObservation("investigation 至少需要 1 个、最多 3 个 observation")
        resources = [self._resource_for(self.control, item) for item in observations]
        operation = self.control.store.create(
            "investigation", context.actor,
            {"observations": observations, "context_id": payload.get("context_id"),
             "cleanup": "owned_only", "resources": resources},
            client_request_id=str(payload.get("client_request_id") or ""),
        )
        job_id = self._job_id(operation["operation_id"])
        if operation["state"] == "created":
            self.control.store.audit(actor=context.actor, action="ai_v2.investigation.create",
                                     resource=",".join(resources), result="queued",
                                     operation_id=operation["operation_id"])
            # Closed historical windows are cheap and deterministic: finish
            # them before returning so create -> read -> evidence is a
            # bounded three-call workflow. Live windows remain asynchronous.
            if all(str((item.get("window") or {}).get("mode") or "live") != "live"
                   for item in observations):
                self._run_job(operation["operation_id"], observations, context.actor)
            else:
                self._executor.submit(self._run_job, operation["operation_id"], observations, context.actor)
        return self._envelope(operation)

    def _run_job(self, operation_id: str, observations: list[dict], actor: str) -> None:
        try:
            self.control.store.set_state(operation_id, "running")
            results: list[dict] = []
            with ThreadPoolExecutor(max_workers=max(1, len(observations)), thread_name_prefix="ai-v2-src") as pool:
                futures = [pool.submit(self._run_observation, item, actor) for item in observations]
                for future in as_completed(futures):
                    results.append(future.result())
            results.sort(key=lambda item: int(item["ordinal"]))
            self.control.store.set_state(
                operation_id, "succeeded",
                result={"observations": results, "source_health": self._health(results)},
            )
            self.control.store.audit(actor=actor, action="ai_v2.investigation.complete",
                                     result="succeeded", operation_id=operation_id)
        except Exception as exc:  # isolated parent failure; source details are retained when available
            self.control.store.set_state(operation_id, "error", error=str(exc),
                                         result={"observations": [], "source_health": {}})
            self.control.store.audit(actor=actor, action="ai_v2.investigation.complete",
                                     result="error", operation_id=operation_id)

    def _run_observation(self, request: dict, actor: str) -> dict:
        source = str(request.get("source") or "")
        ordinal = int(request.get("_ordinal", 0))
        clean = {key: value for key, value in request.items() if key != "_ordinal"}
        started = time.monotonic()
        if source == SourceKind.SIMCON.value:
            try:
                result = self.control.simcon_frames(filters=clean.get("match") or {})
                return {"ordinal": ordinal, "source": source, "state": "succeeded",
                        "operation_id": None, "result": result,
                        "health": {"available": True}}
            except Exception as exc:
                return {"ordinal": ordinal, "source": source, "state": "error",
                        "operation_id": None, "error": str(exc),
                        "health": {"available": False, "reason": str(exc)[:240]}}
        try:
            operation = self.control.create_observation(clean, actor=actor)
            operation_id = operation["operation_id"]
            # Historical observations are already terminal. Live observations
            # are advanced by this worker, never by GET /jobs.
            while operation["state"] not in TERMINAL_STATES:
                operation = self.control.wait_operation(operation_id, timeout_seconds=30)
                if operation["state"] not in TERMINAL_STATES and time.monotonic() - started > 3600:
                    break
            return {"ordinal": ordinal, "source": source, "state": operation["state"],
                    "operation_id": operation_id, "result": operation.get("result"),
                    "error": operation.get("error"), "health": {"available": True}}
        except (InvalidObservation, SourceUnavailable, KeyError) as exc:
            return {"ordinal": ordinal, "source": source, "state": "error",
                    "operation_id": None, "error": str(exc),
                    "health": {"available": False, "reason": str(exc)[:240]}}
        except Exception as exc:  # one source must not erase other source results
            return {"ordinal": ordinal, "source": source, "state": "error",
                    "operation_id": None, "error": str(exc),
                    "health": {"available": False, "reason": str(exc)[:240]}}

    @staticmethod
    def _health(results: list[dict]) -> dict[str, dict]:
        health = {source.value: {"available": False, "reason": "not_requested"}
                  for source in SourceKind}
        for item in results:
            health[item["source"]] = item.get("health") or {"available": True}
        return health

    @staticmethod
    def _compact_operation(operation: dict) -> dict:
        """Expose only bounded, non-path operation data in a v2 write job."""
        result = operation.get("result")
        compact = {
            "underlying_operation_id": operation.get("operation_id"),
            "underlying_state": operation.get("state"),
        }
        if result is not None:
            compact["result"] = _strip_paths(result)
        if operation.get("error"):
            compact["error"] = str(operation["error"])[:3072]
        return compact

    def _new_write_job(self, kind: str, payload: dict, *, context: AccessContext,
                       client_request_id: str) -> dict:
        return self.control.store.create(
            kind, context.actor, payload, client_request_id=client_request_id,
        )

    def _finish_write_job(self, parent: dict, underlying: dict, *, result: dict | None = None) -> JobEnvelope:
        compact = result or self._compact_operation(underlying)
        compact.setdefault("underlying_operation_id", underlying.get("operation_id"))
        compact.setdefault("underlying_state", underlying.get("state"))
        state = str(underlying.get("state") or "error")
        parent_state = "succeeded" if state in {"succeeded", "matched", "timed_out"} else (
            "cancelled" if state == "cancelled" else "error" if state in {"error", "interrupted"} else "waiting"
        )
        stored = self.control.store.set_state(parent["operation_id"], parent_state, result=compact)
        return self._envelope(stored)

    def _monitor_write_job(self, parent_id: str, underlying_id: str) -> None:
        try:
            while True:
                underlying = self.control.get_operation(underlying_id)
                if underlying.get("state") in TERMINAL_STATES:
                    parent = self.control.store.get(parent_id)
                    self._finish_write_job(parent, underlying)
                    return
                time.sleep(0.05)
        except Exception as exc:
            try:
                self.control.store.set_state(parent_id, "error", error=str(exc))
            except KeyError:
                pass

    def start_module_action(self, request: ModuleActionRequest | dict, *, context: AccessContext) -> JobEnvelope:
        payload = self._json_request(request)
        action = str(payload.get("action") or "")
        if action not in {"ensure", "send", "stop"}:
            raise InvalidObservation("module action 仅支持 ensure、send、stop")
        if action == "send":
            has_text = payload.get("text") is not None
            has_hex = payload.get("data_hex") is not None
            if has_text == has_hex:
                raise InvalidObservation("send 必须且只能提供 text 或 data_hex")
        elif payload.get("text") is not None or payload.get("data_hex") is not None:
            raise InvalidObservation("ensure、stop 不接受发送数据")
        client_request_id = str(payload.pop("client_request_id", "") or "")
        cleanup = payload.pop("cleanup", "owned_only")
        resource = str(payload.get("mapping_id") or payload.get("session_id") or "")
        if payload.get("session_id"):
            try:
                resource = self.control.session_resource(str(payload["session_id"]))
            except KeyError:
                resource = str(payload["session_id"])
        parent = self._new_write_job(
            "module_action",
            {"action": action, "target": {"session_id": payload.get("session_id"), "mapping_id": resource},
             "resources": [resource], "cleanup": cleanup, "request": _strip_paths(payload)},
            context=context, client_request_id=client_request_id,
        )
        if parent["state"] != "created":
            return self._envelope(parent)
        try:
            if action == "ensure":
                ensured = self.control.ensure_module_session(payload, actor=context.actor)
                session = ensured.get("session") or {}
                result = {
                    "action": action,
                    "owned": not bool(ensured.get("reused")),
                    "session_id": session.get("session_id"),
                    "mapping_id": (session.get("port_identity") or {}).get("mapping_id") or resource,
                    "state": session.get("state"),
                }
                return self._finish_write_job(parent, {"operation_id": None, "state": "succeeded"}, result=result)
            if action == "send":
                session_id = str(payload.get("session_id") or "")
                if not session_id:
                    raise InvalidObservation("send 必须提供 session_id")
                underlying = self.control.send_module(session_id, payload, actor=context.actor)
            else:
                session_id = str(payload.get("session_id") or "")
                if not session_id:
                    raise InvalidObservation("stop 必须提供 session_id")
                underlying = self.control.stop_module_session(
                    session_id, actor=context.actor, force=bool(payload.get("force", False)),
                )
            return self._finish_write_job(parent, underlying)
        except Exception as exc:
            self.control.store.set_state(parent["operation_id"], "error", error=str(exc))
            raise

    def start_verification_run(self, request: VerificationRunRequest | dict, *, context: AccessContext) -> JobEnvelope:
        payload = self._json_request(request)
        client_request_id = str(payload.get("client_request_id") or "")
        task = dict(payload.get("task") or {})
        parent = self._new_write_job(
            "verification_run", {"target": {"mapping_id": "simcon"}, "resources": ["simcon"],
                                  "request": task},
            context=context, client_request_id=client_request_id,
        )
        if parent["state"] != "created":
            return self._envelope(parent)
        try:
            underlying = self.control.simcon_verify(task, actor=context.actor)
            started = self.control.store.set_state(
                parent["operation_id"], "waiting",
                result={"underlying_operation_id": underlying["operation_id"],
                        "underlying_state": underlying.get("state")},
            )
            self._executor.submit(self._monitor_write_job, parent["operation_id"], underlying["operation_id"])
            return self._envelope(started)
        except Exception as exc:
            self.control.store.set_state(parent["operation_id"], "error", error=str(exc))
            raise

    def start_flash_job(self, request: FlashJobRequest | dict, *, context: AccessContext) -> JobEnvelope:
        payload = self._json_request(request)
        client_request_id = str(payload.pop("client_request_id", "") or "")
        session_id = str(payload.get("session_id") or "")
        try:
            resource = self.control.session_resource(session_id)
        except KeyError:
            resource = session_id
        parent = self._new_write_job(
            "flash_job", {"target": {"session_id": session_id, "mapping_id": resource},
                           "resources": [resource], "cleanup": "owned_only",
                           "request_fingerprint": self._request_fingerprint(payload)},
            context=context, client_request_id=client_request_id,
        )
        if parent["state"] != "created":
            return self._envelope(parent)
        try:
            underlying = self.control.flash_module(payload, actor=context.actor)
            if underlying.get("state") in TERMINAL_STATES:
                return self._finish_write_job(parent, underlying)
            started = self.control.store.set_state(
                parent["operation_id"], "waiting", result=self._compact_operation(underlying),
            )
            self._executor.submit(self._monitor_write_job, parent["operation_id"], underlying["operation_id"])
            return self._envelope(started)
        except Exception as exc:
            self.control.store.set_state(parent["operation_id"], "error", error=str(exc))
            raise

    def cancel_job(self, job_id: str, *, actor: str) -> JobEnvelope:
        parent = self.control.store.get(self._operation_id(job_id))
        payload = parent.get("payload") or {}
        underlying_id = str((parent.get("result") or {}).get("underlying_operation_id") or "")
        if parent.get("kind") in {"flash_job", "verification_run"}:
            raise SessionBusy("烧录或模拟集中器验证任务不可取消")
        if underlying_id:
            underlying = self.control.store.get(underlying_id)
            if underlying.get("kind") in {"module_flash", "simcon_verify"}:
                raise SessionBusy("该任务不可取消")
            self.control.cancel_operation(underlying_id, actor=actor)
        elif parent.get("state") not in TERMINAL_STATES:
            self.control.store.set_state(parent["operation_id"], "cancelled", result={"cancelled_at": "now"})
        else:
            return self._envelope(parent)
        return self._envelope(self.control.store.get(parent["operation_id"]))

    def read_job(self, job_id: str, *, wait_seconds: int = 0) -> JobEnvelope:
        # Deliberately ignore wait_seconds: this endpoint is a pure read. The
        # worker above owns all state transitions.
        operation = self.control.store.get(self._operation_id(job_id))
        return self._envelope(operation)

    def _envelope(self, operation: dict) -> JobEnvelope:
        state = str(operation.get("state") or "created")
        state_map = {"created": JobState.QUEUED, "waiting": JobState.RUNNING,
                     "running": JobState.RUNNING, "matched": JobState.SUCCEEDED,
                     "succeeded": JobState.SUCCEEDED, "timed_out": JobState.SUCCEEDED,
                     "error": JobState.FAILED, "interrupted": JobState.FAILED,
                     "cancelled": JobState.CANCELLED, "source_stopped": JobState.FAILED}
        result = operation.get("result") or {}
        observations = result.get("observations") or []
        refs = self._refs(observations)
        verdict = None if operation.get("kind") in {"module_action", "verification_run", "flash_job"} else self._verdict(state, observations)
        source_health = result.get("source_health") or self._health(observations)
        summary = self._summary(operation, observations)
        underlying = [str(item["operation_id"]) for item in observations if item.get("operation_id")]
        if not underlying and result.get("underlying_operation_id"):
            underlying = [str(result["underlying_operation_id"])]
        public_result = None if operation.get("kind") == "investigation" else _strip_paths(result)
        return JobEnvelope(job_id=self._job_id(operation["operation_id"]),
                           job_state=state_map.get(state, JobState.RUNNING), verdict=verdict,
                           source_health=source_health, summary=summary,
                           evidence_refs=refs, underlying_refs=underlying,
                           result=public_result)

    @staticmethod
    def _verdict(state: str, results: list[dict]) -> Verdict | None:
        if state not in _JOB_TERMINAL and state not in {"error", "timed_out"}:
            return None
        if state in {"error", "interrupted"}:
            return Verdict.ERROR
        if any(item.get("error") or item.get("state") == "error" for item in results):
            return Verdict.ERROR
        if any((item.get("result") or {}).get("reason") == "live_window_unverified" for item in results):
            return Verdict.INCONCLUSIVE
        if any((item.get("result") or {}).get("condition_met") is False for item in results):
            return Verdict.FAIL
        return Verdict.PASS if results else None

    @staticmethod
    def _summary(operation: dict, results: list[dict]) -> str:
        if operation.get("error"):
            return str(operation["error"])[:3072]
        bits = [f"{item.get('source')}: {item.get('state')}" for item in results]
        return ("investigation " + (", ".join(bits) if bits else "queued"))[:3072]

    @staticmethod
    def _refs(results: list[dict]) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        for item in results:
            source = item.get("source")
            result = item.get("result") or {}
            correlation = CorrelationKeys(operation_id=item.get("operation_id"))
            if source == SourceKind.MODULE_LOG.value:
                artifact_id = (result.get("log") or {}).get("artifact_id")
                refs.append(EvidenceRef(source=source, evidence_id=artifact_id,
                                        correlation=correlation))
            elif source == SourceKind.LISTENER.value:
                index_id = (result.get("index") or {}).get("index_id")
                matches = result.get("matches") or []
                if matches:
                    for match in matches[:50]:
                        key = match.get("frame_key") or {}
                        refs.append(EvidenceRef(source=source, index_id=key.get("index_id") or index_id,
                                                frame_id=key.get("frame_id"), correlation=correlation))
                else:
                    refs.append(EvidenceRef(source=source, index_id=index_id, correlation=correlation))
            elif source == SourceKind.SIMCON.value:
                refs.append(EvidenceRef(source=source, raw_ref="simcon:frames",
                                        correlation=correlation))
        return refs

    def read_job_evidence(self, job_id: str, *, level: EvidenceLevel,
                          refs: list[str] | tuple[str, ...] = ()) -> EvidenceView:
        operation = self.control.store.get(self._operation_id(job_id))
        envelope = self._envelope(operation)
        results = (operation.get("result") or {}).get("observations") or []
        if level == EvidenceLevel.L3:
            items = self._l3_items(envelope, list(refs or []))
            return EvidenceView(job_id=job_id, level=level, summary=envelope.summary or "",
                                items=items, refs=envelope.evidence_refs)
        items = [self._evidence_item(item, level) for item in results]
        if level == EvidenceLevel.L1:
            items = self._bound_items_to(items, _L1_MAX_BYTES)
        elif level == EvidenceLevel.L2:
            items = self._bound_items_to(items, _L2_MAX_BYTES)
        return EvidenceView(job_id=job_id, level=level, summary=envelope.summary or "",
                            items=items, refs=envelope.evidence_refs)

    def _l3_items(self, envelope: JobEnvelope, refs: list[str]) -> list[EvidenceItem]:
        """L3：仅对本 job 已返回的 listener ref 回传完整帧 JSON（越权 403、格式错 422）。"""
        if not refs:
            return []
        if len(refs) > _L3_MAX_REFS:
            raise InvalidObservation(f"L3 每次最多 {_L3_MAX_REFS} 个 ref")
        allowed = {
            f"listener:{ref.index_id}:{ref.frame_id}"
            for ref in envelope.evidence_refs
            if ref.source == SourceKind.LISTENER and ref.index_id and ref.frame_id is not None
        }
        items: list[EvidenceItem] = []
        for ref_text in refs:
            matched = _LISTENER_REF_RE.fullmatch(str(ref_text or "").strip())
            if matched is None:
                raise InvalidObservation(f"L3 ref 格式错误：{ref_text}")
            if str(ref_text or "").strip() not in allowed:
                raise EvidenceRefForbidden(f"L3 ref 不属于该 job：{ref_text}")
            index_id = matched.group("index_id")
            frame_id = int(matched.group("frame_id"))
            frame = self.control.listener_frame_detail(index_id, frame_id)
            items.append(EvidenceItem(
                source=SourceKind.LISTENER,
                evidence_id=str(frame_id),
                data=_strip_paths({
                    "ref": f"listener:{index_id}:{frame_id}",
                    "index_id": index_id,
                    "frame_id": frame_id,
                    "raw_hex": frame.get("raw_hex"),
                    "summary": frame.get("summary"),
                    "parse_error": frame.get("parse_error"),
                    "analysis": frame.get("analysis"),
                    "trace_link": "/api/ai/v1/listener/indexes/" + index_id + "/frames/" + str(frame_id),
                }),
            ))
        return items

    @staticmethod
    def _evidence_item(item: dict, level: EvidenceLevel) -> EvidenceItem:
        result = item.get("result") or {}
        source = item.get("source")
        if source == SourceKind.MODULE_LOG.value:
            data = {"condition_met": result.get("condition_met"),
                    "reason": result.get("reason"),
                    "snippet": (result.get("snippet") or [])[:50]}
            evidence_id = (result.get("log") or {}).get("artifact_id")
        elif source == SourceKind.LISTENER.value:
            data = _listener_evidence_projection(result, level)
            evidence_id = result.get("artifact_id")
        else:
            data = result if level == EvidenceLevel.L2 else {"available": True}
            evidence_id = None
        return EvidenceItem(source=source, evidence_id=evidence_id, data=_strip_paths(data))

    @staticmethod
    def _bound_items_to(items: list[EvidenceItem], max_bytes: int) -> list[EvidenceItem]:
        """把证据投影压到字节上限内：优先裁剪末尾的可变列表字段。"""
        def _size() -> int:
            return len(json.dumps(
                [item.model_dump() for item in items], ensure_ascii=False,
            ).encode("utf-8"))

        while _size() > max_bytes:
            if not items:
                break
            last = items[-1]
            trimmed = False
            for field in ("snippet", "matches", "frames", "refs"):
                value = last.data.get(field)
                if isinstance(value, list) and value:
                    last.data[field] = value[:-1]
                    trimmed = True
                    break
            if not trimmed:
                items.pop()
        return items[:_L2_MAX_ITEMS]

    @staticmethod
    def _bound_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
        return AICapabilityService._bound_items_to(items, _L2_MAX_BYTES)


def _listener_kind(result: dict) -> str:
    """从 observation 结果判别 listener 观察类型（result 结构随 observation_kind 变化）。"""
    if "periods" in result:
        return "minute_periods"
    if "trace" in result:
        return "trace_query"
    return "frame_query"


def _correlation_status(summary: dict) -> str:
    if any((summary.get(key) or 0) for key in ("no_ack", "no_response", "no_confirm")):
        return "partial"
    if (summary.get("bad_frames") or 0):
        return "degraded"
    return "complete"


def _listener_evidence_projection(result: dict, level: EvidenceLevel) -> dict:
    """REQS-0022：listener observation 结果 → L1 范围摘要 / L2 解析投影（不返回 raw_hex）。"""
    index_id = (result.get("index") or {}).get("index_id")
    kind = _listener_kind(result)
    if level == EvidenceLevel.L1:
        if kind == "minute_periods":
            periods = result.get("periods") or []
            refs = []
            for period in periods:
                for report in period.get("reports") or []:
                    key = report.get("frame_key") or {}
                    refs.append({"index_id": key.get("index_id") or index_id,
                                 "frame_id": key.get("frame_id")})
            return {
                "index_id": index_id,
                "period_count": len(periods),
                "report_count": sum(len(period.get("reports") or []) for period in periods),
                "parse_backend": "parsed",
                "refs": refs[:50],
            }
        if kind == "trace_query":
            summary = (result.get("trace") or {}).get("summary") or {}
            matches = result.get("matches") or []
            frame_type_counts: dict[Any, int] = {}
            direction_counts: dict[Any, int] = {}
            for match in matches:
                ft = match.get("frm_type")
                frame_type_counts[ft] = frame_type_counts.get(ft, 0) + 1
                direction = match.get("direction")
                direction_counts[direction] = direction_counts.get(direction, 0) + 1
            return {
                "index_id": index_id,
                "scope": (result.get("index") or {}).get("scope"),
                "total_frames": result.get("total_frames") or len(matches),
                "flow_groups": int(summary.get("flows") or 0),
                "frame_type_counts": frame_type_counts,
                "direction_counts": direction_counts,
                "correlation_status": _correlation_status(summary),
                "parse_backend": "parsed",
                "refs": [match.get("frame_key") for match in matches][:50],
            }
        # frame_query / parsed_frame
        matches = result.get("matches") or []
        frame_type_counts: dict[Any, int] = {}
        for match in matches:
            summary = match.get("summary") or {}
            ft = summary.get("FrmType") or summary.get("帧类型")
            frame_type_counts[ft] = frame_type_counts.get(ft, 0) + 1
        return {
            "index_id": index_id,
            "total_frames": len(matches),
            "flow_groups": 0,
            "frame_type_counts": frame_type_counts,
            "direction_counts": {},
            "correlation_status": "unavailable",
            "parse_backend": "parsed" if matches else "none",
            "refs": [match.get("frame_key") for match in matches][:50],
        }

    # L2 解析投影
    if kind == "minute_periods":
        frames = []
        for period in result.get("periods") or []:
            for report in period.get("reports") or []:
                key = report.get("frame_key") or {}
                frame_id = key.get("frame_id")
                ref_index_id = key.get("index_id") or index_id
                frames.append({
                    "index_id": ref_index_id,
                    "frame_id": frame_id,
                    "ref": f"listener:{ref_index_id}:{frame_id}",
                    "log_time": report.get("log_time"),
                    "freeze_time": report.get("freeze_time"),
                    "response_result": report.get("response_result"),
                    "source_mac": report.get("source_mac"),
                    "source_tei": report.get("source_tei"),
                    "report_count": report.get("report_count"),
                    "data_length": report.get("data_length"),
                    "application_error": report.get("application_error"),
                })
        return {"index_id": index_id, "frames": frames[:50]}
    if kind == "trace_query":
        frames = []
        for match in result.get("matches") or []:
            key = match.get("frame_key") or {}
            frame_id = key.get("frame_id")
            ref_index_id = key.get("index_id") or index_id
            frames.append({
                "index_id": ref_index_id,
                "frame_id": frame_id,
                "ref": f"listener:{ref_index_id}:{frame_id}",
                "log_time": match.get("log_time"),
                "direction": match.get("direction"),
                "role": match.get("role"),
                "frm_type": match.get("frm_type"),
                "nid": match.get("nid"),
                "src": match.get("src"),
                "dst": match.get("dst"),
                "ori_s": match.get("ori_s"),
                "ch_type": match.get("ch_type"),
                "app_id": match.get("app_id"),
                "msg_seq": match.get("msg_seq"),
                "flow_dir": match.get("flow_dir"),
                "meter_addrs": match.get("meter_addrs"),
                "detail_url": match.get("detail_url"),
            })
        return {"index_id": index_id, "frames": frames[:50]}
    frames = []
    for match in result.get("matches") or []:
        key = match.get("frame_key") or {}
        frame_id = key.get("frame_id")
        ref_index_id = key.get("index_id") or index_id
        summary = match.get("summary") or {}
        frames.append({
            "index_id": ref_index_id,
            "frame_id": frame_id,
            "ref": f"listener:{ref_index_id}:{frame_id}",
            "log_time": match.get("log_time"),
            "frm_type": summary.get("FrmType"),
            "detail_url": match.get("detail_url"),
        })
    return {"index_id": index_id, "frames": frames[:50]}


def _strip_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_paths(nested) for key, nested in value.items()
                if str(key).lower() not in {"path", "source_log_path", "log_path"}}
    if isinstance(value, list):
        return [_strip_paths(item) for item in value[:50]]
    return value
