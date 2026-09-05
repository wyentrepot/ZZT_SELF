"""信标帧解析（双模 4-2 表38 + 条目 47/48/49/50/54/55/57/J1）与四时段时隙重建。

位域出处：template/protocol/dll/src/frame/beacon.c（与参考工程逐字节一致），
表38 头/周期计数经 network_assessment 真机验证，BPCS CRC32 59/59 全对。

条目长度域约定（beacon.c item_length）：条目头 1B + 长度域（条目号<0xC0 为
1B，否则 2B LE），长度域值**包含条目头与长度域自身**；内容 = 长度-1-长度域宽。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .gw import le16, le24, le32, get_tei, verify_bpcs

ITEM_NAMES = {
    0x00: "站点能力",
    0x01: "路由参数",
    0x02: "频段变更",
    0x03: "无线路由参数",
    0x04: "无线信道变更",
    0x05: "精简信标站点信息及时隙",
    0x06: "万年历同步",
    0xC0: "时隙分配",
}
BCN_TYPE_NAMES = {0: "发现信标", 1: "代理信标", 2: "中央信标"}
ROLE_NAMES = {0: "未知", 1: "STA", 2: "PCO", 4: "CCO"}
# 信标周期上限 15000ms（beacon.c table50_beaconPerioo 钳位）
BEACON_PERIOD_CAP_MS = 15000


@dataclass
class BeaconItem:
    item_id: int = 0
    name: str = ""
    raw: bytes = b""
    fields: dict = field(default_factory=dict)

    @property
    def item_name(self) -> str:
        return self.name


@dataclass
class Beacon:
    """信标载荷解析结果（不含 FCH/BPCS）。"""

    bcn_type: int = 0
    networking_stop: bool = False
    reduce_beacon: bool = False
    permit_assoc: bool = False
    use_beacon_frame: bool = False
    net_form_no: int = 0
    cco_mac: bytes = b""
    cycle_count: int = 0
    rf_chnl: int = 0
    rf_option: int = 0
    items: list = field(default_factory=list)
    bpcs_ok: bool | None = None
    schedule: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def bcn_type_name(self) -> str:
        return BCN_TYPE_NAMES.get(self.bcn_type, f"保留({self.bcn_type})")

    @property
    def cco_mac_text(self) -> str:
        return self.cco_mac.hex().upper() if self.cco_mac else ""

    def item(self, item_id: int) -> Optional[BeaconItem]:
        for it in self.items:
            if it.item_id == item_id:
                return it
        return None


def parse_beacon(content: bytes, verify_crc: bool = True) -> Beacon:
    """解析信标内容（不含 BPCS 与 GW 层）；content 为载荷区去 BPCS 前的全部字节。"""
    if len(content) < 21:
        raise ValueError("信标内容不足 21 字节（表38 头）")
    out = Beacon(
        bcn_type=content[0] & 0x07,
        networking_stop=bool((content[0] >> 3) & 1),
        reduce_beacon=bool((content[0] >> 4) & 1),
        permit_assoc=bool((content[0] >> 6) & 1),
        use_beacon_frame=bool((content[0] >> 7) & 1),
        net_form_no=content[1],
        cco_mac=content[2:8],
        cycle_count=le32(content, 8),
        rf_chnl=content[12] if len(content) > 12 else 0,
        rf_option=(content[13] & 0x03) if len(content) > 13 else 0,
    )
    item_cnt = content[20]
    offset = 21
    total = len(content)
    for _ in range(item_cnt):
        if offset >= total:
            out.warnings.append("条目数超出载荷长度，提前结束")
            break
        item_id = content[offset]
        size = 2 if item_id >= 0xC0 else 1
        if offset + size >= total:
            out.warnings.append("条目长度域越界")
            break
        length = le16(content, offset + 1) if size == 2 else content[offset + 1]
        if length < 1 + size or offset + length > total:
            out.warnings.append(f"条目 0x{item_id:02X} 长度非法（{length}）")
            break
        body = content[offset + 1 + size: offset + length]
        item = BeaconItem(
            item_id=item_id,
            name=ITEM_NAMES.get(item_id, f"保留(0x{item_id:02X})"),
            raw=body,
        )
        try:
            item.fields = _parse_item_fields(item_id, body, out.bcn_type)
        except (IndexError, ValueError) as exc:
            out.warnings.append(f"条目 0x{item_id:02X} 解析失败：{exc}")
        out.items.append(item)
        offset += length
    if verify_crc:
        # 调用方传入的 content 应含尾部 BPCS4（载荷区即 内容+填充+BPCS）
        if len(content) >= 4:
            out.bpcs_ok = verify_bpcs(content[:-4], content[-4:])
    slot = out.item(0xC0)
    if slot is not None:
        try:
            out.schedule = rebuild_schedule(slot.raw, out.bcn_type)
        except (IndexError, ValueError) as exc:
            out.warnings.append(f"时隙重建失败：{exc}")
    return out


def _parse_item_fields(item_id: int, c: bytes, bcn_type: int) -> dict:
    """按条目号解析内容字段（c 不含条目头与长度域）。"""
    if item_id == 0x00:
        return _item47(c)
    if item_id == 0x01:
        return _item48(c)
    if item_id == 0x02:
        return {"band": c[0], "cutover_remain_ms": le32(c, 1)}
    if item_id == 0x03:
        return {"discover_list_period_s": c[0], "recv_rate_ageing_cycle": c[1]}
    if item_id == 0x04:
        return {"object_chnl": c[0], "cutover_remain_ms": le32(c, 1), "object_option": c[5] & 0x03}
    if item_id == 0x05:
        return _item57(c)
    if item_id == 0x06:
        return {"calendar_secs": le32(c, 0), "calendar_ntb": le32(c, 4)}
    if item_id == 0xC0:
        return _item50_head(c)
    return {}


def _item47(c: bytes) -> dict:
    """站点能力条目（内容 13B）：角色/层次/相位/RF 跳数/最低路径成功率。"""
    return {
        "sta_tei": get_tei(c, 0),
        "proxy_tei": ((c[1] >> 4) & 0x0F) | (c[2] << 4),
        "path_lowest_suc": c[3],
        "sta_mac": c[4:10].hex().upper(),
        "role": c[10] & 0x0F,
        "role_name": ROLE_NAMES.get(c[10] & 0x0F, f"保留({c[10] & 0x0F})"),
        "layer": (c[10] >> 4) & 0x0F,
        "pco_lqi": c[11],
        "sta_phase": c[12] & 0x03,
        "rf_hop_cnt": (c[12] >> 2) & 0x0F,
    }


def _item48(c: bytes) -> dict:
    """路由参数通知条目（内容 8B，单位秒）。"""
    return {
        "router_period_s": le16(c, 0),
        "router_evaluate_remain_s": le16(c, 2),
        "pco_discover_list_period_s": le16(c, 4),
        "sta_discover_list_period_s": le16(c, 6),
    }


def _item57(c: bytes) -> dict:
    """精简信标站点信息及时隙条目（内容 17B）。"""
    return {
        "sta_tei": get_tei(c, 0),
        "proxy_tei": ((c[1] >> 4) & 0x0F) | (c[2] << 4),
        "role": c[3] & 0x0F,
        "layer": (c[3] >> 4) & 0x0F,
        "sta_mac": c[4:10].hex().upper(),
        "rf_hop_cnt": c[10] & 0x0F,
        "csma_start_ntb": le32(c, 11),
        "csma_length_ms": le16(c, 15),
    }


def _item50_head(c: bytes) -> dict:
    """时隙分配条目固定头 20B。"""
    return {
        "no_central_slots": c[0],
        "central_slots": c[1] & 0x0F,
        "csma_phase_cnt": (c[1] >> 4) & 0x03,
        "proxy_slots": c[3],
        "beacon_slot_length_ms": c[4],
        "csma_cut_up_length_10ms": c[5],
        "bind_csma_phase_cnt": c[6],
        "bind_csma_lid": c[7],
        "tdma_slot_length_ms": c[8],
        "tdma_lid": c[9],
        "originate_ntb": le32(c, 10),
        "beacon_period_ms": min(le32(c, 14), BEACON_PERIOD_CAP_MS),
        "rf_beacon_slot_length_ms": c[18] | ((c[19] & 0x03) << 8),
    }


def _item50_no_central(c: bytes, count: int) -> list:
    """非中央信标时隙表：每项 2B = TEI(12bit) + 信标类型(1b) + 无线标识(3b)。"""
    result = []
    for i in range(count):
        b = c[20 + i * 2: 22 + i * 2]
        result.append({
            "tei": get_tei(b, 0),
            "beacon_type": "代理信标" if (b[1] >> 4) & 1 else "发现信标",
            "rf_beacon_flag": (b[1] >> 5) & 0x07,
        })
    return result


def rebuild_schedule(c: bytes, bcn_type: int) -> dict:
    """由表50 重建信标/TDMA/CSMA/绑定CSMA 四时段起止（单位 ms，相对信标周期起始）。

    依据 media_access_task.c RecvBcnInd：信标区=(非中央+中央时隙)×信标时隙长；
    TDMA 区紧随；CSMA 区=各相长度之和（提前 2ms 结束）；绑定 CSMA 接续 CSMA。
    """
    head = _item50_head(c)
    no_central = head["no_central_slots"]
    phase_cnt = head["csma_phase_cnt"]
    bind_cnt = head["bind_csma_phase_cnt"]
    csma_base = 20 + no_central * 2

    csma_slots = []
    for i in range(phase_cnt):
        b = c[csma_base + i * 4: csma_base + i * 4 + 4]
        csma_slots.append({"length_ms": le24(b, 0), "phase": b[3] & 0x03})
    bind_base = csma_base + phase_cnt * 4
    bind_slots = []
    for i in range(bind_cnt):
        b = c[bind_base + i * 4: bind_base + i * 4 + 4]
        bind_slots.append({"length_ms": le24(b, 0), "phase": b[3] & 0x03})

    beacon_t = (no_central + head["central_slots"]) * head["beacon_slot_length_ms"]
    tdma_t = head["tdma_slot_length_ms"] * (3 if bcn_type == 2 else 1)
    csma_start = beacon_t + tdma_t
    csma_total = sum(s["length_ms"] for s in csma_slots)
    csma_total = max(csma_total - 2, 0)  # 提前 2ms 结束（工程调整）
    bind_start = csma_start + csma_total
    bind_total = sum(s["length_ms"] for s in bind_slots)

    return {
        **head,
        "no_central_list": _item50_no_central(c, no_central) if bcn_type != 0 else [],
        "csma_slots": csma_slots,
        "bind_csma_slots": bind_slots,
        "periods_ms": {
            "beacon": {"start": 0, "end": beacon_t},
            "tdma": {"start": beacon_t, "end": csma_start},
            "csma": {"start": csma_start, "end": csma_start + csma_total},
            "bind_csma": {"start": bind_start, "end": bind_start + bind_total},
        },
    }
