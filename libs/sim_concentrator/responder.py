"""应答引擎：模块上行帧 → 模拟集中器自动应答下行帧。

内置应答规则表（默认模板）覆盖常见 AFN；验证任务可传入自定义应答模板
（按上行帧特征匹配）覆盖内置默认。规则结构：

    {
      "id": "reply.01F1_confirm",
      "match": {"afn": 1},                 # 按 AFN 匹配（0x01=初始化）
      "reply": {"afn": 0x00, "ctrl": "confirm"},  # 应答：确认帧
    }

reply 字段说明（构造应答帧的参数）：
- afn:    应答帧的 AFN（如 0x00 确认、0x03 查询数据返回）
- userdata_builder: "confirm" | "deny" | "echo" | "copy_rtsa" 或 callable
- seq:    可选，缺省沿用上行帧 seq
- userdata: 可选，直接给定字节

匹配逻辑：
- 上行帧先 decode 得到信封字段；
- 依次遍历规则，取第一个 match 命中的；
- 用例提供的覆盖规则优先于内置表。
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from sim_concentrator.frame_codec import build_13762_frame, decode_frame


def _norm_afn(v) -> Optional[int]:
    """把 AFN 表示归一化为 int（支持 0x01 / 1 / '01' / '0x01'）。"""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 16)
    return None


class ReplyRule:
    def __init__(self, rule: dict):
        self.id = rule.get("id", "reply.custom")
        self.match = rule.get("match", {})  # {"afn": int, ...}
        self.reply = rule.get("reply", {})

    def matches(self, decoded: dict) -> bool:
        m = self.match
        afn = m.get("afn")
        if afn is not None:
            expect = _norm_afn(afn)
            # 信封 AFN 从 fields.AFN.raw 或 fields.AFN.value 提取
            afn_field = decoded.get("fields", {}).get("AFN", {})
            got = afn_field.get("raw")
            if isinstance(got, int):
                if got != expect:
                    return False
            else:
                # 兜底：从 value 文本 "0x01 (初始化)" 提取
                value = str(afn_field.get("value", ""))
                if f"0x{expect:02X}" not in value.upper().replace("0x", "0X"):
                    return False
        return True


class Responder:
    """应答引擎：内置规则表 + 用例覆盖规则。"""

    def __init__(self, override_rules: Optional[List[dict]] = None):
        self._overrides = [ReplyRule(r) for r in (override_rules or [])]
        self._builtin = [ReplyRule(r) for r in _BUILTIN_RULES]

    def list_rules(self) -> List[dict]:
        """返回当前生效规则（覆盖 + 内置），供 API 展示。"""
        out = []
        for r in self._overrides + self._builtin:
            out.append({"id": r.id, "match": r.match, "reply": r.reply})
        return out

    # -- 主入口 ---------------------------------------------------------
    def reply_for(self, raw: bytes, seq_override: Optional[int] = None) -> Optional[bytes]:
        """对上行帧构造应答帧；无匹配规则返回 None（不应答）。"""
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
        # 目标地址：上行帧的 RTUA（应答发回源），展示值反转为线上字节
        rtsa_show = decoded.get("fields", {}).get("终端地址RTUA", {}).get("value", "")
        try:
            rtsa_bytes = bytes.fromhex(rtsa_show)[::-1] if rtsa_show else bytes(6)
        except ValueError:
            rtsa_bytes = bytes(6)

        seq = seq_override
        if seq is None:
            seq = decoded.get("fields", {}).get("SEQ", {}).get("raw", 0)
            if not isinstance(seq, int):
                seq = 0

        afn = _norm_afn(r.get("afn", 0x00))
        msaa = _norm_afn(r.get("msaa", 0x01)) or 0x01
        pw = r.get("pw", 0x0000)
        if isinstance(pw, str):
            pw = int(pw, 16)

        ub = r.get("userdata_builder", "none")
        if isinstance(ub, str):
            if ub == "confirm":
                userdata = b"\x00"
            elif ub == "deny":
                userdata = b"\x01"  # 否认（示意：错误原因 01）
            elif ub == "echo":
                # 回显上行用户数据（便于调试/自检）
                userdata = bytes.fromhex(
                    decoded.get("raw_hex", ""))[15:-2] if decoded.get("raw_hex") else b""
            elif ub == "copy_rtsa":
                userdata = bytes.fromhex(rtsa_show)
            else:
                userdata = r.get("userdata", b"")
        else:
            userdata = ub(decoded) if callable(ub) else r.get("userdata", b"")

        if isinstance(userdata, str):
            userdata = bytes.fromhex(userdata.replace(" ", "")) if userdata else b""
        elif not isinstance(userdata, bytes):
            userdata = bytes(userdata)

        return build_13762_frame(afn=afn, seq=seq, rtsa=rtsa_bytes,
                                 msaa=msaa, pw=pw, userdata=userdata)


# ---------------------------------------------------------------------------
# 内置应答规则表（默认模板，覆盖常用 AFN）
# ---------------------------------------------------------------------------
_BUILTIN_RULES: List[dict] = [
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
]
