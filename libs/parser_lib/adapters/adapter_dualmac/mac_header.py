"""MAC 帧头解析（双模 4-2 表4 标准 16B / 28B）与 MSDU/ICV 校验。

位域出处：template/protocol/dll/src/frame/mac_header.c（表4 getter/stuff），
TEIs 布局为"首字节高半字节=低 4bit、次字节=高 8bit"（与 FCH 可变区 GET_TEI
"低字节在前"不同，两套布局并存，经真机样本核验）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gw import le16, verify_icv

SEND_TYPE_NAMES = {0: "单播", 1: "全网广播", 2: "本地广播", 3: "代理广播"}
MSDU_TYPE_NAMES = {0: "网络管理消息", 48: "应用层报文", 49: "IP报文"}


@dataclass
class MacHeader:
    """表4 标准 MAC 帧头（16B；MAC 地址标志=1 时 28B）。"""

    version: int = 0
    teis: int = 0
    teid: int = 0
    send_type: int = 0
    send_times: int = 0
    msdu_sqn: int = 0
    msdu_type: int = 0
    msdu_len: int = 0
    restart_times: int = 0
    proxy_path_flag: bool = False
    hops: int = 0
    remain_hops: int = 0
    broadcast_dir: int = 0
    path_repair: bool = False
    macaddr_flag: bool = False
    net_form_no: int = 0
    os_mac: bytes = b""
    od_mac: bytes = b""
    header_len: int = 16
    warnings: list = field(default_factory=list)

    @property
    def send_type_name(self) -> str:
        return SEND_TYPE_NAMES.get(self.send_type, f"保留({self.send_type})")

    @property
    def msdu_type_name(self) -> str:
        return MSDU_TYPE_NAMES.get(self.msdu_type, f"保留({self.msdu_type})")

    @property
    def teis_text(self) -> str:
        return f"{self.teis:03X}"

    @property
    def teid_text(self) -> str:
        return f"{self.teid:03X}"


def parse_mac_header(region: bytes) -> MacHeader:
    """从载荷区起点解析表4；region 至少 16 字节。"""
    if len(region) < 16:
        raise ValueError("载荷区不足 16 字节，无法解析 MAC 帧头")
    h = region
    out = MacHeader(
        version=h[0] & 0x0F,
        teis=(h[0] >> 4) | (h[1] << 4),
        teid=h[2] | ((h[3] & 0x0F) << 8),
        send_type=(h[3] >> 4) & 0x0F,
        send_times=h[4] & 0x1F,
        msdu_sqn=le16(h, 5),
        msdu_type=h[7],
        msdu_len=((h[9] & 0x07) << 8) | h[8],
        restart_times=(h[9] >> 3) & 0x0F,
        proxy_path_flag=bool(h[9] & 0x80),
        hops=h[10] & 0x0F,
        remain_hops=(h[10] >> 4) & 0x0F,
        broadcast_dir=h[11] & 0x03,
        path_repair=bool((h[11] >> 2) & 1),
        macaddr_flag=bool((h[11] >> 3) & 1),
        net_form_no=h[13],
        header_len=28 if (h[11] & 0x08) else 16,
    )
    if out.version == 1:
        out.warnings.append("单跳帧（版本1）头布局不同，本解析器按标准帧处理")
    if out.macaddr_flag:
        if len(h) >= 28:
            out.os_mac = h[16:22]
            out.od_mac = h[22:28]
        else:
            out.warnings.append("MAC 地址标志=1 但载荷区不足 28B")
    return out


@dataclass
class Msdu:
    """MSDU + ICV 校验结果。"""

    data: bytes = b""
    truncated: bool = False
    icv_ok: bool | None = None     # None=载荷区不含完整 ICV（截断）
    icv: bytes = b""


def extract_msdu(region: bytes, header: MacHeader) -> Msdu:
    """按 MAC 头长度域切 MSDU 并校验 ICV（紧跟 MSDU 之后 4B 小端 crc32）。"""
    start = header.header_len
    end = start + header.msdu_len
    available = len(region)
    if end + 4 <= available:
        data = region[start:end]
        icv = region[end:end + 4]
        return Msdu(data=data, icv=icv, icv_ok=verify_icv(data, icv))
    if end <= available:
        return Msdu(data=region[start:end], truncated=False, icv_ok=None)
    data = region[start:available]
    return Msdu(data=data, truncated=True, icv_ok=None)
