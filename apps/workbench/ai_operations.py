"""Safe orchestration adapters for the AI workbench control plane."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import regex

from loghooks.engine import Engine
from loghooks.rules import RuleLoader
from loghooks.sources import ParsedLine

from .ai_store import OperationStore, TERMINAL_STATES


class SourceUnavailable(RuntimeError):
    pass


class SessionBusy(RuntimeError):
    pass


class InvalidObservation(ValueError):
    pass


_FORBIDDEN_OBSERVATION_KEYS = frozenset({
    "path", "file", "files", "root", "directory", "upload", "test_data_root",
})
_MAX_CURSOR_RANGE = 10_000
_MAX_LISTENER_CURSOR_RANGE = 500
_MODULE_LOG_LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIControlService:
    """Uses in-process backend services; it never opens a second serial handle."""

    def __init__(self, *, module_service=None, listener_service=None, log_service=None,
                 store=None, resource_registry=None, simcon_service=None,
                 trace_service=None):
        self.module_service = module_service
        self.listener_service = listener_service
        self.log_service = log_service
        self.resource_registry = resource_registry
        self.simcon_service = simcon_service
        self.trace_service = trace_service
        self.store = store or OperationStore()
        self._simcon_verify_gate = threading.Lock()
        self._simcon_verify_running = False

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
        # Only the command selected by the established text-before-hex behavior
        # participates in the idempotency identity.  The operation used to retain
        # only its target, which incorrectly treated different writes as replays.
        if "text" in request:
            command = {
                "type": "text",
                "text": str(request["text"]),
                "append_newline": bool(request.get("append_newline", True)),
            }
        elif "data_hex" in request:
            command = {"type": "data_hex", "data_hex": str(request["data_hex"])}
        else:
            command = {"type": "missing"}
        action = self.store.create(
            "module_send", actor,
            {"target": {"session_id": session_id, "mapping_id": resource}, "command": command},
            client_request_id=client_request_id,
        )
        if action["state"] != "created":
            return action
        try:
            if command["type"] == "text":
                result = self.module_service.write_text_session(
                    session_id, command["text"], command["append_newline"],
                )
            elif command["type"] == "data_hex":
                result = self.module_service.write_session(session_id, command["data_hex"])
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

    @staticmethod
    def _reject_unsafe_observation_input(value: Any, *, _location: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                field = str(key).casefold()
                # `match.where[].path` is the established parsed-frame field
                # selector (for example `analysis.full.beacon.flag`), not a
                # filesystem input. Every other path-shaped field is rejected.
                selector_path = field == "path" and _location[-2:] == ("match", "where")
                if field in _FORBIDDEN_OBSERVATION_KEYS and not selector_path:
                    raise InvalidObservation("观察请求不得提供 path/file/files/root")
                AIControlService._reject_unsafe_observation_input(
                    nested, _location=_location + (field,),
                )
        elif isinstance(value, list):
            for nested in value:
                AIControlService._reject_unsafe_observation_input(nested, _location=_location)

    def validate_observation_request(self, request: Any) -> None:
        """Validate the common API boundary before resource or replay access."""
        if not isinstance(request, dict):
            raise InvalidObservation("观察请求必须是对象")
        self._reject_unsafe_observation_input(request)
        target = request.get("target")
        if target is not None and not isinstance(target, dict):
            raise InvalidObservation("观察请求 target 必须是对象")

    @staticmethod
    def _line_seq(line: dict) -> int:
        value = line.get("seq")
        if isinstance(value, bool):
            raise InvalidObservation("模块日志 seq 必须是整数")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise InvalidObservation("模块日志缺少稳定 seq") from exc

    def _module_lines(self, session_id: str) -> list[dict]:
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        raw = self.module_service.logs_session(session_id, after=-1).get("lines", [])
        lines = [dict(line) for line in raw if isinstance(line, dict)]
        return sorted(lines, key=self._line_seq)

    @staticmethod
    def _normalise_leaf_match(match: dict, *, module: str) -> dict:
        if not isinstance(match, dict):
            raise InvalidObservation("match 必须是对象")
        kind = str(match.get("kind") or "").strip()
        case_sensitive = bool(match.get("case_sensitive", True))
        if kind == "literal":
            value = match.get("value")
            if not isinstance(value, str) or not value:
                raise InvalidObservation("literal 匹配器必须提供非空 value")
            if len(value) > 512:
                raise InvalidObservation("literal 匹配器过长")
            return {"kind": kind, "value": value, "case_sensitive": case_sensitive}
        if kind == "regex":
            value = match.get("value")
            if not isinstance(value, str) or not value:
                raise InvalidObservation("regex 匹配器必须提供非空 value")
            if len(value) > 256:
                raise InvalidObservation("regex pattern 不能超过 256 字符")
            flags = 0 if case_sensitive else regex.IGNORECASE
            try:
                regex.compile(value, flags)
            except regex.error as exc:
                raise InvalidObservation("regex pattern 非法") from exc
            return {"kind": kind, "value": value, "case_sensitive": case_sensitive}
        if kind == "loghook_rule":
            rule_id = match.get("rule_id")
            if not isinstance(rule_id, str) or not rule_id:
                raise InvalidObservation("loghook_rule 必须提供 rule_id")
            loader = RuleLoader().load_all()
            rule = next((item for item in loader.filter_by_module(module) if item.id == rule_id), None)
            if rule is None or "module_log" not in rule.source:
                raise InvalidObservation("loghook_rule 不存在或不适用于当前模块")
            return {"kind": kind, "rule_id": rule_id}
        raise InvalidObservation("match.kind 仅支持 literal、regex、loghook_rule、sequence 或 not_seen")

    @classmethod
    def _normalise_module_match(cls, match: dict, *, module: str) -> dict:
        if not isinstance(match, dict):
            raise InvalidObservation("match 必须是对象")
        kind = str(match.get("kind") or "").strip()
        if kind == "sequence":
            steps = match.get("steps")
            if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
                raise InvalidObservation("sequence.steps 必须是 1 到 16 个叶子匹配器")
            if isinstance(match.get("max_interval_ms"), bool):
                raise InvalidObservation("sequence.max_interval_ms 必须是正整数")
            try:
                max_interval_ms = int(match.get("max_interval_ms"))
            except (TypeError, ValueError) as exc:
                raise InvalidObservation("sequence 必须提供 max_interval_ms") from exc
            if not 1 <= max_interval_ms <= 3_600_000:
                raise InvalidObservation("sequence.max_interval_ms 必须在 1 到 3600000 之间")
            return {
                "kind": kind,
                "steps": [cls._normalise_leaf_match(step, module=module) for step in steps],
                "max_interval_ms": max_interval_ms,
            }
        if kind == "not_seen":
            inner = match.get("matcher")
            if not isinstance(inner, dict) or inner.get("kind") in ("sequence", "not_seen"):
                raise InvalidObservation("not_seen 只能包装一个 literal、regex 或 loghook_rule 叶子")
            return {"kind": kind, "matcher": cls._normalise_leaf_match(inner, module=module)}
        return cls._normalise_leaf_match(match, module=module)

    @staticmethod
    def _line_timestamp_ms(line: dict) -> float | None:
        value = line.get("ts")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) * 1000
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%Y%m%d-%H:%M:%S:%f")
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_MODULE_LOG_LOCAL_TIMEZONE)
        return parsed.timestamp() * 1000

    @staticmethod
    def _parsed_module_line(line: dict, index: int) -> ParsedLine:
        text = str(line.get("text") or "")
        return ParsedLine(
            source="module_log", raw=text, text=text,
            time=str(line.get("ts") or ""), direction=str(line.get("dir") or ""),
            metadata={"_idx": index},
        )

    def _matching_loghook_lines(self, lines: list[dict], match: dict, *, module: str) -> list[dict]:
        loader = RuleLoader().load_all()
        rule = next(
            (item for item in loader.filter_by_module(module) if item.id == match["rule_id"]),
            None,
        )
        if rule is None:
            raise InvalidObservation("loghook_rule 不存在或不适用于当前模块")
        engine = Engine([rule], source="module_log")
        matched_indexes: list[int] = []
        for index, line in enumerate(lines):
            events_before = len(engine.events)
            engine.feed(self._parsed_module_line(line, index))
            for event in engine.events[events_before:]:
                if event.rule_id == rule.id and event.source_line_idx is not None:
                    matched_indexes.append(event.source_line_idx)
        return [lines[index] for index in dict.fromkeys(matched_indexes) if 0 <= index < len(lines)]

    def _matching_leaf_lines(self, lines: list[dict], match: dict, *, module: str) -> list[dict]:
        kind = match["kind"]
        if kind == "loghook_rule":
            return self._matching_loghook_lines(lines, match, module=module)
        if kind == "literal":
            needle = match["value"] if match["case_sensitive"] else match["value"].lower()
            return [
                line for line in lines
                if needle in (str(line.get("text") or "") if match["case_sensitive"]
                              else str(line.get("text") or "").lower())
            ]
        flags = 0 if match["case_sensitive"] else regex.IGNORECASE
        compiled = regex.compile(match["value"], flags)
        matched = []
        for line in lines:
            try:
                if compiled.search(str(line.get("text") or "")[:4096], timeout=0.1):
                    matched.append(line)
            except TimeoutError:
                continue
        return matched

    def _matching_sequence_groups(self, lines: list[dict], match: dict, *, module: str) -> list[list[dict]]:
        step_hits = [self._matching_leaf_lines(lines, step, module=module) for step in match["steps"]]
        positions = [{self._line_seq(line): line for line in hits} for hits in step_hits]
        ordered = sorted(lines, key=self._line_seq)
        groups: list[list[dict]] = []
        previous_group_end = -1
        for first in ordered:
            first_seq = self._line_seq(first)
            if first_seq <= previous_group_end or first_seq not in positions[0]:
                continue
            selected = [positions[0][first_seq]]
            previous_seq = first_seq
            for candidates in positions[1:]:
                next_line = next((line for line in ordered if self._line_seq(line) > previous_seq
                                  and self._line_seq(line) in candidates), None)
                if next_line is None:
                    selected = []
                    break
                selected.append(next_line)
                previous_seq = self._line_seq(next_line)
            if not selected:
                continue
            first_ms = self._line_timestamp_ms(selected[0])
            last_ms = self._line_timestamp_ms(selected[-1])
            if first_ms is None or last_ms is None or last_ms < first_ms:
                continue
            if last_ms - first_ms <= match["max_interval_ms"]:
                groups.append(selected)
                # Sequence matches are intentionally greedy and non-overlapping:
                # a line consumed by one completion cannot start the next one.
                previous_group_end = self._line_seq(selected[-1])
        return groups

    def _module_match_lines(self, lines: list[dict], match: dict, *, module: str) -> tuple[list[dict], int]:
        if match["kind"] == "sequence":
            groups = self._matching_sequence_groups(lines, match, module=module)
            return [line for group in groups for line in group], len(groups)
        matched = self._matching_leaf_lines(lines, match, module=module)
        return matched, len(matched)

    @staticmethod
    def _bounded_snippet(lines: list[dict], matches: list[dict], context: dict) -> list[dict]:
        if not matches:
            return []
        hit_seqs = {AIControlService._line_seq(line) for line in matches}
        first_index = next(
            (index for index, line in enumerate(lines) if AIControlService._line_seq(line) in hit_seqs),
            0,
        )
        before = context["before"]
        after = context["after"]
        return lines[max(0, first_index - before): first_index + after + 1]

    def _module_observation_result(self, payload: dict, *, condition_met: bool,
                                   matches: list[dict], all_lines: list[dict], operation_id: str,
                                   reason: str | None = None) -> dict:
        seqs = [self._line_seq(line) for line in matches]
        result = {
            "source": "module_log",
            "session_id": payload["target"]["session_id"],
            "condition_met": condition_met,
            "matched_at": _iso_now() if condition_met else None,
            "log": {
                "artifact_id": None,
                "path": payload["log_path"],
                "line_start": min(seqs) if seqs else None,
                "line_end": max(seqs) if seqs else None,
                "match_lines": seqs,
            },
            "snippet": self._bounded_snippet(all_lines, matches, payload["context"]),
        }
        if reason is not None:
            result["reason"] = reason
        artifact = self.store.register_artifact(
            operation_id=operation_id,
            resource=payload["target"]["mapping_id"],
            kind="module_log_observation_result",
            content=result,
        )
        result["log"]["artifact_id"] = artifact["artifact_id"]
        return result

    @staticmethod
    def _normalise_bounded_observation_int(value: Any, *, field: str,
                                           minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidObservation(f"{field} 必须是整数")
        if not minimum <= value <= maximum:
            raise InvalidObservation(f"{field} 必须在 {minimum} 到 {maximum} 之间")
        return value

    @classmethod
    def _normalise_observation_context(cls, context: Any) -> dict:
        if context is None:
            context = {}
        if not isinstance(context, dict):
            raise InvalidObservation("context 必须是对象")
        return {
            "before": cls._normalise_bounded_observation_int(
                context.get("before", 20), field="context.before", minimum=0, maximum=100,
            ),
            "after": cls._normalise_bounded_observation_int(
                context.get("after", 30), field="context.after", minimum=0, maximum=100,
            ),
        }

    @classmethod
    def _normalise_observation_completion(cls, completion: Any) -> dict:
        if completion is None:
            completion = {}
        if not isinstance(completion, dict):
            raise InvalidObservation("completion 必须是对象")
        return {
            "match_count": cls._normalise_bounded_observation_int(
                completion.get("match_count", 1), field="completion.match_count", minimum=1, maximum=100,
            ),
        }

    @staticmethod
    def _observation_idempotency_fingerprint(identity: dict) -> str:
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _observation_request_identity(request: dict) -> dict:
        return {key: value for key, value in request.items() if key != "client_request_id"}

    @classmethod
    def _observation_replay_fingerprint(cls, request: dict) -> str:
        return cls._observation_idempotency_fingerprint(cls._observation_request_identity(request))

    # -- 模拟集中器（simcon）：验证任务 / 单步下发 / 会话帧日志 -----------------
    _SIMCON_RESOURCE = "simcon"

    def _require_simcon(self):
        if self.simcon_service is None:
            raise SourceUnavailable("模拟集中器服务不可用")
        return self.simcon_service

    def simcon_verify(self, request: dict, *, actor: str, client_request_id: str = "") -> dict:
        """异步执行模拟集中器验证任务：202 + operation_id，wait 轮询到终态。

        同一会话同一时刻只允许一个验证任务（并发返回 409）；任务不可取消。
        """
        service = self._require_simcon()
        request = dict(request or {})
        with self._simcon_verify_gate:
            if self._simcon_verify_running:
                raise SessionBusy("已有模拟集中器验证任务在运行")
            operation = self.store.create(
                "simcon_verify", actor,
                {"target": {"mapping_id": self._SIMCON_RESOURCE}, "task": request},
                client_request_id=client_request_id,
            )
            if operation["state"] != "created":
                return operation
            self._simcon_verify_running = True
            started = self.store.set_state(operation["operation_id"], "waiting")
        thread = threading.Thread(
            target=self._run_simcon_verify, name="ai-simcon-verify",
            args=(operation["operation_id"], request, actor), daemon=True,
        )
        thread.start()
        self.store.audit(actor=actor, action="simcon.verify", resource=self._SIMCON_RESOURCE,
                         result="waiting", operation_id=operation["operation_id"])
        return started

    def _run_simcon_verify(self, operation_id: str, request: dict, actor: str) -> None:
        try:
            result = self.simcon_service.verify(request)
            self.store.set_state(operation_id, "succeeded", result=result)
            self.store.audit(actor=actor, action="simcon.verify", resource=self._SIMCON_RESOURCE,
                             result="succeeded", operation_id=operation_id)
        except Exception as exc:
            self.store.set_state(operation_id, "error", error=str(exc))
            self.store.audit(actor=actor, action="simcon.verify", resource=self._SIMCON_RESOURCE,
                             result="error", operation_id=operation_id)
        finally:
            self._simcon_verify_running = False

    def simcon_step(self, request: dict, *, actor: str, client_request_id: str = "") -> dict:
        """同步单步语义执行（下发指定 afn/fn 或等待一帧）。"""
        service = self._require_simcon()
        request = dict(request or {})
        action = self.store.create(
            "simcon_step", actor,
            {"target": {"mapping_id": self._SIMCON_RESOURCE}, "step": request},
            client_request_id=client_request_id,
        )
        if action["state"] != "created":
            return action
        try:
            result = service.step(request)
            completed = self.store.set_state(action["operation_id"], "succeeded", result=result)
            self.store.audit(actor=actor, action="simcon.step", resource=self._SIMCON_RESOURCE,
                             result="succeeded", operation_id=action["operation_id"])
            return completed
        except (SessionBusy, ValueError) as exc:
            self.store.set_state(action["operation_id"], "error", error=str(exc))
            self.store.audit(actor=actor, action="simcon.step", resource=self._SIMCON_RESOURCE,
                             result="error", operation_id=action["operation_id"])
            raise
        except Exception as exc:
            failed = self.store.set_state(action["operation_id"], "error", error=str(exc))
            self.store.audit(actor=actor, action="simcon.step", resource=self._SIMCON_RESOURCE,
                             result="error", operation_id=action["operation_id"])
            raise SourceUnavailable(f"模拟集中器单步执行失败：{exc}") from exc

    def simcon_frames(self, filters: dict | None = None) -> dict:
        """会话帧日志查询（本次下发过什么帧 / CCO 主动上报过什么帧 / 按 afn 过滤上行帧）。"""
        service = self._require_simcon()
        return service.frames(**dict(filters or {}))

    def simcon_session(self) -> dict:
        return self._require_simcon().session()

    def simcon_open(self, spec: dict | None, *, actor: str) -> dict:
        result = self._require_simcon().open(dict(spec or {}))
        self.store.audit(actor=actor, action="simcon.open", resource=self._SIMCON_RESOURCE,
                         result="succeeded")
        return result

    def simcon_close(self, *, actor: str) -> dict:
        result = self._require_simcon().close()
        self.store.audit(actor=actor, action="simcon.close", resource=self._SIMCON_RESOURCE,
                         result="succeeded")
        return result

    def idempotent_operation(self, client_request_id: str) -> dict | None:
        return self.store.by_client_request_id(client_request_id)

    def create_observation(self, request: dict, *, actor: str, client_request_id: str = "") -> dict:
        # Reject every filesystem-shaped field before looking up an idempotency
        # replay, so a reused key cannot bypass the request boundary.
        self.validate_observation_request(request)
        source = str(request.get("source") or "")
        replay_fingerprint = self._observation_replay_fingerprint(request)
        existing = self.store.by_client_request_id(client_request_id)
        if existing is not None and existing.get("idempotency_replay_fingerprint") == replay_fingerprint:
            return existing
        if source == "listener":
            return self._create_listener_observation(
                request, actor=actor, client_request_id=client_request_id,
                replay_fingerprint=replay_fingerprint,
            )
        if source != "module_log":
            raise InvalidObservation("source 仅支持 module_log 或 listener")
        target = request.get("target") or {}
        session_id = str(target.get("session_id") or "")
        if not session_id:
            raise InvalidObservation("module_log 观察必须提供 target.session_id")
        if self.module_service is None:
            raise SourceUnavailable("模块日志服务不可用")
        session = self.module_service.get_session(session_id)
        module = str(session.get("module") or "common").lower()
        match = self._normalise_module_match(request.get("match") or {}, module=module)
        window = request.get("window") or {}
        mode = str(window.get("mode") or "live")
        if mode not in ("live", "time_range", "cursor_range"):
            raise InvalidObservation("module_log window.mode 仅支持 live、time_range 或 cursor_range")
        timeout = 0
        baseline = self._module_lines(session_id)
        start_seq = max((self._line_seq(line) for line in baseline), default=-1)
        end_seq = None
        start_time_ms = None
        end_time_ms = None
        if mode == "live":
            if str(window.get("start") or "now") != "now":
                raise InvalidObservation("module_log live 观察仅支持 start=now")
            if isinstance(window.get("timeout_seconds", 180), bool):
                raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间")
            try:
                timeout = int(window.get("timeout_seconds", 180))
            except (TypeError, ValueError) as exc:
                raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间") from exc
            if not 1 <= timeout <= 3600:
                raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间")
        elif mode == "time_range":
            start_time_ms = self._line_timestamp_ms({"ts": window.get("start")})
            end_time_ms = self._line_timestamp_ms({"ts": window.get("end")})
            if start_time_ms is None or end_time_ms is None or start_time_ms > end_time_ms:
                raise InvalidObservation("time_range 必须提供递增的 ISO 8601 start/end")
            retained_times = [
                timestamp for line in baseline
                if (timestamp := self._line_timestamp_ms(line)) is not None
            ]
            if not retained_times:
                raise InvalidObservation("time_range 当前内存日志没有可归一化时间")
            if start_time_ms < min(retained_times) or end_time_ms > max(retained_times):
                raise InvalidObservation("time_range 超出当前保留的内存日志边界")
        else:
            if isinstance(window.get("start_seq"), bool) or isinstance(window.get("end_seq"), bool):
                raise InvalidObservation("cursor_range start_seq/end_seq 必须是整数")
            try:
                start_seq = int(window.get("start_seq"))
                end_seq = int(window.get("end_seq"))
            except (TypeError, ValueError) as exc:
                raise InvalidObservation("cursor_range 必须提供 start_seq/end_seq") from exc
            if start_seq > end_seq:
                raise InvalidObservation("cursor_range start_seq 不能大于 end_seq")
            if end_seq - start_seq + 1 > _MAX_CURSOR_RANGE:
                raise InvalidObservation("cursor_range 范围过大")
            if not baseline:
                raise InvalidObservation("cursor_range 当前没有可用内存日志")
            first_retained = self._line_seq(baseline[0])
            last_retained = self._line_seq(baseline[-1])
            if start_seq < first_retained:
                raise InvalidObservation("cursor_range 起点已被内存环形缓冲裁剪")
            if end_seq > last_retained:
                raise InvalidObservation("cursor_range 尚未闭合或超出当前内存缓冲")
        context = self._normalise_observation_context(request.get("context"))
        completion = self._normalise_observation_completion(request.get("completion"))
        payload = {
            "source": source,
            "target": {"session_id": session_id, "mapping_id": self.session_resource(session_id)},
            "module": module,
            "match": match,
            "window": {"mode": mode,
                       "start": "now" if mode == "live" else window.get("start") if mode == "time_range" else None,
                       "end": window.get("end") if mode == "time_range" else None,
                       "start_seq": start_seq if mode == "cursor_range" else None,
                       "end_seq": end_seq,
                       "start_time_ms": start_time_ms, "end_time_ms": end_time_ms},
            "start_seq": start_seq,
            "deadline_monotonic": time.monotonic() + timeout if mode == "live" else None,
            "context": context,
            "completion": completion,
            "log_path": session.get("log_file"),
        }
        fingerprint = self._observation_idempotency_fingerprint({
            "semantic": {
                "kind": "observation",
                "source": source,
                "resource": {"mapping_id": payload["target"]["mapping_id"], "session_id": session_id},
                "match": match,
                "window": {
                    "mode": mode, "start": payload["window"]["start"], "end": payload["window"]["end"],
                    "start_seq": payload["window"]["start_seq"], "end_seq": payload["window"]["end_seq"],
                    "timeout_seconds": timeout,
                },
                "context": context,
                "completion": completion,
            },
            "request": self._observation_request_identity(request),
        })
        operation = self.store.create(
            "observation", actor, payload, client_request_id=client_request_id,
            idempotency_fingerprint=fingerprint,
            idempotency_replay_fingerprint=replay_fingerprint,
        )
        if operation["state"] != "created":
            return operation
        self.store.audit(
            actor=actor, action="observation.create", resource=payload["target"]["mapping_id"],
            result="waiting" if mode == "live" else "querying", operation_id=operation["operation_id"],
        )
        if mode != "live":
            return self._refresh_module_observation(operation["operation_id"], complete=True)
        return self.store.set_state(operation["operation_id"], "waiting")

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
            "condition_met": bool(matches),
            "index": {
                "index_id": index_id,
                "start_frame_id": payload.get("start_frame_id", 0),
            },
            "matches": matches,
            "snippet": matches[:10],
            "artifact_id": None,
        }
        source_log_path = self._listener_index_source_path(index_id)
        if source_log_path is not None:
            result["index"]["source_log_path"] = source_log_path
        if payload["window"]["mode"] == "cursor_range":
            result["index"]["end_frame_id"] = payload["window"]["end_frame_id"]
        if operation_id:
            artifact = self.store.register_artifact(
                operation_id=operation_id,
                resource=str(payload["target"].get("mapping_id") or "listener-main"),
                kind="listener_observation_result",
                content=result,
            )
            result["artifact_id"] = artifact["artifact_id"]
        return result

    def _listener_index_source_path(self, index_id: str) -> str | None:
        """Return source metadata for this exact versioned index, never the current index's state."""
        try:
            service = self._listener_log_or_error()
            listing = service.list_indexes()
            indexes = listing.get("indexes") or []
        except Exception:
            return None
        record = next(
            (item for item in indexes if isinstance(item, dict) and item.get("index_id") == index_id),
            None,
        )
        if isinstance(record, dict):
            source_path = record.get("source_path")
            if source_path:
                return str(source_path)

        # Legacy/single-current services may expose the trusted current index id
        # separately while omitting per-index metadata.  Falling back is safe only
        # for that exact current id; historical index ids must never inherit the
        # current source path.
        current_index_id = str(listing.get("current_index_id") or "")
        if current_index_id != index_id:
            return None
        try:
            status = service.status()
        except Exception:
            return None
        status_index_id = str(status.get("index_id") or "")
        if status_index_id and status_index_id != index_id:
            return None
        source_path = status.get("source_path")
        return str(source_path) if source_path else None

    def _listener_last_frame_id(self, index_id: str) -> int:
        bounds = self._listener_frame_bounds(index_id)
        return bounds[1] if bounds is not None else 0

    def _listener_frame_bounds(self, index_id: str) -> tuple[int, int] | None:
        service = self._listener_log_or_error()
        first = service.list_index_frames(index_id, offset=0, limit=1)
        total = int(first.get("total", 0))
        if total < 1:
            return None
        page = service.list_index_frames(index_id, offset=max(total - 1, 0), limit=1)
        items = page.get("items") or []
        if not items:
            return None
        first_items = first.get("items") or []
        if not first_items:
            return None
        return (
            int(first_items[0].get("frame_id", first_items[0].get("id", 0))),
            int(items[-1].get("frame_id", items[-1].get("id", 0))),
        )

    @staticmethod
    def _normalise_listener_cursor_id(value: Any, *, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidObservation(f"listener cursor_range {field} 必须是整数")
        if value < 0:
            raise InvalidObservation(f"listener cursor_range {field} 不能为负数")
        return value

    def _create_listener_observation(self, request: dict, *, actor: str, client_request_id: str = "",
                                     replay_fingerprint: str = "") -> dict:
        self._listener_log_or_error()
        target = request.get("target") or {}
        window = request.get("window") or {}
        window_type = window.get("type")
        if window_type is not None:
            if window_type != "cursor_range":
                raise InvalidObservation("listener window.type 仅支持 cursor_range")
            if window.get("mode") not in (None, "cursor_range"):
                raise InvalidObservation("listener window.type 与 window.mode 不一致")
            mode = "cursor_range"
        else:
            mode = str(window.get("mode") or "live")
        if mode not in ("live", "time_range", "cursor_range"):
            raise InvalidObservation("listener window.mode 仅支持 live、time_range 或 cursor_range")
        target_index_id = str(target.get("index_id") or "")
        target_capture = str(target.get("capture") or "")
        cursor_start = None
        cursor_end = None
        if mode == "cursor_range":
            cursor_index_id = window.get("index_id")
            if not isinstance(cursor_index_id, str) or not cursor_index_id:
                raise InvalidObservation("listener cursor_range 必须提供 index_id")
            if target_index_id and target_index_id != cursor_index_id:
                raise InvalidObservation("listener cursor_range index_id 与 target.index_id 不一致")
            if target_capture and target_capture != "current" and target_capture != cursor_index_id:
                raise InvalidObservation("listener cursor_range index_id 与 target.capture 不一致")
            try:
                index_id = self._listener_current_index_id(cursor_index_id)
            except KeyError as exc:
                raise InvalidObservation("listener cursor_range index_id 不存在") from exc
            if "start_frame_id" not in window or "end_frame_id" not in window:
                raise InvalidObservation("listener cursor_range 必须提供 start_frame_id 和 end_frame_id")
            cursor_start = self._normalise_listener_cursor_id(
                window["start_frame_id"], field="start_frame_id",
            )
            cursor_end = self._normalise_listener_cursor_id(
                window["end_frame_id"], field="end_frame_id",
            )
            if cursor_start > cursor_end:
                raise InvalidObservation("listener cursor_range start_frame_id 不能大于 end_frame_id")
            if cursor_end - cursor_start + 1 > _MAX_LISTENER_CURSOR_RANGE:
                raise InvalidObservation("listener cursor_range 范围过大")
            bounds = self._listener_frame_bounds(index_id)
            if bounds is None or cursor_start < bounds[0] or cursor_end > bounds[1]:
                raise InvalidObservation("listener cursor_range 超出索引边界")
        else:
            try:
                index_id = self._listener_current_index_id(target_index_id)
            except KeyError as exc:
                raise InvalidObservation("listener index_id 不存在") from exc
        if mode == "live" and str(window.get("start") or "now") != "now":
            raise InvalidObservation("listener live 观察仅支持 start=now")
        if mode == "time_range" and (not window.get("start") or not window.get("end")):
            raise InvalidObservation("listener time_range 必须提供 start 和 end")
        timeout = 180
        if mode == "live":
            if isinstance(window.get("timeout_seconds", 180), bool):
                raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间")
            try:
                timeout = int(window.get("timeout_seconds", 180))
            except (TypeError, ValueError) as exc:
                raise InvalidObservation("timeout_seconds 必须在 1 到 3600 之间") from exc
            if not 1 <= timeout <= 3600:
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
                "start_frame_id": cursor_start,
                "end_frame_id": cursor_end,
            },
            "match": match,
            "completion": request.get("completion") or {},
            "start_frame_id": (
                self._listener_last_frame_id(index_id) if mode == "live"
                else cursor_start if mode == "cursor_range" else 0
            ),
            "deadline_monotonic": time.monotonic() + timeout if mode == "live" else None,
        }
        fingerprint = self._observation_idempotency_fingerprint({
            "semantic": {
                "kind": "observation",
                "source": "listener",
                "resource": {
                    "mapping_id": resource,
                    "index_id": index_id,
                    "capture": target_capture or "current",
                },
                "match": match,
                "window": {
                    "mode": mode, "start": payload["window"]["start"], "end": payload["window"]["end"],
                    "start_frame_id": cursor_start, "end_frame_id": cursor_end,
                    "timeout_seconds": timeout,
                },
                "completion": request.get("completion") or {},
                "context": request.get("context") or {},
            },
            "request": self._observation_request_identity(request),
        })
        operation = self.store.create(
            "observation", actor, payload, client_request_id=client_request_id,
            idempotency_fingerprint=fingerprint,
            idempotency_replay_fingerprint=replay_fingerprint,
        )
        if operation["state"] != "created":
            return operation
        self.store.audit(
            actor=actor, action="listener.observation.create", resource=resource,
            result="waiting" if mode == "live" else "querying", operation_id=operation["operation_id"],
        )
        if mode in ("time_range", "cursor_range"):
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
            elif window["mode"] == "time_range":
                filters["start_time"] = window["start"]
                filters["end_time"] = window["end"]
            else:
                filters["start_id"] = window["start_frame_id"]
                filters["end_id"] = window["end_frame_id"]
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
        return self._refresh_module_observation(operation_id)

    def _finish_module_live_deadline(self, operation_id: str, payload: dict) -> dict:
        """Finish an unverified live window without using post-deadline buffer state."""
        result = self._module_observation_result(
            payload, condition_met=False, matches=[], all_lines=[], operation_id=operation_id,
            reason="live_window_unverified",
        )
        return self.store.set_state(operation_id, "timed_out", result=result)

    def _refresh_module_observation(self, operation_id: str, *, complete: bool = False) -> dict:
        operation = self.store.get(operation_id)
        if operation["state"] not in ("created", "waiting"):
            return operation
        payload = operation["payload"]
        try:
            if (
                payload["window"]["mode"] == "live"
                and time.monotonic() >= payload["deadline_monotonic"]
            ):
                # Module rows do not carry an arrival timestamp.  Once this
                # process first observes the deadline as elapsed, accepting
                # the current buffer would let post-window rows influence it.
                return self._finish_module_live_deadline(operation_id, payload)
            session_id = payload["target"]["session_id"]
            all_lines = self._module_lines(session_id)
            if payload["window"]["mode"] == "cursor_range":
                candidates = [
                    line for line in all_lines
                    if payload["window"]["start_seq"] <= self._line_seq(line) <= payload["window"]["end_seq"]
                ]
            elif payload["window"]["mode"] == "time_range":
                candidates = [
                    line for line in all_lines
                    if (line_ms := self._line_timestamp_ms(line)) is not None
                    and payload["window"]["start_time_ms"] <= line_ms <= payload["window"]["end_time_ms"]
                ]
            else:
                candidates = [line for line in all_lines if self._line_seq(line) > payload["start_seq"]]
            match = payload["match"]
            if match["kind"] == "not_seen":
                counterexamples, _ = self._module_match_lines(
                    candidates, match["matcher"], module=payload["module"],
                )
                if counterexamples:
                    result = self._module_observation_result(
                        payload, condition_met=False, matches=counterexamples[:1],
                        all_lines=all_lines, operation_id=operation_id,
                    )
                    return self.store.set_state(operation_id, "succeeded", result=result)
                if complete:
                    result = self._module_observation_result(
                        payload, condition_met=True, matches=[], all_lines=all_lines,
                        operation_id=operation_id,
                    )
                    matched = self.store.set_state(operation_id, "matched", result=result)
                    self.store.audit(
                        actor=operation["actor"], action="observation.match",
                        resource=payload["target"]["mapping_id"], result="matched",
                        operation_id=operation_id,
                    )
                    return matched
                return operation
            matching, matched_count = self._module_match_lines(
                candidates, match, module=payload["module"],
            )
            required = payload["completion"]["match_count"]
            if matching and matched_count >= required:
                result = self._module_observation_result(
                    payload, condition_met=True, matches=matching, all_lines=all_lines,
                    operation_id=operation_id,
                )
                matched = self.store.set_state(operation_id, "matched", result=result)
                self.store.audit(
                    actor=operation["actor"], action="observation.match",
                    resource=payload["target"]["mapping_id"], result="matched",
                    operation_id=operation_id,
                )
                return matched
            if complete:
                result = self._module_observation_result(
                    payload, condition_met=False, matches=[], all_lines=all_lines,
                    operation_id=operation_id,
                )
                return self.store.set_state(operation_id, "succeeded", result=result)
            if time.monotonic() >= payload["deadline_monotonic"]:
                result = self._module_observation_result(
                    payload, condition_met=False, matches=[], all_lines=all_lines,
                    operation_id=operation_id,
                )
                return self.store.set_state(operation_id, "timed_out", result=result)
            return operation
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
            elif operation["kind"] == "listener_trace":
                pass  # 追踪线程异步落终态，waiting 态无需刷新
            elif operation["kind"] == "observation":
                operation = self._refresh_observation(operation_id)
            # 其余 kind（simcon_verify/simcon_step/module_send 等）由各自后台
            # 线程异步落终态，waiting 态仅原样返回，不做 observation 刷新，
            # 避免误把非观察操作当 module_log 观察刷新（payload 无 window）
            # 而抛 KeyError 并把错误写回 operation（此前导致 simcon_verify
            # 查询即被误判为 error 'window'）。
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

    # -- 侦听台通信流追踪（需求 0009）：202 + wait，live 与回放统一异步化 --------

    def _require_listener_trace(self):
        if self.trace_service is None:
            raise SourceUnavailable("侦听台追踪服务不可用")
        return self.trace_service

    def listener_trace_create(self, request: dict, *, actor: str,
                              client_request_id: str = "") -> dict:
        """创建追踪操作：回放=执行完整报告；live=注册句柄并返回首份快照。

        与 simcon_verify 同构：202 + operation_id，GET /operations/{id}/wait 复用；
        取消走通用 cancel_operation（终态后不可取消）。
        """
        service = self._require_listener_trace()
        request = dict(request or {})
        # 特征校验前置：坏特征在 HTTP 层即 422，不进入 operation 生命周期
        from listener.trace_service import validate_feature
        validate_feature(request)
        operation = self.store.create(
            "listener_trace", actor,
            {"target": {"mapping_id": self.listener_resource()}, "feature": request},
            client_request_id=client_request_id,
        )
        if operation["state"] != "created":
            return operation
        started = self.store.set_state(operation["operation_id"], "waiting")
        thread = threading.Thread(
            target=self._run_listener_trace, name="ai-listener-trace",
            args=(operation["operation_id"], request, actor), daemon=True,
        )
        thread.start()
        self.store.audit(actor=actor, action="listener.trace", result="waiting",
                         resource=self.listener_resource(),
                         operation_id=operation["operation_id"])
        return started

    def _run_listener_trace(self, operation_id: str, request: dict, actor: str) -> None:
        try:
            service = self._require_listener_trace()
            if (request.get("window") or {}).get("mode") == "live":
                handle = service.register_live(request, actor=actor)
                snapshot = service.live_snapshot(handle["trace_id"])
                result = {"mode": "live", "trace": handle, "snapshot": snapshot}
            else:
                result = {"mode": "replay", "report": service.run_replay(request)}
            self.store.set_state(operation_id, "succeeded", result=result)
            self.store.audit(actor=actor, action="listener.trace", result="succeeded",
                             resource=self.listener_resource(), operation_id=operation_id)
        except Exception as exc:
            self.store.set_state(operation_id, "error", error=str(exc))
            self.store.audit(actor=actor, action="listener.trace", result="error",
                             resource=self.listener_resource(), operation_id=operation_id)

    def listener_traces_list(self) -> dict:
        return {"traces": self._require_listener_trace().list_live()}

    def listener_trace_get(self, trace_id: str) -> dict:
        """读取 live 追踪当前快照（回放报告不走此路径，随操作结果直接返回）。"""
        return self._require_listener_trace().live_snapshot(trace_id)
