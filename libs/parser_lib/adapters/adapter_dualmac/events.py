"""组网事件归一：把解析结果抽成扁平事件流（REQS-0024 G2 输出契约）。

事件字典结构（落库与前端共用）::

    {
      "event": "heartbeat",            # 事件类型键（EVENT_KEYS 之一）
      "name": "心跳检测",               # 中文名
      "direction": "up|down|mesh",     # up→CCO 方向 / down←CCO / mesh 中继间
      "src_tei": "035", "dst_tei": "001",
      "summary": "人类可读摘要",
      "fields": {...},                 # 解析细节（JSON 可序列化）
    }
"""
from __future__ import annotations

from typing import Optional

from . import beacon as beacon_mod
from . import mgmt as mgmt_mod
from .fch import Fch
from .gw import GwFrame, DELIM_BEACON, DELIM_COORD, DELIM_SOF
from .mac_header import MacHeader, Msdu

CCO_TEI = 1

EVENT_NAMES = {
    "beacon_central": "中央信标",
    "beacon_proxy": "代理信标",
    "beacon_discover": "发现信标",
    "assoc_req": "关联请求",
    "assoc_cnf": "关联确认",
    "assoc_gather": "关联汇总入网",
    "proxy_change_req": "代理变更请求",
    "proxy_change_cnf": "代理变更确认",
    "leave_ind": "离网指示",
    "heartbeat": "心跳检测",
    "discover_list": "发现列表",
    "success_rate": "成功率上报",
    "nid_conflict": "NID冲突上报",
    "rf_conflict": "无线信道冲突上报",
    "route_request": "路由请求",
    "route_reply": "路由回复",
    "route_error": "路由错误",
    "route_ack": "路由应答",
    "coord_frame": "网间协调帧",
    "app_data": "1376.2业务透传",
}

# 事件归属分组（前端过滤器）
EVENT_GROUPS = {
    "组网": ["assoc_req", "assoc_cnf", "assoc_gather", "proxy_change_req",
             "proxy_change_cnf", "leave_ind"],
    "维护": ["heartbeat", "discover_list", "success_rate"],
    "冲突": ["nid_conflict", "rf_conflict", "coord_frame"],
    "路由": ["route_request", "route_reply", "route_error", "route_ack"],
    "信标": ["beacon_central", "beacon_proxy", "beacon_discover"],
    "业务": ["app_data"],
}


def _dir(src: int, dst: int) -> str:
    if src == CCO_TEI:
        return "down"
    if dst == CCO_TEI:
        return "up"
    return "mesh"


def _base(event: str, src: int, dst: int, fields: dict, summary: str) -> dict:
    return {
        "event": event,
        "name": EVENT_NAMES.get(event, event),
        "direction": _dir(src, dst),
        "src_tei": f"{src:03X}",
        "dst_tei": f"{dst:03X}",
        "summary": summary,
        "fields": fields,
    }


def extract_events(gw: GwFrame, fch: Fch,
                   mac: Optional[MacHeader] = None,
                   msdu: Optional[Msdu] = None,
                   mgmt: Optional[mgmt_mod.MgmtMessage] = None,
                   beacon: Optional[beacon_mod.Beacon] = None) -> list:
    """从一帧解析结果提取 0~N 个组网事件。"""
    events: list = []
    if fch.delimiter == DELIM_BEACON and beacon is not None:
        kind = {0: "beacon_discover", 1: "beacon_proxy", 2: "beacon_central"}.get(
            beacon.bcn_type, "beacon_discover")
        src = fch.variable.get("teis", 0)
        fields = {
            "cco_mac": beacon.cco_mac_text,
            "cycle_count": beacon.cycle_count,
            "permit_assoc": beacon.permit_assoc,
            "networking_stop": beacon.networking_stop,
            "items": [it.name for it in beacon.items],
            "bpcs_ok": beacon.bpcs_ok,
        }
        if beacon.schedule:
            p = beacon.schedule.get("periods_ms", {})
            fields["beacon_period_ms"] = beacon.schedule.get("beacon_period_ms")
            fields["periods_ms"] = p
            fields["csma_slots"] = beacon.schedule.get("csma_slots")
        events.append(_base(kind, src, 0, fields,
                            f"{beacon.bcn_type_name} 周期#{beacon.cycle_count}"
                            f"{' 允许关联' if beacon.permit_assoc else ''}"))
        return events

    if fch.delimiter == DELIM_COORD:
        var = fch.variable
        events.append(_base("coord_frame", 0, 0, dict(var),
                            f"带宽宣告 {var.get('duration_ms')}ms"
                            f" 邻居NID {var.get('neighbor_nid', 0):06X}"))
        return events

    if fch.delimiter != DELIM_SOF or mac is None:
        return events

    src, dst = mac.teis, mac.teid

    if mac.msdu_type == 48:
        events.append(_base(
            "app_data", src, dst,
            {"msdu_sqn": mac.msdu_sqn, "msdu_len": mac.msdu_len,
             "send_type": mac.send_type_name, "icv_ok": msdu.icv_ok if msdu else None},
            f"业务透传 序号#{mac.msdu_sqn} {mac.msdu_len}B"))
        return events

    if mac.msdu_type != 0 or mgmt is None:
        return events

    f = mgmt.fields
    mm = mgmt.mmtype
    if mm == 0x0000:
        cand = ",".join(f"{c['tei']:03X}({c['link_type']})" for c in f.get("proxy_candidates", []))
        events.append(_base("assoc_req", src, dst, f,
                            f"站点 {f.get('sta_mac', '')} 申请入网 候选代理[{cand}]"))
    elif mm == 0x0001:
        extra = ""
        if f.get("rslt") not in (0, 0xA):
            extra = f" 退避{f.get('retry_time_ms')}ms"
        events.append(_base("assoc_cnf", src, dst, f,
                            f"确认 {f.get('sta_tei'):03X} 结果[{f.get('rslt_name')}]"
                            f" 代理[{f.get('proxy_tei', 0):03X}]{extra}"))
    elif mm == 0x0002:
        events.append(_base("assoc_gather", src, dst, f,
                            f"汇总 {f.get('sta_cnt')} 站经代理 {f.get('proxy_tei', 0):03X} 入网"))
    elif mm in (0x0003,):
        events.append(_base("proxy_change_req", src, dst, f,
                            f"站点 {f.get('sta_tei', 0):03X} 申请变更代理"
                            f"（原 {f.get('old_proxy_tei', 0):03X}）"))
    elif mm in (0x0004, 0x0005):
        events.append(_base("proxy_change_cnf", src, dst, f,
                            f"确认 {f.get('sta_tei', 0):03X} → 新代理 {f.get('proxy_tei', 0):03X}"
                            f" 结果[{f.get('result')}]" + (
                                f" 子站{f.get('sub_sta_cnt')}" if "sub_sta_cnt" in f else "")))
    elif mm == 0x0006:
        events.append(_base("leave_ind", src, dst, f,
                            f"离网 {f.get('sta_cnt')} 站 原因[{f.get('reason_name')}]"
                            f" 延迟{f.get('delay_time_ms')}ms"))
    elif mm == 0x0007:
        events.append(_base("heartbeat", src, dst, f,
                            f"心跳 来自 {f.get('osa_tei', 0):03X}"
                            f" 活跃 {f.get('active_cnt')} 站"
                            f"（位图 {f.get('bitmap_size')}B）"))
    elif mm == 0x0008:
        events.append(_base("discover_list", src, dst, f, "发现列表上报"))
    elif mm == 0x0009:
        events.append(_base("success_rate", src, dst, f,
                            f"成功率上报 代理 {f.get('proxy_tei', 0):03X}"
                            f" {f.get('sub_sta_cnt')} 子站"))
    elif mm == 0x000A:
        events.append(_base("nid_conflict", src, dst, f,
                            f"NID冲突 邻居 {f.get('neighbor_nids')}"))
    elif mm == 0x0080:
        events.append(_base("rf_conflict", src, dst, f,
                            f"信道冲突 chnl={f.get('chnls')} option={f.get('options')}"))
    elif mm == 0x0050:
        events.append(_base("route_request", src, dst, f,
                            f"路由请求 sqn={f.get('sqn')}"))
    elif mm == 0x0051:
        events.append(_base("route_reply", src, dst, f,
                            f"路由回复 sqn={f.get('sqn')}"))
    elif mm == 0x0052:
        events.append(_base("route_error", src, dst, f,
                            f"路由错误 不可达 {f.get('unreach_teis')}"))
    elif mm == 0x0053:
        events.append(_base("route_ack", src, dst, f,
                            f"路由应答 sqn={f.get('sqn')}"))
    else:
        events.append(_base("app_data", src, dst,
                            {"mmtype": f"0x{mm:04X}", **f},
                            f"管理消息 0x{mm:04X}"))
    return events
