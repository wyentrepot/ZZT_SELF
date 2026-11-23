"""能力适配器：模拟集中器（simcon）—— 只走工作台 HTTP，不 import 领域库内部。

REQS-0041 §10.3 契约要点：
- 每个适配器只对接一个领域库：本文件**不** import ``sim_concentrator`` 内部，
  与 ``simeter.py`` / ``frames.py`` **零 import**；
- ``probe()``：依赖（工作台 simcon 子应用）不可用时返回 ``Health(ok=False, reason=...)``，
  **绝不抛异常**；
- ``base_url`` **必须注入**（构造参数或环境变量 ``WORKBENCH_BASE``），本文件不写死端口；
- 超时/重试有界：``timeout`` 是单次请求上限；``retries`` 只对 GET 生效
  （POST 重发会造成重复下发，禁止自动重试）；``max_poll_seconds`` 限制 job 轮询时长。

动作方法**一律不抛异常**，返回 ``{"ok": bool, ...}``；失败时 ``error`` 取值：
``no_base_url`` / ``unreachable`` / ``transport_error`` / ``http_<状态码>`` /
``empty_steps`` / ``poll_timeout`` / ``job_<状态>``。

``ok`` 的语义（块层必读）
-------------------------
``ok: true`` 只表示**服务/任务链路处理成功**，不表示「设备测试通过」；块层不得把
``ok`` 折算成 ``state: "pass"``（执行器同样不认 ``ok``，只认显式 state/outcome）。
判设备是否通过，看 ``verify`` 结果里的 ``steps[i]["result"]``。

verify 步骤结果字段契约（``verify`` / ``job`` / ``poll_job`` 共用）::

    {"ok": bool,                    # job 成功 且 每步 result == "pass"
     "job_id", "job_state", "verdict", "summary",
     "steps": [ {"index", "name",
                 "result": "pass"|"fail",   # **字符串**，不是布尔
                 "matched": <命中帧>,        # 命中帧在这里（hex 或解析结构）
                 "sent_hex": <下发帧 hex>,
                 "parsed": {...},            # 命中帧的解析结果
                 "reason": "..."},           # 这步的判读说明
                ... ],
     "steps_total", "steps_failed", "run_id", "frames_seq",
     "underlying_error", "attempts", "raw"}

- 步骤判定字段是**字符串** ``steps[i]["result"] == "pass"|"fail"``（不是 ``passed: bool``）；
- 命中帧在 ``steps[i]["matched"]``，另配 ``sent_hex`` / ``parsed`` / ``reason``；
- ``steps`` 为空时 ``ok`` 也可能为真（无步可判）—— 块层要判「真通过」须自行核对
  ``steps_total``，不能只看 ``ok``。

``frames()`` 无会话时后端回 **404**，本适配器原样返回
``{"ok": False, "error": "http_404", ...}``（不是设备失败）——块层据此判 skipped/unknown。

端点契约（实测自 ``libs/sim_concentrator/api.py``、``apps/workbench/ai_v2_api.py``）::

    GET  /api/simcon/status                     -> {open, port, pending_frames, session, ...}
    POST /api/simcon/close                      -> {open: false, ...}（幂等；占用自愈用）
    POST /api/simcon/build                      -> {hex: "68 0F ...", length: 15}
    GET  /api/simcon/frames?after_seq=&run_id=  -> {session_id, entries[], next_after_seq, has_more, counts}
    GET  /api/ai/v2/simcon/lease                -> {held, holder}
    POST /api/ai/v2/verification-runs           -> 202 JobEnvelope{job_id, job_state, ...}
    GET  /api/ai/v2/jobs/{id}?wait_seconds=N    -> JobEnvelope{..., result:{result:{steps[],run_id}}}
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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

#: job 终态（后端 JobState 的终态子集，多写几个同义词无害）
_TERMINAL_JOB_STATES = frozenset(
    {"succeeded", "failed", "stopped", "canceled", "cancelled", "error"})

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
    """从 FastAPI 错误体（{"detail": "..."}）提取人话，取不到就给状态码。"""
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


class SimconCapability:
    """模拟集中器能力：probe / build / verify / frames（+ status / lease 只读快照 + close）。"""

    name = "simcon"

    def __init__(self, base_url: Optional[str] = None, *,
                 timeout: float = 10.0,
                 probe_timeout: float = 5.0,
                 retries: int = 1,
                 retry_backoff: float = 0.2,
                 max_poll_seconds: float = 300.0,
                 poll_wait_seconds: float = 5.0,
                 poll_interval: float = 2.0,
                 profile: str = "anhui",
                 mapping_id: str = "simcon",
                 transport: Optional[Transport] = None,
                 env_var: str = ENV_BASE_URL) -> None:
        env_value = os.environ.get(env_var, "") if env_var else ""
        self.base_url = (base_url or env_value or "").strip().rstrip("/")
        self.timeout = max(float(timeout), 0.001)
        self.probe_timeout = max(float(probe_timeout), 0.001)
        self.retries = max(int(retries), 0)
        self.retry_backoff = max(float(retry_backoff), 0.0)
        self.max_poll_seconds = max(float(max_poll_seconds), 0.0)
        self.poll_wait_seconds = max(float(poll_wait_seconds), 0.0)
        self.poll_interval = max(float(poll_interval), 0.0)
        self.profile = profile
        self.mapping_id = mapping_id
        self.transport: Transport = transport or default_transport

    # ------------------------------------------------------------- HTTP ----

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None, *,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """发一次请求并归一化结果；失败返回 ``{"ok": False, "error": ...}``。"""
        if not self.base_url:
            return _error("no_base_url",
                          "未注入 base_url：请用构造参数 base_url 或环境变量 %s 提供"
                          % ENV_BASE_URL)
        url = self.base_url + path
        # 只有 GET 幂等，才允许有限重试；POST 重发等于重复下发，绝不自动重试。
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
                    time.sleep(self.retry_backoff)
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
                time.sleep(self.retry_backoff)
        return last

    # ------------------------------------------------------ 只读快照/健康 ----

    def status(self) -> Dict[str, Any]:
        """``GET /api/simcon/status``：串口是否已开、当前口、挂起帧数。"""
        result = self._request("GET", "/api/simcon/status",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        return {"ok": True, "open": bool(data.get("open")), "port": data.get("port"),
                "port_identity": data.get("port_identity"),
                "mapping_error": data.get("mapping_error"),
                "pending_frames": data.get("pending_frames"),
                "session": data.get("session"), "raw": data}

    def lease(self) -> Dict[str, Any]:
        """``GET /api/ai/v2/simcon/lease``：串口闸门快照（提交 verify 前应查）。"""
        result = self._request("GET", "/api/ai/v2/simcon/lease",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        return {"ok": True, "held": bool(data.get("held")),
                "holder": data.get("holder"), "raw": data}

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
        result = self._request("GET", "/api/simcon/status",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            code = result.get("error") or ""
            if code == "http_404":
                return Health(ok=False,
                              reason="工作台可达但 simcon 子应用未挂载（HTTP 404）：%s"
                                     % result.get("detail", ""))
            return Health(ok=False, reason="simcon 不可用（%s）：%s"
                                           % (code, result.get("detail", "")))
        data = result["data"] if isinstance(result["data"], dict) else {}
        reason = "simcon 可达（open=%s port=%s pending_frames=%s）" % (
            "true" if data.get("open") else "false",
            data.get("port") or "-", data.get("pending_frames"))
        lease = self.lease()   # 闸门只影响「能不能马上提交」，不影响能力健康度
        if lease.get("ok") and lease.get("held"):
            reason += "；lease 被占用"
        return Health(ok=True, reason=reason)

    # -------------------------------------------------- 释放（自愈层 closer） ----

    def close(self) -> Dict[str, Any]:
        """``POST /api/simcon/close``：关串口、释放占用 —— 自愈层的 close 能力。

        接口契约（REQS-0042 §3.4 / D2-7，由 ``blocks/common.py`` 的
        ``with_self_heal`` 经 ``capability_closer`` 按 ``SIMCON_CLOSER_NAMES``
        找到本方法，**零参**调用）：

        - **幂等**：口本来没开时后端照样回 2xx（``{"open": false}``），这里直接按
          成功透传，**不**自己判失败；
        - **不抛异常**：失败一律 ``{"ok": False, "error": ..., "detail": ...}``
          （同本文件其余动作方法）；
        - 服务端应答**原样透传**（``actor / holder / owner / session / session_id /
          run_id / job_id / port / device`` …）：自愈层要靠这些字段写「抢占了谁」
          （见 ``blocks/common.py`` 的 ``_preempt_note``），所以既铺在顶层，
          也保留在 ``raw`` 里。
        """
        result = self._request("POST", "/api/simcon/close", {})
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        closed: Dict[str, Any] = dict(data)      # 先铺服务端字段（抢占对象在这里）
        closed.update({
            # 2xx 即关成功（幂等：不看 open 字段，也未开时后端同样回 2xx）
            "ok": True,
            "error": None,
            "detail": str(data.get("detail") or data.get("message") or ""),
            "status": result["status"],
            "raw": data,
        })
        return closed

    # ---------------------------------------------------------------- 构帧 ----

    def build(self, afn: Any, fn: Any, params: Optional[Dict[str, Any]] = None, *,
              direction: str = "down", profile: Optional[str] = None,
              seq: int = 1) -> Dict[str, Any]:
        """``POST /api/simcon/build``：语义构帧预览（不触串口）。

        成功返回 ``{ok, hex(紧凑大写), hex_spaced(后端原样), length, raw}``。
        """
        body = {"afn": afn, "fn": fn, "params": dict(params or {}),
                "direction": direction, "profile": profile or self.profile,
                "seq": int(seq)}
        result = self._request("POST", "/api/simcon/build", body)
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        raw_hex = str(data.get("hex") or "")
        if not raw_hex.strip():
            return _error("bad_response", "构帧响应缺少 hex 字段", raw=data)
        return {"ok": True, "hex": "".join(raw_hex.split()).upper(),
                "hex_spaced": raw_hex, "length": data.get("length"), "raw": data}

    # -------------------------------------------------------------- 帧日志 ----

    def frames(self, after_seq: int = 0, *, run_id: Optional[str] = None,
               direction: Optional[str] = None, updown: Optional[str] = None,
               afn: Optional[Any] = None, fn: Optional[Any] = None,
               kind: Optional[str] = None, limit: int = 100,
               session_id: Optional[str] = None) -> Dict[str, Any]:
        """``GET /api/simcon/frames``：按 run_id/after_seq 增量翻会话帧日志。

        成功返回 ``{ok, entries, frames(同 entries), count, next_after_seq,
        has_more, counts, session_id, raw}``。无会话时后端回 404，这里按
        ``{"ok": False, "error": "http_404"}`` 原样返回，由块层决定 skipped。
        """
        query: Dict[str, Any] = {"limit": int(limit)}
        for key, value in (("after_seq", after_seq), ("run_id", run_id),
                           ("direction", direction), ("updown", updown),
                           ("afn", afn), ("fn", fn), ("kind", kind),
                           ("session_id", session_id)):
            if value is None or value == "" or value == 0:
                continue
            query[key] = value
        path = "/api/simcon/frames?" + urllib.parse.urlencode(query)
        result = self._request("GET", path)
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        entries = data.get("entries")
        entries = entries if isinstance(entries, list) else []
        return {"ok": True, "entries": entries, "frames": entries,
                "count": len(entries),
                "next_after_seq": data.get("next_after_seq"),
                "has_more": data.get("has_more"),
                "counts": data.get("counts"),
                "session_id": data.get("session_id"), "raw": data}

    # ---------------------------------------------------------------- 验证 ----

    def submit_verify(self, steps: List[Dict[str, Any]], *,
                      profile: Optional[str] = None,
                      mapping_id: Optional[str] = None,
                      client_request_id: Optional[str] = None,
                      task_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """``POST /api/ai/v2/verification-runs``：提交异步验证任务（202）。"""
        if not steps:
            return _error("empty_steps", "steps 为空：verify 至少需要一步")
        request_id = client_request_id or "case-runner-%s" % uuid.uuid4().hex[:12]
        task: Dict[str, Any] = {
            "mapping_id": mapping_id or self.mapping_id,
            "profile": profile or self.profile,
            "steps": [dict(step) for step in steps],
            "client_request_id": request_id,
        }
        if task_extra:
            task.update(dict(task_extra))
        body = {"task": task, "client_request_id": request_id}
        result = self._request("POST", "/api/ai/v2/verification-runs", body)
        if not result["ok"]:
            return result
        envelope = result["data"] if isinstance(result["data"], dict) else {}
        job_id = envelope.get("job_id")
        if not job_id:
            return _error("bad_response", "verification-runs 响应缺少 job_id", raw=envelope)
        return {"ok": True, "job_id": job_id,
                "job_state": envelope.get("job_state"), "status": result["status"],
                "raw": envelope}

    def job(self, job_id: str, *, wait_seconds: int = 0) -> Dict[str, Any]:
        """``GET /api/ai/v2/jobs/{id}``：读一次 job（不轮询）。"""
        if not job_id:
            return _error("missing_job_id", "job_id 为空")
        path = "/api/ai/v2/jobs/%s?wait_seconds=%d" % (
            urllib.parse.quote(str(job_id), safe=""), max(int(wait_seconds), 0))
        result = self._request("GET", path, timeout=max(self.timeout, int(wait_seconds) + 5.0))
        if not result["ok"]:
            return dict(result, job_id=job_id)
        return self._normalize_job(
            result["data"] if isinstance(result["data"], dict) else {}, attempts=1)

    def poll_job(self, job_id: str, *, wait_seconds: Optional[float] = None,
                 max_poll_seconds: Optional[float] = None) -> Dict[str, Any]:
        """轮询 job 到终态。预算用尽返回 ``poll_timeout``（不表示设备失败）。"""
        if not job_id:
            return _error("missing_job_id", "job_id 为空")
        budget = self.max_poll_seconds if max_poll_seconds is None else float(max_poll_seconds)
        wait = self.poll_wait_seconds if wait_seconds is None else float(wait_seconds)
        deadline = time.monotonic() + max(budget, 0.0)
        attempts = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _error("poll_timeout",
                              "轮询 job 超过 %.1fs 未到终态" % max(budget, 0.0),
                              job_id=job_id, attempts=attempts)
            ask = int(max(0.0, min(wait, remaining)))
            path = "/api/ai/v2/jobs/%s?wait_seconds=%d" % (
                urllib.parse.quote(str(job_id), safe=""), ask)
            result = self._request("GET", path,
                                   timeout=max(self.timeout, float(ask) + 5.0))
            attempts += 1
            if not result["ok"]:
                if (result.get("error") in ("unreachable", "transport_error")
                        and deadline - time.monotonic() > 0):
                    self._sleep(min(self.poll_interval,
                                    max(0.0, deadline - time.monotonic())))
                    continue
                return dict(result, job_id=job_id, attempts=attempts)
            envelope = result["data"] if isinstance(result["data"], dict) else {}
            state = str(envelope.get("job_state") or "")
            if state in _TERMINAL_JOB_STATES:
                return self._normalize_job(envelope, attempts=attempts)
            self._sleep(min(self.poll_interval,
                            max(0.0, deadline - time.monotonic())))

    def verify(self, steps: List[Dict[str, Any]], *,
               profile: Optional[str] = None,
               mapping_id: Optional[str] = None,
               client_request_id: Optional[str] = None,
               task_extra: Optional[Dict[str, Any]] = None,
               wait_seconds: Optional[float] = None,
               max_poll_seconds: Optional[float] = None) -> Dict[str, Any]:
        """提交 + 轮询一步到位。``ok`` 仅在 job 成功且每步 ``result == "pass"`` 时为真。"""
        submitted = self.submit_verify(
            steps, profile=profile, mapping_id=mapping_id,
            client_request_id=client_request_id, task_extra=task_extra)
        if not submitted.get("ok"):
            return submitted
        return self.poll_job(submitted["job_id"], wait_seconds=wait_seconds,
                             max_poll_seconds=max_poll_seconds)

    # ---------------------------------------------------------------- 内部 ----

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    @staticmethod
    def _normalize_job(envelope: Dict[str, Any], *, attempts: int = 0) -> Dict[str, Any]:
        """JobEnvelope → 统一结果。step 判定字段是字符串 ``result: "pass"|"fail"``。"""
        state = str(envelope.get("job_state") or "")
        outer = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
        task_result = outer.get("result") if isinstance(outer.get("result"), dict) else {}
        steps = task_result.get("steps")
        steps = steps if isinstance(steps, list) else []
        failed = [step for step in steps
                  if isinstance(step, dict)
                  and str(step.get("result") or "").lower() != "pass"]
        ok = state == "succeeded" and not failed
        underlying = outer.get("error")
        underlying = underlying if isinstance(underlying, str) else ""
        summary = envelope.get("summary")
        out: Dict[str, Any] = {
            "ok": ok, "job_id": envelope.get("job_id"), "job_state": state,
            "verdict": envelope.get("verdict"), "summary": summary,
            "steps": steps, "steps_total": len(steps), "steps_failed": len(failed),
            "run_id": task_result.get("run_id"),
            "frames_seq": task_result.get("frames_seq"),
            "underlying_error": underlying, "attempts": attempts, "raw": envelope,
        }
        if not ok:
            out["error"] = "job_not_passed" if state == "succeeded" else "job_%s" % (state or "unknown")
            detail = summary if isinstance(summary, str) and summary.strip() else underlying
            out["detail"] = detail or "job 未通过（state=%s）" % (state or "unknown")
        return out
