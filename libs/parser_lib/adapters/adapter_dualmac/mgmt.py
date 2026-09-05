"""网络管理消息解析（双模 4-2 第 5.1.3 节管理消息帧，MSDU 类型=0）。

空中格式（经 reqs/0009 真机样本锚定核验）::

    [mmtype 2B LE] [保留 2B，实测恒 00 00] [表内容...]

表内容偏移出处：template/protocol/dll/src/frame/management_message.c +
inc/nwk/sta.h mmtype 枚举；表70 关联确认用真机帧中 CCO MAC 位置锚定
（staMAC@0/CCO_MAC@6/rslt@12/... 全部吻合，retryTime 实测 300001ms ≈
RETRY_ASSOC_TIME 300s 工程常量）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gw import le16, le24, le32, get_tei

# mmtype（sta.h:24-44）
MM_NAMES = {
    0x0000: "关联请求",
    0x0001: "关联确认",
    0x0002: "关联汇总指示",
    0x0003: "代理变更请求",
    0x0004: "代理变更确认",
    0x0005: "代理变更确认(位图)",
    0x0006: "离线指示",
    0x0007: "心跳检测",
    0x0008: "发现列表",
    0x0009: "通信成功率上报",
    0x000A: "网络冲突上报",
    0x000B: "过零NTB采集指示",
    0x000C: "过零NTB上报",
    0x004F: "网络诊断报文",
    0x0050: "路由请求",
    0x0051: "路由回复",
    0x0052: "路由错误",
    0x0053: "路由应答",
    0x0054: "链路确认请求",
    0x0055: "链路确认回应",
    0x0080: "无线信道冲突上报",
}

# 关联结果码（sta.h:46-63）
ASSOC_RSLT = {
    0x0: "成功", 0x1: "不在白名单", 0x2: "在黑名单", 0x3: "站点超上限",
    0x4: "未设白名单", 0x5: "代理超上限", 0x6: "子站超上限",
    0x8: "重复MAC", 0x9: "层次超限", 0xA: "再次关联成功",
    0xB: "以子站为代理", 0xC: "存在环路", 0xD: "未知原因",
    0xE: "RF代理超限", 0xF: "其他",
}
TERMINATER_TYPES = {
    1: "抄控器", 2: "集中器本地通信单元", 3: "电表通信单元", 4: "中继器",
    5: "II型采集器", 6: "I型采集器单元", 7: "三相电表通信单元",
}
MODULE_TYPES = {0: "HPLC单模", 1: "双模HPLC+RF", 2: "无线单模"}
LEAVE_REASONS = {0: "CCO通知立刻离线", 1: "拓扑层级超上限", 2: "不在最新白名单"}


@dataclass
class MgmtMessage:
    """管理消息解析结果。unknown 时仅给出 mmtype 与原始字节。"""

    mmtype: int = 0
    name: str = ""
    fields: dict = field(default_factory=dict)
    raw: bytes = b""
    warnings: list = field(default_factory=list)

    @property
    def mm_name(self) -> str:
        return self.name or MM_NAMES.get(self.mmtype, f"保留(0x{self.mmtype:04X})")


def parse_mgmt(msdu: bytes) -> MgmtMessage:
    """解析网管 MSDU（mmtype + 保留 2B + 表内容）。"""
    if len(msdu) < 4:
        raise ValueError("网管 MSDU 不足 4 字节")
    mmtype = le16(msdu, 0)
    out = MgmtMessage(mmtype=mmtype, name=MM_NAMES.get(mmtype, ""), raw=msdu)
    table = msdu[4:]
    handler = _HANDLERS.get(mmtype)
    if handler is None:
        out.fields = {"table_hex": table.hex().upper()}
        return out
    try:
        out.fields = handler(table)
    except (IndexError, ValueError) as exc:
        out.warnings.append(f"表解析失败：{exc}")
        out.fields = {"table_hex": table.hex().upper()}
    return out


def _pco_entry(b: bytes) -> dict:
    """2B 候选代理项：TEI(12bit GET_TEI 布局) + 链路类型(1b)。"""
    return {"tei": get_tei(b, 0), "link_type": "无线" if (b[1] >> 4) & 1 else "载波"}


def _table60(c: bytes) -> dict:
    """表60 关联请求。"""
    candidates = [_pco_entry(c[6 + i * 2: 8 + i * 2]) for i in range(5)]
    term = c[17]
    module = c[19] & 0x03
    return {
        "sta_mac": c[0:6].hex().upper(),
        "proxy_candidates": candidates,
        "phase": c[16] & 0x3F,
        "terminater_type": term,
        "terminater_name": TERMINATER_TYPES.get(term, f"保留({term})"),
        "mac_addr_type": "模块MAC" if c[18] else "电能表地址",
        "module_type": MODULE_TYPES.get(module, f"保留({module})"),
        "mclt_enable": bool((c[19] >> 2) & 1),
        "random": le32(c, 20),
    }


def _table70(c: bytes) -> dict:
    """表70 关联确认（含路由信息分段）。"""
    rslt = c[12]
    direct_sta_cnt = le16(c, 40)
    direct_pco_cnt = le16(c, 42)
    router_size = le16(c, 44)
    direct_stas = []
    for i in range(direct_sta_cnt):
        b = c[48 + i * 2: 50 + i * 2]
        direct_stas.append({"tei": get_tei(b, 0),
                            "link_type": "无线" if (b[1] >> 4) & 1 else "载波"})
    pco_base = 48 + direct_sta_cnt * 2
    direct_pcos = []
    pos = pco_base
    for _ in range(direct_pco_cnt):
        if pos + 4 > len(c):
            break
        sub_cnt = le16(c, pos + 2)
        pco = _pco_entry(c[pos:pos + 2])
        subs = []
        sub_pos = pos + 4
        for _j in range(sub_cnt):
            if sub_pos + 2 > len(c):
                break
            subs.append(get_tei(c, sub_pos))
            sub_pos += 2
        pco["sub_sta_cnt"] = sub_cnt
        pco["sub_stas"] = subs
        direct_pcos.append(pco)
        pos = sub_pos
    return {
        "sta_mac": c[0:6].hex().upper(),
        "cco_mac": c[6:12].hex().upper(),
        "rslt": rslt,
        "rslt_name": ASSOC_RSLT.get(rslt, f"保留({rslt:#x})"),
        "sta_layer": c[13],
        "sta_tei": get_tei(c, 14),
        "link_type": "无线" if (c[15] >> 4) & 1 else "载波",
        "hplc_band": (c[15] >> 5) & 0x03,
        "proxy_tei": get_tei(c, 16),
        "packet_size": c[18],
        "packet_no": c[19],
        "random": le32(c, 20),
        "retry_time_ms": le32(c, 24),
        "t2t_sqn": le32(c, 28),
        "path_sqn": le32(c, 32),
        "direct_sta_cnt": direct_sta_cnt,
        "direct_pco_cnt": direct_pco_cnt,
        "router_table_size": router_size,
        "direct_stas": direct_stas,
        "direct_pcos": direct_pcos,
    }


def _table76(c: bytes) -> dict:
    """表76 关联汇总指示（第一层 STA 批量入网）。"""
    sta_cnt = c[11]
    stas = []
    for i in range(sta_cnt):
        base = 16 + i * 8
        if base + 8 > len(c):
            break
        stas.append({"mac": c[base:base + 6].hex().upper(),
                     "tei": get_tei(c, base + 6)})
    return {
        "rslt": c[0],
        "sta_layer": c[1],
        "cco_mac": c[2:8].hex().upper(),
        "proxy_tei": get_tei(c, 8),
        "hplc_band": (c[9] >> 4) & 0x03,
        "sta_cnt": sta_cnt,
        "stas": stas,
    }


def _table79(c: bytes) -> dict:
    """表79 代理变更请求（5 个候选代理）。"""
    candidates = [_pco_entry(c[2 + i * 2: 4 + i * 2]) for i in range(5)]
    return {
        "sta_tei": get_tei(c, 0),
        "proxy_candidates": candidates,
        "old_proxy_tei": get_tei(c, 12),
        "proxy_type": c[14],
        "why_change": c[15],
        "t2t_sqn": le32(c, 16),
        "phase": c[20] & 0x3F if len(c) > 20 else None,
    }


def _table84(c: bytes) -> dict:
    """表84 代理变更确认（TEI 列表式）。"""
    sub_cnt = le16(c, 16)
    subs = [get_tei(c, 20 + i * 2) for i in range(sub_cnt) if 22 + i * 2 <= len(c)]
    return {
        "result": c[0],
        "packet_cnt": c[1],
        "packet_sqn": c[2],
        "sta_tei": get_tei(c, 4),
        "link_type": "无线" if (c[5] >> 4) & 1 else "载波",
        "proxy_tei": get_tei(c, 6),
        "t2t_sqn": le32(c, 8),
        "path_sqn": le32(c, 12),
        "sub_sta_cnt": sub_cnt,
        "sub_stas": subs,
    }


def _table88(c: bytes) -> dict:
    """表88 代理变更确认（128B 位图式）。"""
    bitmap_size = le16(c, 2)
    return {
        "result": c[0],
        "bitmap_size": bitmap_size,
        "sta_tei": get_tei(c, 4),
        "link_type": "无线" if (c[5] >> 4) & 1 else "载波",
        "proxy_tei": get_tei(c, 6),
        "t2t_sqn": le32(c, 8),
        "path_sqn": le32(c, 12),
        "bitmap_hex": c[20:20 + bitmap_size].hex().upper() if 20 + bitmap_size <= len(c) else "",
    }


def _table91(c: bytes) -> dict:
    """表91 离网指示。"""
    sta_cnt = le16(c, 2)
    macs = [c[16 + i * 6: 22 + i * 6].hex().upper()
            for i in range(sta_cnt) if 22 + i * 6 <= len(c)]
    reason = le16(c, 0)
    return {
        "reason": reason,
        "reason_name": LEAVE_REASONS.get(reason, f"保留({reason})"),
        "sta_cnt": sta_cnt,
        "delay_time_ms": le16(c, 4),
        "macs": macs,
    }


def _table94(c: bytes) -> dict:
    """表94 心跳检测（原发站 TEI + 子站活跃位图）。"""
    bitmap_size = le16(c, 6)
    return {
        "osa_tei": get_tei(c, 0),
        "most_discover_sta_tei": get_tei(c, 2),
        "most_discover_sta": le16(c, 4),
        "bitmap_size": bitmap_size,
        "bitmap_hex": c[8:8 + bitmap_size].hex().upper(),
        "active_cnt": _bitmap_popcount(c[8:8 + bitmap_size]),
    }


def _bitmap_popcount(bitmap: bytes) -> int:
    return sum(bin(b).count("1") for b in bitmap)


def _table100(c: bytes) -> dict:
    """表100 通信成功率上报。"""
    cnt = le16(c, 2)
    entries = []
    for i in range(cnt):
        base = 4 + i * 4
        if base + 4 > len(c):
            break
        entries.append({"tei": get_tei(c, base), "down": c[base + 2], "up": c[base + 3]})
    return {"proxy_tei": get_tei(c, 0), "sub_sta_cnt": cnt, "entries": entries}


def _table102(c: bytes) -> dict:
    """表102 网络冲突上报（NID 冲突）。"""
    cnt = c[6]
    width = c[7] or 3
    nids = [f"{le24(c, 8 + i * width):06X}" for i in range(cnt) if 11 + i * width <= len(c)]
    return {"cco_mac": c[0:6].hex().upper(), "neighbor_nwk_cnt": cnt,
            "nid_width": width, "neighbor_nids": nids}


def _table121(c: bytes) -> dict:
    """表121 无线信道冲突上报。"""
    cnt = c[6]
    chnls = [c[7 + i] for i in range(cnt) if 7 + i < len(c)]
    opts = [c[7 + cnt + i] & 0x03 for i in range(cnt) if 7 + cnt + i < len(c)]
    return {"cco_mac": c[0:6].hex().upper(), "neighbor_nwk_cnt": cnt,
            "chnls": chnls, "options": opts}


def _route_path_entries(c: bytes, count: int, base: int) -> list:
    entries = []
    for i in range(count):
        b = c[base + i * 4: base + i * 4 + 4]
        if len(b) < 4:
            break
        entries.append({"tei": get_tei(b, 0), "suc_rate": b[2], "lqi": b[3]})
    return entries


def _table111(c: bytes) -> dict:
    """表111 路由请求。"""
    length = c[6]
    return {
        "version": c[0],
        "sqn": le32(c, 1),
        "path_preference": bool((c[5] >> 3) & 1),
        "payload_type": (c[5] >> 4) & 0x0F,
        "payload_len": length,
        "paths": _route_path_entries(c, length // 4 if length else 0, 7)
        if (c[5] >> 4) & 0x0F == 1 else [],
    }


def _table114(c: bytes) -> dict:
    """表114 路由回复。"""
    length = c[6]
    return {
        "version": c[0],
        "sqn": le32(c, 1),
        "payload_len": length,
        "paths": _route_path_entries(c, (length - 7) // 4 if length > 7 else 0, 7),
    }


def _table117(c: bytes) -> dict:
    """表117 路由错误（≤15 个不可达 TEI）。"""
    cnt = c[6]
    teis = [get_tei(c, 7 + i * 2) for i in range(cnt) if 9 + i * 2 <= len(c)]
    return {"version": c[0], "sqn": le32(c, 1), "unreach_cnt": cnt, "unreach_teis": teis}


def _table118(c: bytes) -> dict:
    """表118 路由应答。"""
    return {"version": c[0], "sqn": le32(c, 4)}


_HANDLERS = {
    0x0000: _table60,
    0x0001: _table70,
    0x0002: _table76,
    0x0003: _table79,
    0x0004: _table84,
    0x0005: _table88,
    0x0006: _table91,
    0x0007: _table94,
    0x0009: _table100,
    0x000A: _table102,
    0x0050: _table111,
    0x0051: _table114,
    0x0052: _table117,
    0x0053: _table118,
    0x0080: _table121,
}
