"""双模 4-2 链路层/NWK 层适配器：统一解码入口 + parser_lib 协议适配器实现。

主入口 :func:`decode_frame` 接受 GW 侦听台封装帧（bytes/十六进制文本/日志行），
返回分层结构 :class:`DualMacFrame`；:class:`DualMacAdapter` 实现
parser_lib.core.adapter.ProtocolAdapter，供通用渲染/嗅探路由复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from parser_lib.core.adapter import DataField, ProtocolAdapter, ProtocolFrame

from . import beacon as beacon_mod
from . import events as events_mod
from . import mgmt as mgmt_mod
from .fch import Fch, parse_fch
from .gw import (DELIM_BEACON, DELIM_SACK, DELIM_SOF, GwFrame, parse_gw_frame,
                 split_gw_stream, strip_gw)
from .mac_header import MacHeader, Msdu, extract_msdu, parse_mac_header


@dataclass
class DualMacFrame:
    """一帧 GW 封装帧的完整分层解析结果。"""

    gw: GwFrame
    fch: Fch
    mac: Optional[MacHeader] = None
    msdu: Optional[Msdu] = None
    mgmt: Optional[mgmt_mod.MgmtMessage] = None
    beacon: Optional[beacon_mod.Beacon] = None
    events: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def nid_hex(self) -> str:
        return self.fch.nid_hex

    @property
    def delimiter_name(self) -> str:
        return self.fch.delimiter_name


def decode_frame(raw) -> DualMacFrame:
    """解码一帧。raw 可为：GW 封装 bytes / 含 7E 边界的 hex 文本 / 侦听台日志行。"""
    if isinstance(raw, str):
        stripped = split_gw_stream(raw)
        data = stripped if stripped is not None else strip_gw(raw)
    else:
        data = strip_gw(bytes(raw))
    gw = parse_gw_frame(data)
    fch = parse_fch(gw.fch)
    warnings = list(gw.warnings) + list(fch.warnings)
    out = DualMacFrame(gw=gw, fch=fch, warnings=warnings)

    if fch.delimiter == DELIM_SOF and gw.region:
        try:
            out.mac = parse_mac_header(gw.region)
            out.msdu = extract_msdu(gw.region, out.mac)
            if out.msdu.truncated:
                warnings.append("载荷区不完整（疑似网关截断长帧），MSDU 已按可用长度截取")
            if out.msdu.icv_ok is False:
                warnings.append("ICV 校验失败（MSDU crc32 不匹配，可能空口误码）")
            if out.mac.msdu_type == 0 and out.msdu.data and not out.msdu.truncated:
                out.mgmt = mgmt_mod.parse_mgmt(out.msdu.data)
                warnings.extend(out.mgmt.warnings)
        except (IndexError, ValueError) as exc:
            warnings.append(f"SOF 解析失败：{exc}")
    elif fch.delimiter == DELIM_BEACON and gw.region:
        try:
            out.beacon = beacon_mod.parse_beacon(gw.region)
            warnings.extend(out.beacon.warnings)
        except (IndexError, ValueError) as exc:
            warnings.append(f"信标解析失败：{exc}")

    out.events = events_mod.extract_events(gw, fch, out.mac, out.msdu, out.mgmt, out.beacon)
    return out


def _field(name, value, hex_="", desc="") -> DataField:
    return DataField(name=name, value=value, hex=hex_, desc=desc)


class DualMacAdapter(ProtocolAdapter):
    """parser_lib 协议适配器（用于通用渲染与嗅探）。"""

    protocol = "dualmac-42"

    def try_extract(self, buf: bytes) -> Optional[object]:
        head = bytes(buf[:3])
        if head == b"\x7e\xff\x02" and len(buf) >= 40:
            return None  # 由 GW 层切帧，此处不做消耗
        return None

    def confidence(self, raw: bytes) -> float:
        try:
            data = strip_gw(bytes(raw))
            fch = data[20:36]
        except Exception:
            return 0.0
        if len(data) < 36:
            return 0.0
        if (fch[0] & 0x07) <= 3 and (fch[0] >> 3) == 0 and fch[1:4] != b"\x00\x00\x00":
            return 0.9
        return 0.3

    def decode(self, raw: bytes) -> ProtocolFrame:
        parsed = decode_frame(bytes(raw))
        frame = ProtocolFrame(
            structure=f"GW双模空口帧/{parsed.delimiter_name}",
            address=parsed.fch.nid_hex,
            raw_hex=bytes(raw).hex().upper(),
            warnings=list(parsed.warnings),
        )
        frame.fields.extend([
            _field("定界符", parsed.fch.delimiter_name),
            _field("NID", parsed.fch.nid_hex),
            _field("网络类型", parsed.fch.nwk_type),
            _field("GW尾4B", parsed.gw.gw_tail.hex().upper()),
        ])
        var = parsed.fch.variable
        for key, value in var.items():
            frame.fields.append(_field(f"FCH.{key}", value))
        if parsed.mac is not None:
            frame.items.extend([
                _field("源TEI", parsed.mac.teis_text),
                _field("目的TEI", parsed.mac.teid_text),
                _field("发送类型", parsed.mac.send_type_name),
                _field("MSDU类型", parsed.mac.msdu_type_name),
                _field("MSDU序号", parsed.mac.msdu_sqn),
                _field("重启次数", parsed.mac.restart_times),
                _field("路由总/剩余跳数", f"{parsed.mac.hops}/{parsed.mac.remain_hops}"),
                _field("ICV校验", "通过" if parsed.msdu and parsed.msdu.icv_ok else
                       ("失败" if parsed.msdu and parsed.msdu.icv_ok is False else "未校验")),
            ])
        if parsed.mgmt is not None:
            frame.nested.append(ProtocolFrame(
                structure=f"网管消息/{parsed.mgmt.mm_name}",
                fields=[_field(k, v) for k, v in parsed.mgmt.fields.items()],
            ))
        if parsed.beacon is not None:
            item_frames = []
            for it in parsed.beacon.items:
                item_frames.append(ProtocolFrame(
                    structure=f"条目 {it.item_name}",
                    fields=[_field(k, v) for k, v in it.fields.items()],
                ))
            frame.nested.append(ProtocolFrame(
                structure=f"信标/{parsed.beacon.bcn_type_name}",
                fields=[
                    _field("CCO MAC", parsed.beacon.cco_mac_text),
                    _field("周期计数", parsed.beacon.cycle_count),
                    _field("允许关联", parsed.beacon.permit_assoc),
                    _field("BPCS", "通过" if parsed.beacon.bpcs_ok else "失败"),
                ],
                nested=item_frames,
            ))
        for ev in parsed.events:
            frame.items.append(_field(f"事件[{ev['name']}]", ev["summary"]))
        return frame
