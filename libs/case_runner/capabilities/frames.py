"""能力适配器：帧级工具（frames）—— 进程内只读，唯一允许 import 领域库的适配器。

REQS-0041 §10.3 契约要点：
- 本文件只对接一个领域库：``parser_lib.facade``（decode / build / verify），**只读**；
- 与 ``simcon.py`` / ``simeter.py`` **零 import**；不走 HTTP、不需要工作台、不需要串口；
- 不复制任何解析/构帧逻辑：``decode`` / ``build`` / ``verify`` 一律转调门面，返回值与
  门面同构（门面自己就承诺不裸崩），本类只补「懒导入 + 异常收敛 + 便捷取值」；
- ``probe()``：facade 不可导入或自检解码失败时返回 ``Health(ok=False, reason=...)``，
  **绝不抛异常**。

协议入参（两种写法都收，块层可直接传 1376.2 的「通信协议类型」字节）：
- **名字**：``"645"`` / ``"698.45"`` / ``"1376.2"`` 及门面别名（``645-2007`` / ``698`` …），
  原样转给门面，行为与以前完全一致；
- **整数协议码**（``int``；或纯数字字符串且落在映射表里，如 ``"2"``）：
  按 :data:`PROTOCOL_CODE_MAP` 归一 ——
  ``1``→645-1997、``2``→645-2007、``3``→698.45、``0``（透明传输）→ **不指定协议**
  （门面自动嗅探）。返回里带 ``protocol_code=<码>``，供调用方判断「按协议未递归」。
  未知 **整数** 码 → ``{"ok": false, "error": "unsupported_protocol", "protocol_code": 码}``，
  **不**悄悄退化成自动嗅探。
  注意 ``"645"`` / ``"69845"`` 这种「纯数字但其实是名字」的入参仍按名字处理（老行为不变）。

整数码在 ``nested`` / ``meter_addr`` 里按 1376.2 语义理解：它描述的是**内嵌帧**的协议，
所以外层帧交门面自动嗅探，再按该码筛选内嵌帧结构（名字入参仍是「按该名字硬解外层帧」，
老行为不变）。否则把 1376.2 封装帧当裸 645 强解，拿到的「地址域」会是外层噪声。

用途（REQS §10.7 事实 E，已实测）：帧级判据的基础。例：从 02H-F1 上行帧里取出内嵌
645 的地址域 —— ``meter_addr(frame, protocol=2)`` 直接给
``{"ok": True, "addr": "000000000001", "protocol_code": 2}``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:  # 并发期兜底：core/models.py 尚未落地时本地定义同名契约（字段与 core 一致）
    from case_runner.core.models import Health
except ImportError:  # pragma: no cover - core 落地后本分支不再生效
    from dataclasses import dataclass as _dataclass

    @_dataclass(frozen=True)
    class Health:  # type: ignore[no-redef]
        ok: bool
        reason: str = ""


#: probe() 自检帧：一条合法 645 读数据帧（facade.build 实测产物）
SELF_CHECK_FRAME = "6800000000000168110433333433B316"

#: 实测产物：02H-F1 上行帧，内嵌 645，地址域 = 000000000001（REQS §10.7 事实 E 口径）
NESTED_645_FRAME = (
    "682100830000000000000201000210"
    "6800000000000168110433333433B3161416"
)

ADDR_FIELD_NAME = "地址域"

#: 1376.2「通信协议类型」字节 → 门面协议名（``None`` = 不指定，交门面自动嗅探）
PROTOCOL_CODE_MAP: Dict[int, Optional[str]] = {
    0: None,        # 透明传输：不按协议递归
    1: "645",       # 645-1997
    2: "645",       # 645-2007
    3: "698.45",
}

#: 未知整数码的报错提示
PROTOCOL_CODE_HINT = "0 透明传输 / 1 645-1997 / 2 645-2007 / 3 698.45"


def _protocol_code(protocol: Any) -> Optional[int]:
    """整数协议码入参识别：``int`` 原样，纯数字字符串仅当落在映射表里才算码。

    ``"645"`` / ``"69845"`` 这类纯数字**名字**不能当码，否则老行为会被破坏
    （``"645"`` 被读成协议码 645 → unsupported_protocol）。
    """
    if isinstance(protocol, bool):        # bool 是 int 子类，别把 True 当协议码 1
        return None
    if isinstance(protocol, int):
        return protocol
    if isinstance(protocol, str):
        text = protocol.strip()
        if text.isdigit() and int(text) in PROTOCOL_CODE_MAP:
            return int(text)
    return None


def _resolve_protocol(
    protocol: Any,
) -> Tuple[Any, Optional[int], Optional[Dict[str, Any]]]:
    """协议入参归一 → ``(门面协议名, 协议码, 错误 dict)``。

    名字入参原样返回（``code is None``）；整数码查 :data:`PROTOCOL_CODE_MAP`，
    ``0`` 归一为 ``None``（自动嗅探）；未知码给 ``unsupported_protocol`` 错误。
    """
    code = _protocol_code(protocol)
    if code is None:
        return protocol, None, None
    if code not in PROTOCOL_CODE_MAP:
        return protocol, code, _error(
            "unsupported_protocol",
            "整数协议码 %r 不在映射表（%s）" % (code, PROTOCOL_CODE_HINT),
            protocol_code=code,
        )
    return PROTOCOL_CODE_MAP[code], code, None


def _same_protocol(left: Any, right: Any) -> bool:
    """内嵌帧 ``structure`` 与协议名比对（容 ``698``/``698.45`` 这类写法）。"""
    def norm(value: Any) -> str:
        text = str(value or "").strip().lower()
        return {"698": "698.45", "69845": "698.45",
                "645-1997": "645", "645-2007": "645", "6451997": "645",
                "6452007": "645"}.get(text, text)

    return bool(norm(left)) and norm(left) == norm(right)


def _id_field_names(entry: Any) -> str:
    return str(entry.get("name") or "") if isinstance(entry, dict) else ""


def _error(code: str, detail: str = "", **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "error": code, "detail": detail}
    out.update(extra)
    return out


def _entries(frame: Any) -> List[dict]:
    if not isinstance(frame, dict):
        return []
    out: List[dict] = []
    for key in ("fields", "items"):
        value = frame.get(key)
        if isinstance(value, list):
            out.extend([entry for entry in value if isinstance(entry, dict)])
    return out


def _named_field_value(frame: Any, name: str) -> Any:
    for entry in _entries(frame):
        if _id_field_names(entry) == name:
            return entry.get("value")
    return None


def _field_names(frame: Any) -> List[str]:
    return [name for name in (_id_field_names(entry) for entry in _entries(frame)) if name]


def _import_facade() -> Any:
    """懒导入 ``parser_lib.facade``（测试可 monkeypatch 本函数模拟缺失）。"""
    from parser_lib import facade  # noqa: PLC0415 - 故意懒导入：不在 import 期强绑领域库
    return facade


class FramesCapability:
    """帧级能力：decode / build / verify（+ nested / meter_addr 便捷取值）。"""

    name = "frames"

    def __init__(self, facade: Any = None) -> None:
        #: 注入点：测试用假门面；生产留空即懒导入 parser_lib.facade
        self._facade_override = facade

    # --------------------------------------------------------------- 门面 ----

    def facade(self) -> Any:
        return self._facade_override if self._facade_override is not None else _import_facade()

    def probe(self) -> Health:
        """facade 可用性 + 一次最小解码自检。任何异常都收敛成 Health(ok=False)。"""
        try:
            return self._probe()
        except Exception as exc:  # noqa: BLE001 - 契约：probe 不抛异常
            return Health(ok=False, reason="probe 异常：%r" % (exc,))

    def _probe(self) -> Health:
        try:
            facade = self.facade()
        except Exception as exc:  # noqa: BLE001 - ImportError 及其它导入期异常
            return Health(ok=False, reason="parser_lib.facade 不可导入：%r" % (exc,))
        try:
            result = facade.decode(SELF_CHECK_FRAME)
        except Exception as exc:  # noqa: BLE001 - 门面本不抛，抛了也不能崩
            return Health(ok=False, reason="自检解码抛异常：%r" % (exc,))
        if not isinstance(result, dict) or not result.get("ok"):
            code = result.get("error") if isinstance(result, dict) else result
            return Health(ok=False, reason="自检解码失败：%s" % (code,))
        if result.get("protocol") != "645":
            return Health(ok=False,
                          reason="自检解码协议不符（期望 645，实得 %s）"
                                 % (result.get("protocol"),))
        return Health(ok=True, reason="parser_lib.facade 可用（自检解码 645 通过）")

    # --------------------------------------------------------------- 三个动作 ----

    def decode(self, frame: Any, protocol: Any = None) -> Dict[str, Any]:
        """解析一帧（hex 字符串或 bytes）。

        ``protocol`` 给名字 = 按该协议强解；给整数协议码 = 先归一（``0`` → 自动嗅探）；
        不给 = 自动嗅探（严格）。
        """
        resolved, code, err = _resolve_protocol(protocol)
        if err is not None:
            return err
        try:
            result = self.facade().decode(frame, protocol=resolved)
        except Exception as exc:  # noqa: BLE001 - 适配器承诺不裸崩
            return _error("facade_error", "facade.decode 异常：%r" % (exc,))
        if code is not None and isinstance(result, dict):
            result = dict(result, protocol_code=code)
        return result

    def build(self, protocol: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """语义构帧：``{ok, protocol, frame_hex, length, warnings}``。"""
        try:
            return self.facade().build(protocol, dict(params or {}))
        except Exception as exc:  # noqa: BLE001
            return _error("facade_error", "facade.build 异常：%r" % (exc,))

    def verify(self, frame: Any, protocol: Any = None,
               expect: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """帧回验：解析 +（可选）意图字段比对，``verified`` 才是断言依据。"""
        resolved, code, err = _resolve_protocol(protocol)
        if err is not None:
            return err
        try:
            result = self.facade().verify(frame, protocol=resolved, expect=expect)
        except Exception as exc:  # noqa: BLE001
            return _error("facade_error", "facade.verify 异常：%r" % (exc,))
        if code is not None and isinstance(result, dict):
            result = dict(result, protocol_code=code)
        return result

    # ------------------------------------------------------- 内嵌帧便捷取值 ----

    def nested(self, frame: Any, protocol: Any = None,
               index: int = 0) -> Dict[str, Any]:
        """取第 ``index`` 个内嵌帧（如 02H-F1 里的 645）。成功返回 ``{ok, structure, frame}``。

        整数协议码按 1376.2「通信协议类型」语义描述**内嵌帧**：外层交门面自动嗅探，
        再按该码筛内嵌帧结构；筛不到 → ``nested_protocol_mismatch``（绝不猜一个交差）。
        """
        resolved, code, err = _resolve_protocol(protocol)
        if err is not None:
            return err
        outer_protocol = None if code is not None else resolved
        decoded = self.decode(frame, protocol=outer_protocol)
        if not decoded.get("ok"):
            return decoded
        inner = decoded.get("frame")
        nested = inner.get("nested") if isinstance(inner, dict) else None
        nested = [sub for sub in nested if isinstance(sub, dict)] \
            if isinstance(nested, list) else []
        if not nested:
            return _error("no_nested",
                          "该帧没有内嵌帧（protocol=%s structure=%s）"
                          % (decoded.get("protocol"),
                             inner.get("structure") if isinstance(inner, dict) else None))
        if code is not None and resolved is not None:
            candidates = [sub for sub in nested
                          if _same_protocol(sub.get("structure"), resolved)]
            if not candidates:
                return _error(
                    "nested_protocol_mismatch",
                    "协议码 %r（%s）与内嵌帧协议不符：实得 %s"
                    % (code, resolved,
                       [sub.get("structure") for sub in nested]),
                    protocol_code=code, expected_protocol=resolved,
                    available=[sub.get("structure") for sub in nested])
            nested = candidates
        if not isinstance(index, int) or index < 0 or index >= len(nested):
            return _error("nested_index_out_of_range",
                          "内嵌帧下标 %r 越界（共 %d 个）" % (index, len(nested)))
        sub = nested[index]
        out = {"ok": True, "index": index, "count": len(nested),
               "structure": sub.get("structure"), "frame": sub,
               "protocol": decoded.get("protocol")}
        if code is not None:
            out["protocol_code"] = code
        return out

    def meter_addr(self, frame: Any, protocol: Any = None,
                   index: int = 0) -> Dict[str, Any]:
        """从内嵌帧取「地址域」—— 02H-F1 → 645 表地址的帧级判据（事实 E）。

        成功返回 ``{ok, addr, structure, frame}``（整数协议码入参另带 ``protocol_code``）；
        取不到返回 ``no_nested`` / ``nested_protocol_mismatch`` / ``no_addr_field``，
        **绝不猜值**。
        """
        sub = self.nested(frame, protocol=protocol, index=index)
        if not sub.get("ok"):
            return sub
        inner = sub.get("frame") or {}
        value = _named_field_value(inner, ADDR_FIELD_NAME)
        if value is None:
            return _error("no_addr_field",
                          "内嵌帧没有「%s」字段" % ADDR_FIELD_NAME,
                          structure=sub.get("structure"),
                          available=_field_names(inner), frame=inner)
        out = {"ok": True, "addr": value, "structure": sub.get("structure"),
               "index": index, "frame": inner}
        if "protocol_code" in sub:
            out["protocol_code"] = sub["protocol_code"]
        return out
