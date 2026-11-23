"""能力适配器：模块串口控制台（module-actions）—— 只走工作台 HTTP。

给「复位 / 控制台命令 / 读该口会话日志」这类块用（首个使用者是 ``blocks/reset.py``
的 ``reset.sta``：经 ``sta-main`` 会话向 STA 控制台发 ``reboot``）。

REQS-0041 §10.3 / §10.4 契约要点：
- 本文件**只对接工作台 HTTP**（``/api/module-serial/*``、``/api/ai/v2/module-actions``、
  ``/api/ai/v2/jobs/*``），**不得 import** ``simcon`` / ``simeter`` / ``frames`` 三个兄弟
  适配器（分层铁律 2），也不 import ``apps.*``；
- ``probe()``：依赖不可用时返回 ``Health(ok=False, reason=...)``，**绝不抛异常**；
- ``base_url`` **必须注入**（构造参数或环境变量 ``WORKBENCH_BASE``），本文件不写死 8790；
- 超时/重试有界：``timeout`` 单次上限；``retries`` 只对 GET 生效（POST 重发 = 重复下发，
  绝不自动重试）；``max_poll_seconds`` 限制 job 轮询总时长。

动作方法**一律不抛异常**，返回 ``{"ok": bool, ...}``；失败时 ``error`` 取值：
``no_base_url`` / ``unreachable`` / ``transport_error`` / ``http_<状态码>`` /
``bad_response`` / ``job_timeout`` / ``job_<状态>`` / ``no_session``。

端点契约（实测自 ``apps/module_log/app.py``、``apps/workbench/ai_v2_api.py``、
``tools/scripts/minute_collect/mclt_list_query.py``）::

    GET  /api/module-serial/ports                -> {ports[], port_details[], mapping_error}
    GET  /api/module-serial/sessions             -> {sessions[]}（含 port_identity.mapping_id、log_file）
    GET  /api/module-serial/sessions/{id}/logs   -> {lines:[{seq,ts,dir,text}]}
    GET  /api/serial-bridge/status               -> {ports:[{id,windows_com,usable,errors[]}], summary}
    POST /api/ai/v2/module-actions               -> 202 JobEnvelope
         {"action":"ensure","mapping_id":...} → result.session_id
         {"action":"send","session_id":...,"text":"reboot","append_newline":true}
         {"action":"stop","session_id":...,"force":true}
    GET  /api/ai/v2/jobs/{job_id}?wait_seconds=N -> JobEnvelope{job_state, result, summary}

已知坑（上游实测，见 REQS §10.10 / RESEARCH-reset.md）：``send`` 成功只代表「字节写出了」，
设备是否真的响应必须看该口会话日志（``reboot`` 实测 69 发 5 次首轮无回显）。
``409`` 是会话忙/闸门占用，**不排队**，本适配器不重试、交由上层策略。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # 并发期兜底：core/models.py 尚未落地时本地定义同名契约（字段与 core 一致）
    from case_runner.core.models import Health
except ImportError:  # pragma: no cover - core 落地后本分支不再生效
    from dataclasses import dataclass as _dataclass

    @_dataclass(frozen=True)
    class Health:  # type: ignore[no-redef]
        ok: bool
        reason: str = ""


ENV_BASE_URL = "WORKBENCH_BASE"

#: job 终态（后端 ``JobState`` + store 里的同义词，多写几个无害）
_TERMINAL_JOB_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "canceled", "error", "stopped", "interrupted"}
)

#: 会话日志时间戳格式：``YYYYMMDD-HH:MM:SS:mmm``（``module_serial_service.format_event_timestamp``）
_LOG_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{2}):(\d{2}):(\d{2}):(\d{3})$")

#: 会话日志整行格式：``[20261119-13:43:46:750] [RX] text``
_LOG_LINE_RE = re.compile(r"^\[([0-9]{8}-[0-9]{2}:[0-9]{2}:[0-9]{2}:[0-9]{3})\]\s*\[([A-Z]+)\]\s?(.*)$")

#: transport 契约：(method, url, json_body, timeout) -> (status_code, parsed_payload)
Transport = Callable[[str, str, Optional[Dict[str, Any]], float], Tuple[int, Any]]


def default_transport(method: str, url: str, body: Optional[Dict[str, Any]],
                      timeout: float) -> Tuple[int, Any]:
    """默认 urllib transport。连接失败/超时以 ``OSError``（含 URLError）向上抛。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read()
    except urllib.error.HTTPError as exc:  # 非 2xx 也要读出 body 里的 detail
        status = int(exc.code)
        raw = exc.read()
    return status, _parse_body(raw)


def _parse_body(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace") if raw else ""
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _error(code: str, detail: str = "", **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "error": code, "detail": detail}
    out.update(extra)
    return out


def _http_detail(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            value = payload.get(key)
            if value:
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False)
                return str(value)
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:500]
    return "HTTP %d" % status


def parse_log_timestamp(ts: Any) -> Optional[float]:
    """``"20261119-13:43:46:750"`` → epoch（本地时区）；格式不符 → ``None``。"""
    if not isinstance(ts, str):
        return None
    match = _LOG_TS_RE.match(ts.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, milli = (int(part) for part in match.groups())
    try:
        moment = datetime.datetime(
            year, month, day, hour, minute, second, milli * 1000
        )
    except ValueError:
        return None
    return moment.timestamp()


def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """解析一行落盘日志（``[ts] [RX] text``）；不符格式 → ``None``。"""
    match = _LOG_LINE_RE.match(line.rstrip("\n"))
    if not match:
        return None
    ts, direction, text = match.groups()
    return {"ts": ts, "dir": direction, "text": text, "epoch": parse_log_timestamp(ts)}


class ModuleCapability:
    """模块串口会话能力：probe / ensure / send / stop / logs（+ 只读快照）。"""

    name = "module"

    def __init__(self, base_url: Optional[str] = None, *,
                 timeout: float = 10.0,
                 probe_timeout: float = 5.0,
                 retries: int = 1,
                 retry_backoff: float = 0.2,
                 poll_wait_seconds: float = 5.0,
                 poll_interval: float = 0.5,
                 max_poll_seconds: float = 60.0,
                 mapping_id: str = "sta-main",
                 transport: Optional[Transport] = None,
                 clock: Optional[Callable[[], float]] = None,
                 sleeper: Optional[Callable[[float], None]] = None,
                 env_var: str = ENV_BASE_URL) -> None:
        env_value = os.environ.get(env_var, "") if env_var else ""
        self.base_url = (base_url or env_value or "").strip().rstrip("/")
        self.timeout = max(float(timeout), 0.001)
        self.probe_timeout = max(float(probe_timeout), 0.001)
        self.retries = max(int(retries), 0)
        self.retry_backoff = max(float(retry_backoff), 0.0)
        self.poll_wait_seconds = max(float(poll_wait_seconds), 0.0)
        self.poll_interval = max(float(poll_interval), 0.0)
        self.max_poll_seconds = max(float(max_poll_seconds), 0.0)
        self.mapping_id = mapping_id
        self.transport: Transport = transport or default_transport
        self.clock: Callable[[], float] = clock or time.time
        self.sleeper: Callable[[float], None] = sleeper or time.sleep

    # ------------------------------------------------------------- HTTP ----

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None, *,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """发一次请求并归一化结果；失败返回 ``{"ok": False, "error": ...}``。

        只有 GET 幂等，才允许有限重试；POST 重发等于重复下发（复位命令重发一次
        就是复位两次），**绝不自动重试**。
        """
        if not self.base_url:
            return _error("no_base_url",
                          "未注入 base_url：请用构造参数 base_url 或环境变量 %s 提供"
                          % ENV_BASE_URL)
        url = self.base_url + path
        attempts = self.retries + 1 if method == "GET" else 1
        limit = self.timeout if timeout is None else max(float(timeout), 0.001)
        last: Dict[str, Any] = _error("unreachable", "未发出请求")
        for index in range(attempts):
            try:
                status, payload = self.transport(method, url, body, limit)
            except OSError as exc:          # 连接拒绝 / 超时 / DNS / URLError
                last = _error("unreachable", "服务不可达：%s" % (exc,),
                              url=url, attempts=index + 1)
                if index + 1 < attempts and self.retry_backoff:
                    self.sleeper(self.retry_backoff)
                continue
            except Exception as exc:        # noqa: BLE001 - 适配器承诺不裸崩
                return _error("transport_error", "传输层异常：%r" % (exc,),
                              url=url, attempts=index + 1)
            if 200 <= status < 300:
                return {"ok": True, "status": status, "data": payload,
                        "url": url, "error": None, "detail": "", "attempts": index + 1}
            last = _error("http_%d" % status, _http_detail(payload, status),
                          url=url, status=status, body=payload, attempts=index + 1)
            if not (status >= 500) or index + 1 >= attempts:
                return last
            if self.retry_backoff:
                self.sleeper(self.retry_backoff)
        return last

    # ------------------------------------------------------ 只读快照/健康 ----

    def status(self) -> Dict[str, Any]:
        """``GET /api/module-serial/ports``：串口列表与映射错误（服务是否启用）。"""
        result = self._request("GET", "/api/module-serial/ports",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        ports = data.get("ports") if isinstance(data.get("ports"), list) else []
        return {"ok": True, "ports": ports,
                "port_details": data.get("port_details") or [],
                "mapping_error": data.get("mapping_error"), "raw": data}

    def probe(self) -> Health:
        """能力是否就绪。任何异常都收敛成 ``Health(ok=False)``。"""
        try:
            return self._probe()
        except Exception as exc:  # noqa: BLE001 - 契约：probe 不抛异常
            return Health(ok=False, reason="probe 异常：%r" % (exc,))

    def _probe(self) -> Health:
        if not self.base_url:
            return Health(ok=False,
                          reason="未注入 base_url（构造参数或环境变量 %s）" % ENV_BASE_URL)
        result = self._request("GET", "/api/module-serial/ports",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            code = result.get("error") or ""
            if code == "http_404":
                return Health(ok=False,
                              reason="工作台可达但 module-serial 子应用未挂载（HTTP 404）：%s"
                                     % result.get("detail", ""))
            if code == "http_503":
                return Health(ok=False,
                              reason="module-serial 子应用在但模块串口服务未启用（HTTP 503）")
            return Health(ok=False, reason="module-serial 不可用（%s）：%s"
                                           % (code, result.get("detail", "")))
        data = result["data"] if isinstance(result["data"], dict) else {}
        ports = data.get("ports") if isinstance(data.get("ports"), list) else []
        reason = "module-serial 可达（%d 个可选口" % len(ports)
        if data.get("mapping_error"):
            reason += "；映射错误：%s" % data["mapping_error"]
        return Health(ok=True, reason=reason + "）")

    def bridge_status(self) -> Dict[str, Any]:
        """``GET /api/serial-bridge/status``：串口桥各 mapping 的可用性（只读 preflight）。"""
        result = self._request("GET", "/api/serial-bridge/status",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        ports = data.get("ports") if isinstance(data.get("ports"), list) else []
        by_id: Dict[str, Dict[str, Any]] = {}
        for port in ports:
            if isinstance(port, dict) and port.get("id"):
                by_id[str(port["id"])] = port
        return {"ok": True, "ports": by_id, "summary": data.get("summary"), "raw": data}

    def port_status(self, mapping_id: Optional[str] = None) -> Dict[str, Any]:
        """该 mapping 的桥可用性：``{ok, usable, errors, windows_com, detail}``。"""
        target = str(mapping_id or self.mapping_id)
        bridge = self.bridge_status()
        if not bridge["ok"]:
            # 桥状态查不到不等于该口不可用 —— 标记 unknown，交由调用方决定
            return {"ok": False, "usable": None, "mapping_id": target,
                    "errors": [], "detail": "串口桥状态不可读（%s）：%s"
                                            % (bridge.get("error"), bridge.get("detail", "")),
                    "raw": bridge}
        port = bridge["ports"].get(target)
        if port is None:
            return {"ok": True, "usable": False, "mapping_id": target, "errors": [],
                    "detail": "串口桥配置里没有该 mapping_id：%r" % target, "raw": port}
        usable = port.get("usable", port.get("workbench_ready"))
        errors = port.get("errors") or []
        return {"ok": True, "usable": bool(usable), "mapping_id": target,
                "windows_com": port.get("windows_com"), "errors": list(errors),
                "detail": ("可用" if usable else "不可用：%s" % (errors or "原因未知")),
                "raw": port}

    # ---------------------------------------------------------- 会话（只读） ----

    def sessions(self) -> Dict[str, Any]:
        """``GET /api/module-serial/sessions``：当前全部模块串口会话摘要。"""
        result = self._request("GET", "/api/module-serial/sessions")
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
        return {"ok": True, "sessions": sessions, "raw": data}

    def session_for(self, mapping_id: Optional[str] = None) -> Dict[str, Any]:
        """按 ``mapping_id`` 找**现有**会话（只读；不创建也不启动）。"""
        target = str(mapping_id or self.mapping_id)
        listed = self.sessions()
        if not listed["ok"]:
            return listed
        for session in listed["sessions"]:
            if not isinstance(session, dict):
                continue
            identity = session.get("port_identity") or {}
            if identity.get("mapping_id") == target:
                return {"ok": True, "session": session, "session_id": session.get("session_id"),
                        "mapping_id": target}
        return _error("no_session", "没有 mapping_id=%r 的模块会话" % target,
                      mapping_id=target)

    # ------------------------------------------------------------- job ----

    def job(self, job_id: str, *, wait_seconds: int = 0) -> Dict[str, Any]:
        """``GET /api/ai/v2/jobs/{job_id}``（只读快照）。"""
        path = "/api/ai/v2/jobs/%s" % urllib.parse.quote(str(job_id), safe="")
        if wait_seconds:
            path += "?" + urllib.parse.urlencode({"wait_seconds": int(wait_seconds)})
        result = self._request("GET", path)
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        return {"ok": True, "job_id": data.get("job_id") or job_id,
                "job_state": str(data.get("job_state") or ""),
                "summary": data.get("summary"), "result": data.get("result"),
                "raw": data}

    def poll_job(self, job_id: str, *, wait_seconds: Optional[float] = None,
                 timeout_s: Optional[float] = None,
                 interval: Optional[float] = None) -> Dict[str, Any]:
        """轮询 job 到终态；本地轮询预算耗尽 → ``poll_timeout``（**不是失败**）。"""
        wait = self.poll_wait_seconds if wait_seconds is None else max(float(wait_seconds), 0.0)
        budget = self.max_poll_seconds if timeout_s is None else max(float(timeout_s), 0.0)
        step = self.poll_interval if interval is None else max(float(interval), 0.0)
        deadline = self.clock() + budget
        snapshot: Dict[str, Any] = {}
        while True:
            snapshot = self.job(job_id, wait_seconds=int(wait))
            if not snapshot.get("ok"):
                return snapshot
            state = snapshot.get("job_state") or ""
            if state in _TERMINAL_JOB_STATES:
                return snapshot
            if self.clock() >= deadline:
                return _error("poll_timeout",
                              "job %s 仍在 %r（本地轮询预算 %.1fs 用尽）"
                              % (job_id, state, budget),
                              job_id=job_id, job_state=state, last=snapshot)
            self.sleeper(step)

    def _action(self, payload: Dict[str, Any], *, what: str,
                timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """提交一个 module-action 并轮询到终态（统一 409/422 等错误口径）。"""
        result = self._request("POST", "/api/ai/v2/module-actions", payload)
        if not result["ok"]:
            code = result.get("error") or ""
            extra: Dict[str, Any] = {"request": payload, "action": what}
            if code == "http_409":
                extra["busy"] = True
            return _error(code, result.get("detail", ""), **extra)
        data = result["data"] if isinstance(result["data"], dict) else {}
        job_id = data.get("job_id")
        if not job_id:
            return _error("bad_response", "%s 响应缺少 job_id：%r" % (what, data),
                          raw=data)
        snapshot = self.poll_job(job_id, timeout_s=timeout_s)
        if not snapshot.get("ok"):
            return snapshot
        state = snapshot.get("job_state") or ""
        if state != "succeeded":
            return _error("job_%s" % (state or "unknown"),
                          "%s 未成功：%s" % (what, snapshot.get("summary") or state),
                          job_id=job_id, job_state=state, raw=snapshot.get("raw"))
        return {"ok": True, "job_id": job_id, "job_state": state,
                "result": snapshot.get("result") or {}, "raw": snapshot.get("raw")}

    # -------------------------------------------------------- 动作（写） ----

    def ensure(self, mapping_id: Optional[str] = None, *, module: Optional[str] = None,
               title: Optional[str] = None, timeout_s: Optional[float] = None,
               **serial: Any) -> Dict[str, Any]:
        """``POST /api/ai/v2/module-actions {action:"ensure"}`` → ``session_id``。

        幂等（同 mapping_id 可复用已有会话）；返回
        ``{ok, session_id, owned, mapping_id, session_state, job_id}``。
        """
        target = str(mapping_id or self.mapping_id)
        if not target:
            return _error("bad_response", "ensure 必须提供 mapping_id")
        payload: Dict[str, Any] = {"action": "ensure", "mapping_id": target}
        if module:
            payload["module"] = module
        if title:
            payload["title"] = title
        if serial:
            payload["serial"] = {key: value for key, value in serial.items() if value is not None}
        outcome = self._action(payload, what="ensure", timeout_s=timeout_s)
        if not outcome["ok"]:
            return outcome
        result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        session_id = result.get("session_id")
        if not session_id:
            return _error("bad_response", "ensure 结果里没有 session_id：%r" % (result,),
                          job_id=outcome.get("job_id"), raw=result)
        return {"ok": True, "session_id": str(session_id),
                "owned": bool(result.get("owned")),
                "mapping_id": result.get("mapping_id") or target,
                "session_state": result.get("state"),
                "job_id": outcome.get("job_id"), "raw": result}

    def send(self, session_id: str, text: Optional[str] = None, *,
             data_hex: Optional[str] = None, append_newline: bool = True,
             timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """``POST /api/ai/v2/module-actions {action:"send"}``。

        ``text`` 与 ``data_hex`` 二选一；``append_newline=True`` 时服务端补 ``\\r\\n``
        （如 ``"reboot"`` → 实发 8 字节）。返回 ``{ok, sent_bytes, job_id, raw}``。
        """
        sid = str(session_id or "")
        if not sid:
            return _error("bad_response", "send 必须提供 session_id")
        has_text = text is not None
        has_hex = data_hex is not None
        if has_text == has_hex:
            return _error("bad_response", "send 必须且只能提供 text 或 data_hex")
        payload: Dict[str, Any] = {"action": "send", "session_id": sid}
        if has_text:
            payload["text"] = str(text)
        else:
            payload["data_hex"] = str(data_hex)
        payload["append_newline"] = bool(append_newline)
        outcome = self._action(payload, what="send", timeout_s=timeout_s)
        if not outcome["ok"]:
            return outcome
        compact = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
        inner = compact.get("result") if isinstance(compact.get("result"), dict) else compact
        sent = inner.get("sent") if isinstance(inner, dict) else None
        return {"ok": True, "sent_bytes": sent, "append_newline": append_newline,
                "session_id": sid, "job_id": outcome.get("job_id"),
                "raw": compact}

    def stop(self, session_id: str, *, force: bool = True,
             timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """``POST /api/ai/v2/module-actions {action:"stop"}``：关会话并释放串口。"""
        sid = str(session_id or "")
        if not sid:
            return _error("bad_response", "stop 必须提供 session_id")
        return self._action({"action": "stop", "session_id": sid, "force": bool(force)},
                            what="stop", timeout_s=timeout_s)

    # ------------------------------------------------------------ 日志（只读） ----

    def logs(self, mapping_id: Optional[str] = None, *,
             after_epoch: Optional[float] = None,
             lines: int = 200,
             session_id: Optional[str] = None,
             rx_only: bool = True) -> Dict[str, Any]:
        """读该口会话日志（只读；不产生任何激励）。

        两条来源，按顺序尝试：

        1. ``GET /api/module-serial/sessions/{id}/logs``（内存环形缓冲，带 ``ts``）；
        2. 会话摘要里的 ``log_file`` 落盘文件（内存缓冲已裁剪时兜底），按
           ``[ts] [DIR] text`` 逐行解析。

        ``after_epoch`` 给了就只保留该时刻**之后**的行（时间戳解析不出 → 丢弃，因为
        「无法证明是新的」不能当作新证据，宁缺勿假 PASS）。``rx_only=True`` 只保留设备
        输出（``RX``）—— 我们自己的 ``TX`` 命令回显不构成设备端证据。

        返回 ``{ok, entries[], lines[], source, session_id, log_file, dropped}``。
        """
        target = str(mapping_id or self.mapping_id)
        resolved_session = None
        if session_id:
            sid = str(session_id)
            found = self.sessions()
            if found["ok"]:
                for session in found["sessions"]:
                    if isinstance(session, dict) and session.get("session_id") == sid:
                        resolved_session = session
                        break
        else:
            looked = self.session_for(target)
            if not looked["ok"]:
                return looked
            sid = str(looked["session_id"])
            resolved_session = looked.get("session")

        entries: List[Dict[str, Any]] = []
        source = ""
        http_logs = self._request(
            "GET", "/api/module-serial/sessions/%s/logs?after=-1"
                   % urllib.parse.quote(sid, safe=""))
        if http_logs["ok"]:
            data = http_logs["data"] if isinstance(http_logs["data"], dict) else {}
            raw_lines = data.get("lines") if isinstance(data.get("lines"), list) else []
            entries = [self._normalize_entry(item) for item in raw_lines]
            entries = [entry for entry in entries if entry is not None]
            source = "http"
        else:
            log_file = (resolved_session or {}).get("log_file")
            read = self._read_log_file(log_file)
            if not read["ok"]:
                return _error(read["error"], read["detail"], session_id=sid,
                              log_file=log_file)
            entries = read["entries"]
            source = "file"

        dropped = 0
        if after_epoch is not None:
            cutoff = float(after_epoch)
            kept: List[Dict[str, Any]] = []
            for entry in entries:
                epoch = entry.get("epoch")
                if epoch is None:
                    dropped += 1
                    continue
                if epoch >= cutoff:
                    kept.append(entry)
            entries = kept
        if rx_only:
            entries = [entry for entry in entries if str(entry.get("dir", "")).upper() == "RX"]
        limit = max(int(lines), 0)
        if limit:
            entries = entries[-limit:]
        return {"ok": True, "entries": entries,
                "lines": [entry.get("text", "") for entry in entries],
                "source": source, "session_id": sid, "mapping_id": target,
                "log_file": (resolved_session or {}).get("log_file"),
                "dropped": dropped}

    @staticmethod
    def _normalize_entry(item: Any) -> Optional[Dict[str, Any]]:
        if isinstance(item, str):
            parsed = parse_log_line(item)
            return parsed
        if not isinstance(item, dict):
            return None
        text = item.get("text", "")
        ts = item.get("ts")
        epoch = item.get("epoch")
        if epoch is None:
            epoch = parse_log_timestamp(ts)
        if epoch is None:  # 有的行只有毫秒偏移或没有时间戳 → 用 text 里可能带的 ts 再试一次
            parsed = parse_log_line(str(text))
            if parsed is not None:
                ts, epoch = parsed["ts"], parsed["epoch"]
        return {"seq": item.get("seq"), "ts": ts, "dir": item.get("dir", ""),
                "text": text, "epoch": epoch}

    @staticmethod
    def _read_log_file(log_file: Any) -> Dict[str, Any]:
        """读落盘会话日志（只读）；失败返回 ``{ok: False, error, detail}``。"""
        if not log_file:
            return {"ok": False, "error": "bad_response",
                    "detail": "会话摘要里没有 log_file，且内存日志接口不可用"}
        try:
            with open(str(log_file), encoding="utf-8", errors="replace") as handle:
                raw = handle.read()
        except OSError as exc:
            return {"ok": False, "error": "bad_response",
                    "detail": "会话日志不可读：%s（%s）" % (log_file, exc)}
        entries: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            parsed = parse_log_line(line)
            if parsed is not None:
                entries.append(parsed)
        return {"ok": True, "entries": entries}


__all__ = [
    "ENV_BASE_URL",
    "Transport",
    "ModuleCapability",
    "default_transport",
    "parse_log_line",
    "parse_log_timestamp",
]
