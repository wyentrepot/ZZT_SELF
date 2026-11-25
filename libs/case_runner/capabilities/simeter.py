"""能力适配器：模拟电表（simeter）—— 只走工作台 HTTP，不 import 领域库内部。

REQS-0041 §10.3 契约要点：
- 本文件**不** import ``sim_meter`` 内部，与 ``simcon.py`` / ``frames.py`` **零 import**；
- ``probe()``：工作台 simeter 子应用不可用时返回 ``Health(ok=False, reason=...)``，
  **绝不抛异常**；
- ``base_url`` **必须注入**（构造参数或环境变量 ``WORKBENCH_BASE``），本文件不写死端口；
- 超时/重试有界：``timeout`` 单次上限；``retries`` 只对 GET 生效（POST 不重发）。

动作方法**一律不抛异常**，返回 ``{"ok": bool, ...}``；失败时 ``error`` 取值：
``no_base_url`` / ``unreachable`` / ``transport_error`` / ``http_<状态码>`` / ``bad_response``。

端点契约（实测自 ``libs/sim_meter/api.py``）::

    GET  /api/simeter/status                     -> {brain:{addr,version,rx_count,...}, serial, session}
    GET  /api/simeter/config                     -> {addr,version,...,jzjc:{protocol_type,...},registers}
    POST /api/simeter/jzjc {protocol_type}       -> jzjc（本能力面只开 protocol_type 字段）
    POST /api/simeter/handle_hex {hex}           -> {reply_hex, reply_wire_hex, note, action}
    POST /api/simeter/open {mapping_id|port,...} -> status()（已开 409；打不开 500）
    POST /api/simeter/close                      -> status()
    GET  /api/simeter/frames?dir=&after_seq=     -> {session, entries[]}
    POST /api/simeter/send_hex {hex}             -> {sent_bytes}（未开口 409）
"""

from __future__ import annotations

import json
import os
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


class SimeterCapability:
    """模拟电表能力：probe / handle_hex / open_port / close_port / frames。"""

    name = "simeter"

    def __init__(self, base_url: Optional[str] = None, *,
                 timeout: float = 10.0,
                 probe_timeout: float = 5.0,
                 retries: int = 1,
                 retry_backoff: float = 0.2,
                 mapping_id: str = "listener",
                 transport: Optional[Transport] = None,
                 env_var: str = ENV_BASE_URL) -> None:
        env_value = os.environ.get(env_var, "") if env_var else ""
        self.base_url = (base_url or env_value or "").strip().rstrip("/")
        self.timeout = max(float(timeout), 0.001)
        self.probe_timeout = max(float(probe_timeout), 0.001)
        self.retries = max(int(retries), 0)
        self.retry_backoff = max(float(retry_backoff), 0.0)
        self.mapping_id = mapping_id
        self.transport: Transport = transport or default_transport

    # ------------------------------------------------------------- HTTP ----

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None, *,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
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

    def config(self) -> Dict[str, Any]:
        """``GET /api/simeter/config``：电表配置快照（含即装即采 jzjc）。

        表侧协议模式取 ``jzjc.protocol_type``（``sim_meter/api.py`` JzjcConfig：
        00H 透明 / 02H 645-2007 / 03H 698.45，REQS-0043 §G-6 三方一致性的
        simeter 侧判读值）。返回 ``{ok, addr, version, protocol_type, jzjc, raw}``。
        """
        result = self._request("GET", "/api/simeter/config")
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        jzjc = data.get("jzjc") if isinstance(data.get("jzjc"), dict) else {}
        return {"ok": True,
                "addr": data.get("addr"), "version": data.get("version"),
                "protocol_type": jzjc.get("protocol_type"),
                "jzjc": jzjc, "raw": data}

    def set_jzjc(self, *, protocol_type: Optional[int] = None) -> Dict[str, Any]:
        """``POST /api/simeter/jzjc``：更新即装即采配置（本能力面只开 protocol_type）。

        服务端（``sim_meter/api.py`` JzjcSpec）对未给出的字段原值保留——只传
        ``protocol_type`` 不动设备类型/描述符/下挂设备。返回
        ``{ok, protocol_type, jzjc, request}``。
        """
        body: Dict[str, Any] = {}
        if protocol_type is not None:
            body["protocol_type"] = int(protocol_type)
        if not body:
            return _error("empty_params", "未给出要修改的即装即采字段（protocol_type）")
        result = self._request("POST", "/api/simeter/jzjc", body)
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        return {"ok": True, "protocol_type": data.get("protocol_type"),
                "jzjc": data, "request": body}

    def status(self) -> Dict[str, Any]:
        """``GET /api/simeter/status``：电表脑（地址/版本/收发计数）+ 串口 + 会话。"""
        result = self._request("GET", "/api/simeter/status",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        brain = data.get("brain") if isinstance(data.get("brain"), dict) else {}
        serial = data.get("serial") if isinstance(data.get("serial"), dict) else {}
        return {"ok": True,
                "addr": brain.get("addr"), "version": brain.get("version"),
                "rx_count": brain.get("rx_count"), "tx_count": brain.get("tx_count"),
                "serial_open": bool(serial.get("open")) if serial else False,
                "port": serial.get("port"),
                "session": data.get("session"), "raw": data}

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
        result = self._request("GET", "/api/simeter/status",
                               timeout=min(self.timeout, self.probe_timeout))
        if not result["ok"]:
            code = result.get("error") or ""
            if code == "http_404":
                return Health(ok=False,
                              reason="工作台可达但 simeter 子应用未挂载（HTTP 404）：%s"
                                     % result.get("detail", ""))
            return Health(ok=False, reason="simeter 不可用（%s）：%s"
                                           % (code, result.get("detail", "")))
        data = result["data"] if isinstance(result["data"], dict) else {}
        brain = data.get("brain") if isinstance(data.get("brain"), dict) else {}
        serial = data.get("serial") if isinstance(data.get("serial"), dict) else {}
        return Health(
            ok=True,
            reason="simeter 可达（addr=%s version=%s 串口=%s）" % (
                brain.get("addr") or "-", brain.get("version") or "-",
                (serial.get("port") if serial else None) or "未打开"))

    # ------------------------------------------------------------ 纯函数入口 ----

    def handle_hex(self, hexstr: str) -> Dict[str, Any]:
        """``POST /api/simeter/handle_hex``：无串口的纯函数入口（喂一帧看应答）。

        成功返回 ``{ok, reply_hex, reply_wire_hex, note, action, raw}``；
        ``reply_hex`` 为 ``None`` 表示电表脑不回应（不是传输错误）。
        """
        payload = str(hexstr or "").strip()
        if not payload:
            return _error("empty_hex", "hex 为空：至少要一帧 645/698 请求")
        result = self._request("POST", "/api/simeter/handle_hex", {"hex": payload})
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        if not any(key in data for key in ("reply_hex", "note", "action")):
            return _error("bad_response", "handle_hex 响应结构不符（缺 reply_hex/note/action）",
                          raw=data)
        return {"ok": True, "reply_hex": data.get("reply_hex"),
                "reply_wire_hex": data.get("reply_wire_hex"),
                "note": data.get("note"), "action": data.get("action"), "raw": data}

    # ---------------------------------------------------------------- 串口 ----

    def open_port(self, mapping_id: Optional[str] = None, *,
                  port: Optional[str] = None,
                  baudrate: Optional[int] = None,
                  bytesize: Optional[int] = None,
                  parity: Optional[str] = None,
                  stopbits: Optional[int] = None) -> Dict[str, Any]:
        """``POST /api/simeter/open``：打开表口（mapping_id 或 port 二选一）。"""
        body: Dict[str, Any] = {}
        if mapping_id:
            body["mapping_id"] = mapping_id
        elif port:
            body["port"] = port
        else:
            body["mapping_id"] = self.mapping_id
        for key, value in (("baudrate", baudrate), ("bytesize", bytesize),
                           ("parity", parity), ("stopbits", stopbits)):
            if value is not None:
                body[key] = value
        result = self._request("POST", "/api/simeter/open", body)
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        return {"ok": True, "status": data, "request": body}

    def close_port(self) -> Dict[str, Any]:
        """``POST /api/simeter/close``：关闭表口并释放串口资源。"""
        result = self._request("POST", "/api/simeter/close", {})
        if not result["ok"]:
            return result
        return {"ok": True,
                "status": result["data"] if isinstance(result["data"], dict) else {}}

    def send_hex(self, hexstr: str) -> Dict[str, Any]:
        """``POST /api/simeter/send_hex``：从已打开的表口发原始帧（未开口 409）。"""
        payload = str(hexstr or "").strip()
        if not payload:
            return _error("empty_hex", "hex 为空")
        result = self._request("POST", "/api/simeter/send_hex", {"hex": payload})
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        return {"ok": True, "sent_bytes": data.get("sent_bytes"), "raw": data}

    def frames(self, after_seq: int = 0, *, direction: Optional[str] = None,
               limit: int = 200,
               session_id: Optional[str] = None) -> Dict[str, Any]:
        """``GET /api/simeter/frames``：按 dir/after_seq 翻表口会话帧日志。"""
        query: Dict[str, Any] = {"limit": int(limit)}
        if after_seq:
            query["after_seq"] = int(after_seq)
        if direction:
            query["dir"] = direction
        if session_id:
            query["session_id"] = session_id
        path = "/api/simeter/frames?" + urllib.parse.urlencode(query)
        result = self._request("GET", path)
        if not result["ok"]:
            return result
        data = result["data"] if isinstance(result["data"], dict) else {}
        entries = data.get("entries")
        entries = entries if isinstance(entries, list) else []
        return {"ok": True, "entries": entries, "frames": entries,
                "count": len(entries), "session": data.get("session"), "raw": data}
