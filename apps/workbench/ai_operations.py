"""Safe orchestration adapters for the AI workbench control plane."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .ai_store import OperationStore, TERMINAL_STATES


class SourceUnavailable(RuntimeError):
    pass


class SessionBusy(RuntimeError):
    pass


class InvalidObservation(ValueError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIControlService:
    """Uses in-process backend services; it never opens a second serial handle."""

    def __init__(self, *, module_service=None, listener_service=None, log_service=None,
                 store=None, resource_registry=None):
        self.module_service = module_service
        self.listener_service = listener_service
        self.log_service = log_service
        self.resource_registry = resource_registry
        self.store = store or OperationStore()

    def status(self, *, include_paths: bool = False) -> dict:
        sessions = []
        if self.module_service is not None:
            try:
                sessions = self.module_service.list_sessions()
            except Exception:
                sessions = []
        module_sessions = [self._session_summary(item, include_paths=include_paths) for item in sessions]

        listener = {"state": "unavailable", "degraded": "listener_service_unavailable"}
        try:
            raw = self.listener_service.status() if self.listener_service is not None else {}
            index_status = self.log_service.status() if self.log_service is not None else {}
            versioned = all(
                callable(getattr(self.log_service, name, None))
                for name in ("list_indexes", "list_index_frames", "get_index_frame")
            )
            listener = {
                "state": raw.get("state", index_status.get("state", "unknown")),
                "backend_session_id": "listener-main",
                "port": raw.get("port", ""),
                "port_identity": raw.get("port_identity") or {},
                "frame_count": raw.get("frame_count", index_status.get("frame_count", 0)),
                "index_id": index_status.get("index_id") or raw.get("index_id"),
                "index_capability": "versioned" if versioned else "degraded",
            }
            if not versioned:
                listener["degraded"] = "versioned_listener_index_not_available"
            if include_paths:
                listener["log_path"] = raw.get("log_file")
                listener["source_log_path"] = index_status.get("source_path")
                listener["index_path"] = str(getattr(self.log_service, "database_path", "") or "")
        except Exception as exc:
            listener = {"state": "error", "degraded": str(exc)}

        serial_handles = []
        snapshot = getattr(self.resource_registry, "snapshot", None)
        if callable(snapshot):
            try:
                serial_handles = snapshot()
            except Exception:
                serial_handles = []

        return {
            "server_time": _iso_now(),
            "workbench": {"state": "ready", "version": "ai-v1-foundation"},
            "listener": listener,
            "module_sessions": module_sessions,
            "operations": [
                {key: item[key] for key in ("operation_id", "kind", "actor", "state", "version")}
                for item in self.store.list_active()
            ],
            "serial_handles": serial_handles,
        }

    @staticmethod
    def _session_summary(session: dict, *, include_paths: bool) -> dict:
        result = {
            "session_id": session.get("session_id"),
            "title": session.get("title"),
            "module": session.get("module"),
            "state": session.get("state"),
            "port": session.get("port"),
            "port_identity": session.get("port_identity") or {},
            "flash": session.get("flash") or {},
        }
        if include_paths:
            result["log_path"] = session.get("log_file")
        return result

    def session_resource(self, session_id: str) -> str:
        if self.module_service is None:
            return session_id
        session = self.module_service.get_session(session_id)
        identity = session.get("port_identity") or {}
        return str(identity.get("mapping_id") or session_id)

    def ensure_module_session(self, request: dict, *, actor: str = "ai") -> dict:
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        mapping_id = str(request.get("mapping_id") or "").strip()
        session_id = str(request.get("session_id") or "").strip()
        module = str(request.get("module") or "cco").strip().lower()
        sessions = self.module_service.list_sessions()
        if session_id:
            for session in sessions:
                if session.get("session_id") == session_id:
                    self.store.audit(
                        actor=actor, action="module_session.ensure",
                        resource=str((session.get("port_identity") or {}).get("mapping_id") or session_id),
                        result="reused",
                    )
                    return {"reused": True, "session": session}
            raise KeyError(session_id)
        if mapping_id:
            for session in sessions:
                identity = session.get("port_identity") or {}
                if identity.get("mapping_id") == mapping_id:
                    self.store.audit(actor=actor, action="module_session.ensure", resource=mapping_id, result="reused")
                    return {"reused": True, "session": session}

        created = self.module_service.create_session(
            title=str(request.get("title") or ""), module=module,
        )
        port = str(request.get("port") or self._port_for_mapping(mapping_id) or "").strip()
        if not port:
            raise SourceUnavailable("无法解析串口映射；请提供已授权的映射 ID 或实际端口")
        serial = request.get("serial") or {}
        started = self.module_service.start_session(
            created["session_id"], port,
            baudrate=int(serial.get("baudrate", 115200)),
            bytesize=int(serial.get("bytesize", 8)),
            parity=str(serial.get("parity", "N")),
            stopbits=int(serial.get("stopbits", 1)),
        )
        self.store.audit(actor=actor, action="module_session.ensure", resource=mapping_id or port, result="created")
        return {"reused": False, "session": started}

    def _port_for_mapping(self, mapping_id: str) -> str:
        if not mapping_id or self.module_service is None:
            return ""
        getter = getattr(self.module_service, "list_available_port_details", None)
        if not callable(getter):
            return ""
        for detail in getter():
            if detail.get("mapping_id") == mapping_id and detail.get("online"):
                return str(detail.get("device") or "")
        return ""

    def stop_module_session(self, session_id: str, *, actor: str, force: bool = False) -> dict:
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        active = [
            item for item in self.store.list_active()
            if item.get("payload", {}).get("target", {}).get("session_id") == session_id
        ]
        if active and not force:
            raise SessionBusy("会话仍有活动观察任务，不能停止")
        resource = self.session_resource(session_id)
        result = self.module_service.stop_session(session_id)
        self.store.audit(actor=actor, action="module_session.stop", resource=resource, result="stopped")
        return result

    def send_module(self, session_id: str, request: dict, *, actor: str, client_request_id: str = "") -> dict:
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        resource = self.session_resource(session_id)
        action = self.store.create(
            "module_send", actor, {"target": {"session_id": session_id, "mapping_id": resource}},
            client_request_id=client_request_id,
        )
        if action["state"] != "created":
            return action
        try:
            if "text" in request:
                result = self.module_service.write_text_session(
                    session_id, str(request["text"]), bool(request.get("append_newline", True)),
                )
            elif "data_hex" in request:
                result = self.module_service.write_session(session_id, str(request["data_hex"]))
            else:
                raise ValueError("必须提供 text 或 data_hex")
            completed = self.store.set_state(action["operation_id"], "succeeded", result=result)
            self.store.audit(actor=actor, action="module_session.send", resource=resource,
                             result="succeeded", operation_id=action["operation_id"])
            return completed
        except Exception as exc:
            failed = self.store.set_state(action["operation_id"], "error", error=str(exc))
            self.store.audit(actor=actor, action="module_session.send", resource=resource,
                             result="error", operation_id=action["operation_id"])
            return failed

    def flash_module(self, request: dict, *, actor: str = "ai") -> dict:
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        session_id = str(request.get("session_id") or "").strip()
        bin_path = str(request.get("bin_path") or "").strip()
        if not session_id or not bin_path:
            raise ValueError("烧录必须提供 session_id 和 bin_path")
        resource = self.session_resource(session_id)
        operation = self.store.create(
            "module_flash", actor,
            {
                "target": {"session_id": session_id, "mapping_id": resource},
                "bin_path": bin_path,
                "slot": int(request.get("slot", 0)),
                "baud_plan": request.get("baud_plan"),
                "no_reboot_after": bool(request.get("no_reboot_after", False)),
            },
            client_request_id=str(request.get("client_request_id") or ""),
        )
        if operation["state"] != "created":
            return operation
        try:
            payload = operation["payload"]
            initial = self.module_service.flash_session(
                session_id, payload["bin_path"], payload["slot"], payload["baud_plan"],
                payload["no_reboot_after"],
            )
            flash = initial.get("flash") or self.module_service.get_session(session_id).get("flash") or {}
            self.store.audit(
                actor=actor, action="module_session.flash", resource=resource,
                result="started", operation_id=operation["operation_id"],
            )
            if flash.get("flashing"):
                return self.store.set_state(operation["operation_id"], "waiting", result={"initial": initial})
            return self.store.set_state(
                operation["operation_id"], "succeeded",
                result={"session_id": session_id, "flash": flash},
            )
        except Exception as exc:
            failed = self.store.set_state(operation["operation_id"], "error", error=str(exc))
            self.store.audit(
                actor=actor, action="module_session.flash", resource=resource,
                result="error", operation_id=operation["operation_id"],
            )
            return failed

    def _refresh_flash_operation(self, operation_id: str) -> dict:
        operation = self.store.get(operation_id)
        if operation["state"] != "waiting":
            return operation
        try:
            session_id = operation["payload"]["target"]["session_id"]
            session = self.module_service.get_session(session_id)
            flash = session.get("flash") or {}
            if flash.get("flashing"):
                return operation
            if flash.get("phase") == "error" or flash.get("error"):
                return self.store.set_state(
                    operation_id, "error", error=str(flash.get("error") or flash.get("message") or "烧录失败"),
                )
            result = {
                "session_id": session_id,
                "flash": flash,
                "log_path": session.get("log_file"),
            }
            completed = self.store.set_state(operation_id, "succeeded", result=result)
            self.store.audit(
                actor=operation["actor"], action="module_session.flash",
                resource=operation["payload"]["target"]["mapping_id"],
                result="succeeded", operation_id=operation_id,
            )
            return completed
        except Exception as exc:
            return self.store.set_state(operation_id, "error", error=str(exc))

    def listener_resource(self, requested: str = "") -> str:
        if requested:
            return str(requested)
        if self.listener_service is not None:
            try:
                identity = self.listener_service.status().get("port_identity") or {}
                return str(identity.get("mapping_id") or "listener-main")
            except Exception:
                pass
        return "listener-main"

    def _listener_log_or_error(self):
        if self.log_service is None:
            raise SourceUnavailable("侦听台索引服务不可用")
        required = ("status", "list_indexes", "list_index_frames", "get_index_frame")
        if not all(callable(getattr(self.log_service, name, None)) for name in required):
            raise SourceUnavailable("侦听台版本化索引服务不可用")
        return self.log_service

    def _listener_port_for_mapping(self, mapping_id: str) -> str:
        if not mapping_id or self.listener_service is None:
            return ""
        getter = getattr(self.listener_service, "list_available_ports", None)
        if not callable(getter):
            return ""
        for detail in getter():
            if detail.get("mapping_id") == mapping_id and detail.get("online"):
                return str(detail.get("device") or "")
        return ""

    def ensure_listener(self, request: dict, *, actor: str) -> dict:
        if self.listener_service is None:
            raise SourceUnavailable("侦听台服务不可用")
        mapping_id = str(request.get("mapping_id") or "").strip()
        state = self.listener_service.status()
        resource = self.listener_resource(mapping_id)
        if state.get("state") in ("running", "starting"):
            self.store.audit(actor=actor, action="listener.ensure", resource=resource, result="reused")
            return {"reused": True, "listener": state}
        port = str(request.get("port") or self._listener_port_for_mapping(mapping_id) or "").strip()
        if not port:
            raise SourceUnavailable("侦听台未运行且未提供可用端口或映射 ID")
        result = self.listener_service.start(
            port=port, baudrate=int(request.get("baudrate", 115200)),
            bytesize=int(request.get("bytesize", 8)),
            parity=str(request.get("parity", "N")), stopbits=int(request.get("stopbits", 1)),
        )
        self.store.audit(actor=actor, action="listener.ensure", resource=resource, result="created")
        return {"reused": False, "listener": result}

    def stop_listener(self, *, actor: str, force: bool = False) -> dict:
        """Stop the shared listener only when no active observer depends on it."""
        if self.listener_service is None:
            raise SourceUnavailable("侦听台服务不可用")
        resource = self.listener_resource()
        active = [
            item for item in self.store.list_active()
            if item.get("payload", {}).get("source") == "listener"
            and str((item.get("payload", {}).get("target") or {}).get("mapping_id") or resource) == resource
        ]
        if active and not force:
            raise SessionBusy("侦听台仍有活动观察任务，不能停止")
        result = self.listener_service.stop()
        stopped_operations = []
        if force:
            for item in active:
                stopped = self.store.set_state(
                    item["operation_id"],
                    "source_stopped",
                    result={
                        "source": "listener",
                        "reason": "listener_stopped_by_authorized_request",
                        "stopped_at": _iso_now(),
                    },
                )
                stopped_operations.append(stopped["operation_id"])
        self.store.audit(
            actor=actor, action="listener.stop", resource=resource,
            result="forced" if force else "stopped",
        )
        return {"listener": result, "source_stopped_operations": stopped_operations}

    def create_observation(self, request: dict, *, actor: str, client_request_id: str = "") -> dict:
        source = str(request.get("source") or "")
        if source == "listener":
            return self._create_listener_observation(
                request, actor=actor, client_request_id=client_request_id,
            )
        if source != "module_log":
            raise InvalidObservation("source 仅支持 module_log 或 listener")
        target = request.get("target") or {}
        session_id = str(target.get("session_id") or "")
        if not session_id:
            raise InvalidObservation("module_log 观察必须提供 target.session_id")
        match = request.get("match") or {}
        if match.get("kind") != "literal" or not isinstance(match.get("value"), str) or not match["value"]:
            raise InvalidObservation("当前仅支持非空 literal 匹配器")
        if len(match["value"]) > 512:
            raise InvalidObservation("literal 匹配器过长")
        window = request.get("window") or {}
        if window.get("mode", "live") != "live" or window.get("start", "now") != "now":
            raise InvalidObservation("当前基础实现仅支持 live / start=now")
        timeout = int(window.get("timeout_seconds", 180))
        if not 1 <= timeout <= 3600:
            raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间")
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        session = self.module_service.get_session(session_id)
        baseline = self.module_service.logs_session(session_id, after=-1).get("lines", [])
        start_seq = max((int(line.get("seq", -1)) for line in baseline), default=-1)
        context = request.get("context") or {}
        payload = {
            "source": source, "target": {
                "session_id": session_id, "mapping_id": self.session_resource(session_id),
            }, "match": {
                "kind": "literal", "value": match["value"],
                "case_sensitive": bool(match.get("case_sensitive", True)),
            },
            "start_seq": start_seq, "deadline_monotonic": time.monotonic() + timeout,
            "context": {"before": min(max(int(context.get("before", 20)), 0), 100),
                        "after": min(max(int(context.get("after", 30)), 0), 100)},
            "log_path": session.get("log_file"),
        }
        operation = self.store.create("observation", actor, payload, client_request_id=client_request_id)
        if operation["state"] == "created":
            operation = self.store.set_state(operation["operation_id"], "waiting")
            self.store.audit(
                actor=actor, action="observation.create",
                resource=operation["payload"]["target"]["mapping_id"],
                result="waiting", operation_id=operation["operation_id"],
            )
        return operation

    def _listener_current_index_id(self, requested: str = "") -> str:
        service = self._listener_log_or_error()
        if requested:
            service.list_index_frames(requested, offset=0, limit=1)
            return requested
        status = service.status()
        index_id = str(status.get("index_id") or "")
        if not index_id:
            index_id = str(service.list_indexes().get("current_index_id") or "")
        if not index_id:
            raise SourceUnavailable("侦听台当前没有可查询的版本化索引")
        return index_id

    @staticmethod
    def _frame_matches_kind(summary: dict, frame_kind: str) -> bool:
        if not frame_kind:
            return True
        if frame_kind != "central_beacon":
            raise InvalidObservation("当前仅支持 frame_kind=central_beacon")
        expected = {"中央信标", "central_beacon", "central beacon"}
        for key in ("FrmType", "帧类型", "frame_type", "frame_kind"):
            value = summary.get(key)
            if str(value or "").strip().lower() in expected:
                return True
        return False

    @staticmethod
    def _value_at_path(value, path: str):
        current = value
        for part in str(path or "").split("."):
            if not part:
                continue
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _listener_frame_matches(self, index_id: str, frame: dict, match: dict):
        summary = frame.get("summary") or {}
        if not self._frame_matches_kind(summary, str(match.get("frame_kind") or "")):
            return False, None
        where = match.get("where") or []
        if not where:
            return True, None
        detail = self._listener_log_or_error().get_index_frame(index_id, int(frame["frame_id"]))
        for condition in where:
            if not isinstance(condition, dict):
                raise InvalidObservation("where 必须是对象数组")
            if condition.get("op", "eq") != "eq":
                raise InvalidObservation("where 当前仅支持 op=eq")
            actual = self._value_at_path(detail, str(condition.get("path") or ""))
            if actual != condition.get("value"):
                return False, detail
        return True, detail

    @staticmethod
    def _select_listener_matches(candidates: list[tuple[dict, dict | None]], match: dict) -> list[tuple[dict, dict | None]]:
        selector = str(match.get("selector") or "first")
        ordered = sorted(candidates, key=lambda item: int(item[0].get("frame_id", item[0].get("id", 0))))
        if selector == "all":
            return ordered[:100]
        if selector == "last":
            return ordered[-1:] if ordered else []
        if selector == "nth":
            position = int(match.get("nth", 1))
            if position < 1:
                raise InvalidObservation("nth 必须大于 0")
            return ordered[position - 1:position]
        if selector == "first_per_minute":
            selected = []
            seen = set()
            for item in ordered:
                minute = str(item[0].get("log_time") or "")[:5]
                if minute not in seen:
                    selected.append(item)
                    seen.add(minute)
            return selected[:100]
        if selector != "first":
            raise InvalidObservation("selector 仅支持 first、last、all、first_per_minute 或 nth")
        return ordered[:1]

    def _listener_result(self, payload: dict, selected: list[tuple[dict, dict | None]],
                         operation_id: str = "") -> dict:
        index_id = payload["index_id"]
        status = self._listener_log_or_error().status()
        matches = []
        for frame, detail in selected:
            frame_id = int(frame.get("frame_id", frame.get("id")))
            entry = {
                "frame_key": {"index_id": index_id, "frame_id": frame_id},
                "sequence": frame.get("sequence"),
                "log_time": frame.get("log_time"),
                "summary": frame.get("summary") or {},
                "detail_url": "/api/ai/v1/listener/indexes/" + index_id + "/frames/" + str(frame_id),
                "ui_url": "/static/pages/listener/index.html?index_id=" + index_id + "&frame_id=" + str(frame_id),
            }
            if detail is not None:
                entry["analysis"] = detail.get("analysis")
            matches.append(entry)
        result = {
            "source": "listener",
            "index": {
                "index_id": index_id,
                "source_log_path": status.get("source_path"),
                "start_frame_id": payload.get("start_frame_id", 0),
            },
            "matches": matches,
            "artifact_id": None,
        }
        if operation_id:
            artifact = self.store.register_artifact(
                operation_id=operation_id,
                resource=str(payload["target"].get("mapping_id") or "listener-main"),
                kind="listener_observation_result",
                content=result,
            )
            result["artifact_id"] = artifact["artifact_id"]
        return result

    def _listener_last_frame_id(self, index_id: str) -> int:
        service = self._listener_log_or_error()
        first = service.list_index_frames(index_id, offset=0, limit=1)
        total = int(first.get("total", 0))
        if total < 1:
            return 0
        page = service.list_index_frames(index_id, offset=max(total - 1, 0), limit=1)
        items = page.get("items") or []
        if not items:
            return 0
        return int(items[-1].get("frame_id", items[-1].get("id", 0)))

    def _create_listener_observation(self, request: dict, *, actor: str, client_request_id: str = "") -> dict:
        service = self._listener_log_or_error()
        target = request.get("target") or {}
        index_id = self._listener_current_index_id(str(target.get("index_id") or ""))
        window = request.get("window") or {}
        mode = str(window.get("mode") or "live")
        if mode not in ("live", "time_range"):
            raise InvalidObservation("listener window.mode 仅支持 live 或 time_range")
        if mode == "live" and str(window.get("start") or "now") != "now":
            raise InvalidObservation("listener live 观察仅支持 start=now")
        if mode == "time_range" and (not window.get("start") or not window.get("end")):
            raise InvalidObservation("listener time_range 必须提供 start 和 end")
        timeout = int(window.get("timeout_seconds", 180))
        if mode == "live" and not 1 <= timeout <= 3600:
            raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间")
        match = request.get("match") or {}
        if str(match.get("kind") or "") not in ("parsed_frame", "frame_query"):
            raise InvalidObservation("listener 观察仅支持 parsed_frame 或 frame_query")
        self._select_listener_matches([], match)
        resource = self.listener_resource(str(target.get("mapping_id") or ""))
        payload = {
            "source": "listener",
            "target": {"mapping_id": resource, "capture": "current"},
            "index_id": index_id,
            "window": {
                "mode": mode,
                "start": window.get("start", "now"),
                "end": window.get("end"),
                "timeout_seconds": timeout,
            },
            "match": match,
            "completion": request.get("completion") or {},
            "start_frame_id": self._listener_last_frame_id(index_id) if mode == "live" else 0,
            "deadline_monotonic": time.monotonic() + timeout,
        }
        operation = self.store.create("observation", actor, payload, client_request_id=client_request_id)
        if operation["state"] != "created":
            return operation
        self.store.audit(
            actor=actor, action="listener.observation.create", resource=resource,
            result="waiting" if mode == "live" else "querying", operation_id=operation["operation_id"],
        )
        if mode == "time_range":
            return self._refresh_listener_observation(operation["operation_id"], complete=True)
        return self.store.set_state(operation["operation_id"], "waiting")

    def _refresh_listener_observation(self, operation_id: str, *, complete: bool = False) -> dict:
        operation = self.store.get(operation_id)
        if operation["state"] not in ("created", "waiting"):
            return operation
        payload = operation["payload"]
        if not complete and time.monotonic() >= payload["deadline_monotonic"]:
            return self.store.set_state(
                operation_id, "timed_out",
                result=self._listener_result(payload, [], operation_id),
            )
        try:
            window = payload["window"]
            filters = {"offset": 0, "limit": 500}
            if window["mode"] == "live":
                filters["after_id"] = payload["start_frame_id"]
            else:
                filters["start_time"] = window["start"]
                filters["end_time"] = window["end"]
            page = self._listener_log_or_error().list_index_frames(payload["index_id"], **filters)
            candidates = []
            for frame in page.get("items") or []:
                matched, detail = self._listener_frame_matches(payload["index_id"], frame, payload["match"])
                if matched:
                    candidates.append((frame, detail))
            selected = self._select_listener_matches(candidates, payload["match"])
            required = int((payload.get("completion") or {}).get("match_count", 1))
            if selected and len(selected) >= max(required, 1):
                result = self._listener_result(payload, selected, operation_id)
                matched = self.store.set_state(operation_id, "matched", result=result)
                self.store.audit(
                    actor=operation["actor"], action="listener.observation.match",
                    resource=payload["target"]["mapping_id"], result="matched", operation_id=operation_id,
                )
                return matched
            if complete:
                return self.store.set_state(
                    operation_id, "succeeded",
                    result=self._listener_result(payload, [], operation_id),
                )
            return self.store.set_state(operation_id, "waiting")
        except Exception as exc:
            return self.store.set_state(operation_id, "error", error=str(exc))

    def listener_indexes(self) -> dict:
        return self._listener_log_or_error().list_indexes()

    def listener_frame_page(self, index_id: str, **filters) -> dict:
        return self._listener_log_or_error().list_index_frames(index_id, **filters)

    def listener_frame_detail(self, index_id: str, frame_id: int) -> dict:
        return self._listener_log_or_error().get_index_frame(index_id, int(frame_id))

    @staticmethod
    def listener_schema() -> dict:
        return {
            "frame_kinds": {"central_beacon": ["FrmType", "帧类型"]},
            "selectors": ["first", "last", "all", "first_per_minute", "nth"],
            "where": {"path": "analysis.full.<field>", "op": ["eq"]},
        }

    def _refresh_observation(self, operation_id: str) -> dict:
        operation = self.store.get(operation_id)
        if operation["state"] != "waiting":
            return operation
        payload = operation["payload"]
        if payload.get("source") == "listener":
            return self._refresh_listener_observation(operation_id)
        if time.monotonic() >= payload["deadline_monotonic"]:
            return self.store.set_state(operation_id, "timed_out", result={"source": payload["source"]})
        try:
            session_id = payload["target"]["session_id"]
            new_lines = self.module_service.logs_session(session_id, after=payload["start_seq"]).get("lines", [])
            needle = payload["match"]["value"]
            if not payload["match"]["case_sensitive"]:
                needle = needle.lower()
            matching = []
            for line in new_lines:
                text = str(line.get("text", ""))
                candidate = text if payload["match"]["case_sensitive"] else text.lower()
                if needle in candidate:
                    matching.append(line)
            if not matching:
                return operation
            all_lines = self.module_service.logs_session(session_id, after=-1).get("lines", [])
            match_seqs = {line.get("seq") for line in matching}
            first_index = next(index for index, line in enumerate(all_lines) if line.get("seq") in match_seqs)
            before = payload["context"]["before"]
            after = payload["context"]["after"]
            snippet = all_lines[max(0, first_index - before): first_index + after + 1]
            session = self.module_service.get_session(session_id)
            seqs = [int(line["seq"]) for line in matching]
            result = {
                "source": "module_log", "session_id": session_id, "matched_at": _iso_now(),
                "log": {
                    "artifact_id": None, "path": session.get("log_file") or payload["log_path"],
                    "line_start": min(seqs), "line_end": max(seqs), "match_lines": seqs,
                },
                "snippet": snippet,
            }
            artifact = self.store.register_artifact(
                operation_id=operation_id,
                resource=payload["target"]["mapping_id"],
                kind="module_log_observation_result",
                content=result,
            )
            result["log"]["artifact_id"] = artifact["artifact_id"]
            matched = self.store.set_state(operation_id, "matched", result=result)
            self.store.audit(
                actor=operation["actor"], action="observation.match",
                resource=payload["target"]["mapping_id"],
                result="matched", operation_id=operation_id,
            )
            return matched
        except Exception as exc:
            return self.store.set_state(operation_id, "error", error=str(exc))

    def artifact_resource(self, artifact_id: str) -> str:
        return str(self.store.get_artifact(artifact_id)["resource"])

    def artifact_manifest(self, artifact_id: str) -> dict:
        return self.store.get_artifact(artifact_id)

    def read_artifact(self, artifact_id: str) -> dict:
        return self.store.read_artifact(artifact_id)

    def audit_entries(self, resources: list[str]) -> list[dict]:
        allowed = set(resources)
        if "*" in allowed:
            return self.store.audit_entries()
        return [
            entry for entry in self.store.audit_entries()
            if not entry.get("resource") or entry.get("resource") in allowed
        ]

    def operation_resource(self, operation_id: str) -> str:
        operation = self.store.get(operation_id)
        target = operation.get("payload", {}).get("target", {})
        if target.get("mapping_id"):
            return str(target["mapping_id"])
        session_id = str(target.get("session_id") or "")
        if session_id:
            return self.session_resource(session_id)
        return "listener-main"

    def get_operation(self, operation_id: str) -> dict:
        operation = self.store.get(operation_id)
        if operation["state"] == "waiting":
            if operation["kind"] == "module_flash":
                operation = self._refresh_flash_operation(operation_id)
            else:
                operation = self._refresh_observation(operation_id)
        return operation

    def wait_operation(self, operation_id: str, timeout_seconds: int = 30) -> dict:
        bounded = min(max(int(timeout_seconds), 0), 30)
        deadline = time.monotonic() + bounded
        while True:
            operation = self.get_operation(operation_id)
            if operation["state"] in TERMINAL_STATES or time.monotonic() >= deadline:
                return operation
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def cancel_operation(self, operation_id: str, *, actor: str = "ai") -> dict:
        operation = self.store.get(operation_id)
        if operation["state"] in TERMINAL_STATES:
            return operation
        if operation["kind"] == "module_flash":
            raise SessionBusy("烧录操作不能由观察任务取消接口中断")
        result = self.store.set_state(operation_id, "cancelled", result={"cancelled_at": _iso_now()})
        self.store.audit(actor=actor, action="operation.cancel", result="cancelled",
                         operation_id=operation_id)
        return result