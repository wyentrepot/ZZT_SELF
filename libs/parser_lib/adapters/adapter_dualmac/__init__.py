"""双模 4-2 链路层 / NWK 组网层适配器（REQS-0024）。

覆盖：
- GW 侦听台封装帧剥离（gw.py，真机样本实证格式）
- FCH 帧控制域：表13 + 表17/19/23/26/27/30/34（fch.py）
- MAC 帧头表4 + MSDU/ICV（mac_header.py）
- 信标：表38 + 条目47/48/49/50/54/55/57/J1 + BPCS + 四时段时隙重建（beacon.py）
- 网络管理消息：mmtype 0x0000~0x0080 全量事件表（mgmt.py）
- 组网事件归一（events.py）

位域出处：CCO 工程蒸馏库 template 快照 src/frame/*.c + inc/nwk/sta.h；
全部关键偏移经 reqs/0009 真机样本 2276 帧实证（详见各模块 docstring）。
"""
from .adapter import DualMacAdapter, DualMacFrame, decode_frame
from .beacon import Beacon, BeaconItem, parse_beacon, rebuild_schedule
from .events import EVENT_GROUPS, EVENT_NAMES, extract_events
from .fch import Fch, parse_fch
from .gw import GwFrame, parse_gw_frame, split_gw_stream, strip_gw
from .mac_header import MacHeader, Msdu, extract_msdu, parse_mac_header
from .mgmt import MM_NAMES, MgmtMessage, ASSOC_RSLT, parse_mgmt

__all__ = [
    "DualMacAdapter", "DualMacFrame", "decode_frame",
    "Beacon", "BeaconItem", "parse_beacon", "rebuild_schedule",
    "EVENT_GROUPS", "EVENT_NAMES", "extract_events",
    "Fch", "parse_fch",
    "GwFrame", "parse_gw_frame", "split_gw_stream", "strip_gw",
    "MacHeader", "Msdu", "extract_msdu", "parse_mac_header",
    "MM_NAMES", "MgmtMessage", "ASSOC_RSLT", "parse_mgmt",
]
