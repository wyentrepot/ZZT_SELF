"""REQS-0032 应用层帧工具门面：decode / build / verify 统一入口。

基线：REQS.md v1.2。约束：
- 只复用 parser_lib 适配器，不复制解析/构帧逻辑；
- 日常路由仅 {645, 698.45, 1376.2}（GW 封装/双模帧走全功能档，不在本门面）；
- 双模式容错（REQS §3.1）：
  * 自动模式 = 严格：confidence 打分（校验和参与），低于阈值拒绝并输出得分，
    禁止兜底硬解（现 ProtocolRouter 全 0 分会兜底"双模4-3"，此处已拦截）；
  * 指定模式 = 宽容：允许错误帧，强制 decode，校验失败以 warnings 表达，
    适配器异常捕获为错误 JSON，不裸崩。
- 本模块为库层：不得 import apps/* 或 shared/*。
"""
from __future__ import annotations

import dataclasses
from typing import Any, Optional

from .adapters import build_adapters
from .core.router import ProtocolRouter

DAILY_PROTOCOLS = ("645", "698.45", "1376.2")

# 自动模式置信度阈值（PLAN T1 盘点）：645 CS 错=0.4、698 HCS错=0.3/FCS错=0.5、
# 698 未知APDU=0.85、结构+校验全对=1.0 → 0.8 恰好放行"结构成立且校验通过"。
AUTO_CONFIDENCE_MIN = 0.8

_PROTOCOL_ALIASES = {
    "645": "645", "645-2007": "645", "645-1997": "645",
    "698": "698.45", "698.45": "698.45", "69845": "698.45",
    "1376.2": "1376.2", "13762": "1376.2", "10376.2": "1376.2", "103762": "1376.2",
}

_ROUTER_CACHE: Optional[ProtocolRouter] = None


def _daily_router() -> ProtocolRouter:
    """仅含日常三协议的嗅探路由（懒加载缓存）。"""
    global _ROUTER_CACHE
    if _ROUTER_CACHE is None:
        adapters, _store = build_adapters()
        daily = [a for a in adapters if a.protocol in DAILY_PROTOCOLS]
        _ROUTER_CACHE = ProtocolRouter(daily)
    return _ROUTER_CACHE


def _norm_protocol(protocol: Any) -> str:
    return _PROTOCOL_ALIASES.get(str(protocol).strip().lower(), str(protocol))


def _to_bytes(frame: Any) -> bytes:
    """hex 字符串（可含空白/0x 前缀）或 bytes → 原始帧字节。"""
    if isinstance(frame, (bytes, bytearray)):
        return bytes(frame)
    if isinstance(frame, str):
        s = "".join(frame.split())
        if s.lower().startswith("0x"):
            s = s[2:]
        if len(s) % 2 or not all(c in "0123456789abcdefABCDEF" for c in s):
            raise ValueError("帧 hex 非法：长度非偶数或含非 hex 字符")
        return bytes.fromhex(s)
    raise ValueError("帧仅支持 hex 字符串或 bytes")


def _to_jsonable(value: Any) -> Any:
    """ProtocolFrame / DataField / bytes 等递归转 JSON 可序列化结构。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f: _to_jsonable(getattr(value, f))
                for f in value.__dataclass_fields__}
    if isinstance(value, (bytes, bytearray)):
        return value.hex().upper()
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    return value


def _unsupported(protocol: Any) -> dict:
    return {
        "ok": False,
        "error": "unsupported_protocol",
        "protocol": str(protocol),
        "hint": "日常档仅支持 " + "/".join(DAILY_PROTOCOLS)
                + "；GW 封装/双模/网络层帧请走全功能档（侦听台/组网观测）",
    }


# ---------------------------------------------------------------- decode ----

def decode(frame: Any, protocol: Optional[str] = None,
           auto_min: float = AUTO_CONFIDENCE_MIN) -> dict:
    """解析一帧：不指定协议=自动嗅探（严格），指定协议=宽容强解（允许错误帧）。"""
    try:
        raw = _to_bytes(frame)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_hex", "detail": str(exc)}

    if protocol is not None:
        p = _norm_protocol(protocol)
        if p not in DAILY_PROTOCOLS:
            return _unsupported(protocol)
        adapter = _daily_router().adapters[p]
        try:
            pf = adapter.decode(raw)
        except Exception as exc:  # noqa: BLE001 - 门面承诺不裸崩
            return {
                "ok": False, "error": "decode_failed", "protocol": p,
                "detail": repr(exc),
                "hint": "帧可能残缺过度或不属于该协议；可去掉 --protocol 用自动模式嗅探",
            }
        return {
            "ok": True, "protocol": p, "mode": "specified",
            "frame": _to_jsonable(pf), "warnings": list(pf.warnings),
        }

    router = _daily_router()
    scores = {p: round(a.confidence(raw), 3) for p, a in router.adapters.items()}
    best = router.select(raw)
    best_p = best.protocol if best is not None else None
    if best_p is None or scores.get(best_p, 0.0) < auto_min:
        return {
            "ok": False, "error": "unrecognized", "scores": scores,
            "hint": "无法可靠识别协议（阈值 %.2f，校验失败会拉低得分）；"
                    "确认是错误帧时可用 --protocol 645|698.45|1376.2 指定后强解" % auto_min,
        }
    pf = best.decode(raw)
    return {
        "ok": True, "protocol": best_p, "mode": "auto",
        "confidence": scores[best_p],
        "frame": _to_jsonable(pf), "warnings": list(pf.warnings),
    }


# ----------------------------------------------------------------- build ----

def build(protocol: str, params: dict) -> dict:
    """语义构帧：1376.2 透传 build_frame_json；645/698 提供常用语义层。"""
    p = _norm_protocol(protocol)
    if p not in DAILY_PROTOCOLS:
        return _unsupported(protocol)
    params = dict(params or {})
    try:
        if p == "1376.2":
            from .adapters.adapter_10376 import build_frame_json
            r = build_frame_json(params)
            return {
                "ok": bool(r.get("ok")), "protocol": p,
                "frame_hex": r.get("frame_hex", ""), "length": r.get("length", 0),
                "warnings": list(r.get("warnings", [])),
                **({} if r.get("ok") else {"error": "build_failed",
                                           "detail": r.get("error", "")}),
            }
        if p == "645":
            return _build_645(params)
        return _build_698(params)
    except Exception as exc:  # noqa: BLE001 - 门面承诺不裸崩
        return {"ok": False, "error": "build_failed", "protocol": p,
                "detail": repr(exc)}


def _addr_bytes_645(addr: Any) -> bytes:
    """645 地址域：12 位 BCD hex，传输序（A0 在前）。"""
    s = str(addr).strip()
    if len(s) != 12 or any(c not in "0123456789abcdefABCDEF" for c in s):
        raise ValueError("645 地址须为 12 位 BCD hex（如 123456789012）")
    return bytes.fromhex(s)


def _build_645(params: dict) -> dict:
    """645 常用构帧：addr + control + (di | data)。

    - di：8 位 hex（协议附录写法，如 00010000），按小端展开并逐字节 +33H；
    - data：逻辑数据域 hex（未 +33H），门面按协议补 33H；
    - 控制码 0x03/0x08（读通信地址/广播校时）按协议不加 33H；
    - 传输层统一走 build_frame_escaped（含 1BH 转义，CS 按逻辑字节计算）。
    """
    from .adapters.adapter_645 import build_frame_escaped

    addr_b = _addr_bytes_645(params.get("addr", ""))
    control = int(str(params.get("control", "11")), 16)
    payload = bytearray()
    if params.get("di"):
        payload += int(str(params["di"]), 16).to_bytes(4, "little")
    if params.get("data"):
        payload += bytes.fromhex(str(params["data"]).replace(" ", ""))
    if control not in (0x03, 0x08):  # 与 1376.2 nested_645 构帧口径一致
        payload = bytearray((b + 0x33) & 0xFF for b in payload)
    frame = build_frame_escaped(addr_b, control, bytes(payload))
    return {"ok": True, "protocol": "645", "frame_hex": frame.hex().upper(),
            "length": len(frame), "warnings": []}


def _build_698(params: dict) -> dict:
    """698 常用构帧：addr(SA 完整域) + ca + (oad → GET-RequestNormal | apdu 透传)。

    - addr：SA 完整域 hex（首字节为长度/类型头，如 05 + 6 字节 BCD = 14 位 hex）；
    - oad：8 位 hex（OI2+属性2），构 GET-RequestNormal（05 01 + OAD + 无时标 00）；
    - apdu：直接给完整 APDU hex 时优先于 oad。
    """
    from .adapters.adapter_698 import build_frame as build_698_link

    addr_hex = str(params.get("addr", "")).replace(" ", "")
    if not addr_hex or len(addr_hex) % 2:
        raise ValueError("698 地址须为偶数位 SA 完整域 hex（如 05353781090030）")
    addr_b = bytes.fromhex(addr_hex)
    ca = int(str(params.get("ca", "00")), 16)
    control = int(str(params.get("control", "43")), 16)
    if params.get("apdu"):
        apdu = bytes.fromhex(str(params["apdu"]).replace(" ", ""))
    elif params.get("oad"):
        apdu = bytes([0x05, 0x01]) + bytes.fromhex(str(params["oad"])) + b"\x00"
    else:
        raise ValueError("698 构帧需 oad（GET-RequestNormal）或 apdu hex")
    frame = build_698_link(apdu, addr_b, ca=ca, control=control)
    return {"ok": True, "protocol": "698.45", "frame_hex": frame.hex().upper(),
            "length": len(frame), "warnings": []}


# ---------------------------------------------------------------- verify ----

def _flatten_named(frame: dict, out: dict) -> None:
    """把 frame 的 fields/items/nested 展开为 {name: value} 平铺表（嵌套同前缀防撞）。"""
    for key in ("fields", "items"):
        for entry in frame.get(key) or []:
            if isinstance(entry, dict) and entry.get("name"):
                out[str(entry["name"])] = entry.get("value")
    for idx, sub in enumerate(frame.get("nested") or []):
        inner: dict = {}
        _flatten_named(sub, inner)
        for name, value in inner.items():
            out[f"nested[{idx}].{name}"] = value


def _norm_value(value: Any) -> str:
    return str(value).strip().upper().replace(" ", "")


def _match_expect(frame: dict, expect: Optional[dict]) -> dict:
    diff = {"matched": [], "missing": [], "mismatched": []}
    if not expect:
        return diff
    flat: dict = {}
    _flatten_named(frame, flat)
    for name, wanted in dict(expect).items():
        if name not in flat:
            diff["missing"].append({"name": name, "expected": wanted})
        elif _norm_value(flat[name]) == _norm_value(wanted):
            diff["matched"].append({"name": name, "value": flat[name]})
        else:
            diff["mismatched"].append({"name": name, "expected": wanted,
                                       "actual": flat[name]})
    return diff


def verify(frame: Any, protocol: Optional[str] = None,
           expect: Optional[dict] = None,
           auto_min: float = AUTO_CONFIDENCE_MIN) -> dict:
    """帧验收：解析回验 + （可选）意图字段比对。构帧产物的 round-trip 断言依据。"""
    r = decode(frame, protocol=protocol, auto_min=auto_min)
    if not r.get("ok"):
        return r
    diff = _match_expect(r["frame"], expect)
    verified = expect is None or not (diff["missing"] or diff["mismatched"])
    out = {
        "ok": True, "protocol": r["protocol"], "mode": r.get("mode"),
        "verified": verified, "diff": diff,
        "frame": r["frame"], "warnings": r.get("warnings", []),
    }
    if "confidence" in r:
        out["confidence"] = r["confidence"]
    return out
