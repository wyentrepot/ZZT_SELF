"""应答引擎：模块上行帧 → 模拟集中器自动应答下行帧。

支持两种帧格式：
- 标准 1376.2（单 68，Q/GDW 10376.2）：68 L C R A AFN DT1 DT2 ... CS 16
- CCO 本地协议（单 68）：68 L ctrl info afn DT1 DT2 buff CS 16

内置应答规则表（默认模板）覆盖常见 AFN；验证任务可传入自定义应答模板
（按上行帧特征匹配）覆盖内置默认。规则结构：

    {
      "id": "reply.01F1_confirm",
      "match": {"afn": 1, "fn": 1},       # 按 AFN / FN 匹配
      "reply": {"afn": 0x00, "ctrl": "confirm"},  # 应答：确认帧
    }

reply 字段说明（构造应答帧的参数）：
- afn:    应答帧的 AFN（如 0x00 确认、0x03 查询数据返回）
- userdata_builder: "confirm" | "deny" | "echo" | "copy_rtsa" 或 callable
- fn:     应答帧的 Fn（默认 F1）
- info:   应答帧信息域 dict（含 seq 报文序列号）
- address: 应答帧地址域 dict
- userdata: 可选，直接给定字节
- format: "local"（缺省按上行帧同格式）

匹配逻辑：
- 上行帧先 decode 得到信封字段（单 68）；
- 依次遍历规则，取第一个 match 命中的；
- 用例提供的覆盖规则优先于内置表。
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from sim_concentrator.frame_codec import (
    build_13762_frame,
    build_local_13762_frame,
    decode_frame,
    decode_local_13762_frame,
)


def _norm_afn(v) -> Optional[int]:
    """把 AFN 表示归一化为 int（支持 0x01 / 1 / '01' / '0x01'）。"""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 16)
    return None


def _is_local_frame(raw: bytes) -> bool:
    """判断是否为 CCO 本地协议帧（单 68）：第 3 字节不是 0x68。"""
    if len(raw) < 15 or raw[0] != 0x68:
        return False
    return raw[3] != 0x68


def _now_bcd_time(dt=None) -> bytes:
    """当前时间 → 6 字节 BCD：秒 分 时 日 月 年（Q/GDW 10376.2 时间格式）。

    供 14H-F2（路由请求集中器时钟）下行应答使用。
    """
    from datetime import datetime
    now = dt or datetime.now()
    year = now.year % 100
    return bytes([
        ((now.second // 10) << 4) | (now.second % 10),
        ((now.minute // 10) << 4) | (now.minute % 10),
        ((now.hour // 10) << 4) | (now.hour % 10),
        ((now.day // 10) << 4) | (now.day % 10),
        ((now.month // 10) << 4) | (now.month % 10),
        ((year // 10) << 4) | (year % 10),
    ])


class ReplyRule:
    def __init__(self, rule: dict):
        self.id = rule.get("id", "reply.custom")
        self.match = rule.get("match", {})  # {"afn": int, "fn": int, ...}
        self.reply = rule.get("reply", {})

    def matches(self, decoded: dict) -> bool:
        m = self.match
        afn = m.get("afn")
        if afn is not None:
            expect = _norm_afn(afn)
            # 统一单 68：AFN 取 fields.AFN.raw（或兼容顶层 afn）
            got = decoded.get("afn")
            if not isinstance(got, int):
                afn_field = decoded.get("fields", {}).get("AFN", {})
                got = afn_field.get("raw")
                if not isinstance(got, int):
                    value = str(afn_field.get("value", ""))
                    if f"0x{expect:02X}" in value.upper().replace("0x", "0X"):
                        got = expect
            if got != expect:
                return False
        fn = m.get("fn")
        if fn is not None:
            # 统一单 68：FN 取 fields.FN.raw（或兼容顶层 fn）
            got_fn = decoded.get("fn")
            if not isinstance(got_fn, int):
                fn_field = decoded.get("fields", {}).get("FN", {})
                got_fn = fn_field.get("raw")
            if not isinstance(got_fn, int) or got_fn != int(fn):
                return False
        return True


class Responder:
    """应答引擎：内置规则表 + 用例覆盖规则。"""

    def __init__(self, override_rules: Optional[List[dict]] = None, builtin: bool = True):
        """builtin=False：仅用 override 规则（用于常驻自动应答等需要精确控制
        应答面的场景，排除内置查询 echo 规则以免对查询/主动查询帧引发应答回环）。"""
        self._overrides = [ReplyRule(r) for r in (override_rules or [])]
        self._builtin = [ReplyRule(r) for r in _BUILTIN_RULES] if builtin else []

    def list_rules(self) -> List[dict]:
        """返回当前生效规则（覆盖 + 内置），供 API 展示。"""
        out = []
        for r in self._overrides + self._builtin:
            out.append({"id": r.id, "match": r.match, "reply": r.reply})
        return out

    # -- 主入口 ---------------------------------------------------------
    def reply_for(self, raw: bytes, seq_override: Optional[int] = None) -> Optional[bytes]:
        """对上行帧构造应答帧；无匹配规则返回 None（不应答）。

        单 68 统一帧（CCO 本地 = 单 68 标准，无需区分），一律 decode_frame。
        """
        try:
            decoded = decode_frame(raw)
        except Exception:
            return None
        rule = self._find(decoded)
        if rule is None:
            return None
        return self._build_reply(decoded, rule, seq_override)

    def _find(self, decoded: dict) -> Optional[ReplyRule]:
        for r in self._overrides:
            if r.matches(decoded):
                return r
        for r in self._builtin:
            if r.matches(decoded):
                return r
        return None

    def _build_reply(self, decoded: dict, rule: ReplyRule,
                     seq_override: Optional[int]) -> Optional[bytes]:
        r = rule.reply
        fmt = r.get("format", "auto")

        # 本地帧：构造单 68 确认帧
        if fmt == "local" or (fmt == "auto" and "afn" in decoded and isinstance(decoded["afn"], int)):
            afn = _norm_afn(r.get("afn", 0x00))
            fn = int(r.get("fn", 1))
            buff = b""
            ub = r.get("userdata_builder", "confirm")
            if isinstance(ub, str) and ub == "deny":
                buff = b"\x01"
            elif isinstance(ub, str) and ub == "confirm":
                buff = b""
            elif isinstance(ub, str) and ub == "echo":
                buff = decoded.get("buff", b"")
            elif isinstance(ub, str) and ub == "copy":
                buff = decoded.get("buff", b"")
            else:
                ud = r.get("userdata", b"")
                if isinstance(ud, str):
                    buff = bytes.fromhex(ud.replace(" ", "")) if ud else b""
                else:
                    buff = bytes(ud or b"")
            return build_local_13762_frame(afn=afn, fn=fn, buff=buff)

        # 标准帧（单 68）：构造应答帧
        # 从上行帧 fields 提取 seq（信息域R报文序列号）与地址域 A
        info_field = decoded.get("fields", {}).get("信息域R", {})
        info_raw = info_field.get("raw", "")
        info_bytes = bytes.fromhex(info_raw) if isinstance(info_raw, str) and info_raw else None
        seq = seq_override
        if seq is None and info_bytes is not None and len(info_bytes) >= 6:
            seq = info_bytes[5]  # 信息域第6字节 = 报文序列号
        if seq is None:
            seq = 0

        # 地址域：沿用上行帧地址（src/dst），供应答帧回包
        addr_field = decoded.get("fields", {}).get("地址域A", {})
        addr_value = addr_field.get("value", "")
        address = None
        if isinstance(addr_value, str) and addr_value and addr_value != "(无)":
            # 地址域 hex 形如 "123456789012999999999999"（src+dst 连续 BCD）
            s = addr_value
            if len(s) >= 24:
                address = {"src": s[0:12], "dst": s[12:24]}

        afn = _norm_afn(r.get("afn", 0x00))
        fn = int(r.get("fn", 1))

        ub = r.get("userdata_builder", "none")
        if isinstance(ub, str):
            if ub == "confirm":
                userdata = b"\x00"
            elif ub == "deny":
                userdata = b"\x01"
            elif ub == "echo":
                userdata = bytes.fromhex(
                    decoded.get("raw_hex", ""))[4:-2] if decoded.get("raw_hex") else b""
            elif ub == "copy_rtsa":
                userdata = bytes.fromhex(
                    decoded.get("raw_hex", ""))[4:-2] if decoded.get("raw_hex") else b""
            elif ub == "clock":
                # 集中器当前时间：6B BCD 秒分时日月年（14H-F2 时钟应答）
                userdata = _now_bcd_time()
            else:
                userdata = r.get("userdata", b"")
        else:
            userdata = ub(decoded) if callable(ub) else r.get("userdata", b"")

        if isinstance(userdata, str):
            userdata = bytes.fromhex(userdata.replace(" ", "")) if userdata else b""
        elif not isinstance(userdata, bytes):
            userdata = bytes(userdata)

        return build_13762_frame(
            afn=afn, fn=fn, appdata=userdata,
            direction="down", info={"seq": seq} if info_bytes is not None else None,
            address=address,
        )


# ---------------------------------------------------------------------------
# 内置应答规则表（默认模板，覆盖常用 AFN）
# ---------------------------------------------------------------------------
_BUILTIN_RULES: List[dict] = [
    {
        "id": "builtin.local_ack",
        "match": {"afn": 0x00},
        "reply": {"afn": 0x00, "fn": 1, "format": "local",
                  "desc": "本地确认/否认帧 → 回 00H-F1 确认"},
    },
    {
        "id": "builtin.local_03F10_running",
        "match": {"afn": 0x03, "fn": 10},
        "reply": {"afn": 0x00, "fn": 1, "format": "local",
                  "desc": "CCO 上报 03H-F10 运行模式 → 回确认"},
    },
    {
        "id": "builtin.local_06F3_route",
        "match": {"afn": 0x06, "fn": 3},
        "reply": {"afn": 0x00, "fn": 1, "format": "local",
                  "desc": "CCO 上报 06H-F3 工况变动 → 回确认"},
    },
    {
        "id": "builtin.local_06F230_mclt_report",
        "match": {"afn": 0x06, "fn": 230},
        "reply": {"afn": 0x00, "fn": 1, "format": "local",
                  "desc": "CCO 上报 06H-F230 采集数据 → 回确认"},
    },
    {
        "id": "builtin.00F1_confirm",
        "match": {"afn": 0x00},
        "reply": {"afn": 0x00, "userdata_builder": "confirm",
                  "desc": "确认/否认帧 → 回确认"},
    },
    {
        "id": "builtin.01xx_init",
        "match": {"afn": 0x01},
        "reply": {"afn": 0x00, "userdata_builder": "confirm",
                  "desc": "初始化类(01H) → 回确认"},
    },
    {
        "id": "builtin.03xx_query",
        "match": {"afn": 0x03},
        "reply": {"afn": 0x03, "userdata_builder": "echo",
                  "desc": "查询类(03H) → 回 03H 并回显数据（示意）"},
    },
    {
        "id": "builtin.10xx_route",
        "match": {"afn": 0x10},
        "reply": {"afn": 0x10, "userdata_builder": "echo",
                  "desc": "路由查询类(10H) → 回 10H 并回显数据（示意）"},
    },
    {
        "id": "builtin.02xx_forward",
        "match": {"afn": 0x02},
        "reply": {"afn": 0x02, "userdata_builder": "echo",
                  "desc": "数据转发类(02H) → 回 02H 并回显（示意）"},
    },
    {
        "id": "builtin.14F2_clock",
        "match": {"afn": 0x14, "fn": 2},
        "reply": {"afn": 0x14, "fn": 2, "userdata_builder": "clock",
                  "desc": "路由请求集中器时钟(14H-F2) → 回 14H-F2 集中器当前时间（6B BCD）"},
    },
]
