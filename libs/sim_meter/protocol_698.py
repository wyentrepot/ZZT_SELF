"""DL/T 698.45 从站侧（模拟电表）协议编解码。

只覆盖 STA 即装即采需要的最小口径，结构对齐 STA 源码 /home/H_STA/01/sta：
- 即装即采读设备描述符：SECURITY-Request(明文) 包 GET-Request(43000200)，
  应答 SECURITY-Response(明文) 包 GET-Response(85 01 PIID 43 00 02 00 01 0A N 描述符)。
- 分钟采集读任务点：SECURITY-Request(明文) 包 GET-Request WithList
  （05 02 PIID num OAD×num TimeTag，20261103 台架抓帧实证），
  应答 SECURITY-Response(明文) 包 GET-Response WithList
  （85 02 PIID num OAD 01 data…，条目布局对齐 mclt_698.OadItem：
  OAD(4 大端) + 0x01(响应选择=数据) + data）。
- 电表主动事件上报：REPORT-Notification list（88 01 PIID 01 OAD DATA）。

链路层：68 | L(2B 小端) | C(1B) | SA(0x05+6B) | CA(1B) | HCS(2) | APDU | FCS(2) | 16，
帧校验复用 parser_lib.adapters.adapter_698.build_frame / _check_fcs（PPP FCS-16）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from parser_lib.adapters.adapter_698 import _check_fcs, build_frame

FE_PREAMBLE = b"\xFE\xFE\xFE\xFE"
JZJC_OAD = bytes([0x43, 0x00, 0x02, 0x00])          # 43000200 设备描述符

# APDU 服务类型（对齐 DL/T 698.45 §6.3.4）
APDU_SECURITY_REQUEST = 0x10
APDU_SECURITY_RESPONSE = 0x90
APDU_GET_REQUEST = 0x05
APDU_GET_RESPONSE = 0x85
APDU_REPORT_NOTIFICATION = 0x88

# 链路控制域（bit7=DIR，bit6=PRM，bit3-0=功能码，用户数据=3）
CTRL_CLIENT_REQUEST = 0x43     # 客户机发出 / 启动站（DIR=0, PRM=1）
CTRL_SERVER_RESPONSE = 0xC3    # 服务器响应（DIR=1, PRM=1）
# 服务器主动上报：STA procedure_server_report_698_frame 要求 dir_bit==1 且
# prm_bit==0（dlt698_45.c:3693），STA 自身 send_report_notification_list 也以
# prm=0,dir=1 组帧（aps_fujian.c:620 packet_698_header(...,0,1)）→ 控制域 0x83。
CTRL_SERVER_REPORT = 0x83


@dataclass
class Parsed698:
    """一帧 698.45 的解析结果（raw 为去 FE 前导后的 68..16 帧）。"""
    raw: bytes
    control: int
    addr: bytes                       # 完整 SA 域（含长度/类型字节，如 05+6B）
    ca: int
    apdu: bytes                       # 外层 APDU
    inner_apdu: Optional[bytes] = None  # 安全帧剥出的内层 APDU，非安全帧为 None
    security: Optional[int] = None    # 外层安全 APDU 标签 0x10/0x90，非安全帧为 None
    service: Optional[int] = None     # 有效服务标签（安全帧取内层）
    piid: Optional[int] = None
    oad: Optional[bytes] = None       # GET-Request-Normal 单 OAD（WithList 为 None）
    oads: Optional[tuple[bytes, ...]] = None  # GET-Request WithList 的 OAD 列表
    request_type: Optional[int] = None  # GET 请求类型 / REPORT 子类型
    list_num: Optional[int] = None      # WithList/REPORT-Notification 条目数
    data: Optional[bytes] = None        # REPORT-Notification 条目数据


def _strip_fe(data: bytes) -> bytes:
    i = 0
    while i < len(data) and data[i] == 0xFE:
        i += 1
    return data[i:]


def _read_ber_len(apdu: bytes, pos: int) -> tuple[int, int]:
    """读 BER 短/长形式长度，返回 (长度值, 消费字节数)。"""
    if pos >= len(apdu):
        raise ValueError("SECURITY 长度编码不完整")
    first = apdu[pos]
    if first < 0x80:
        return first, 1
    if first == 0x81:
        if pos + 1 >= len(apdu):
            raise ValueError("SECURITY 长度编码不完整(0x81)")
        return apdu[pos + 1], 2
    if first == 0x82:
        if pos + 2 >= len(apdu):
            raise ValueError("SECURITY 长度编码不完整(0x82)")
        return (apdu[pos + 1] << 8) | apdu[pos + 2], 3
    raise ValueError(f"SECURITY 长度编码不支持：0x{first:02X}")


def parse_698_frame(raw: bytes) -> Parsed698:
    """解析 698.45 帧，失败抛 ValueError 并带原因。

    覆盖：去 FE 前导；校验 68/L/16 与 HCS/FCS；解析 C/SA/CA/APDU；SECURITY
    明文解内层 APDU；解析 GET-Request / REPORT-Notification 的 piid/oad。
    """
    if not raw:
        raise ValueError("空帧")
    frame = _strip_fe(bytes(raw))
    if len(frame) < 12:
        raise ValueError(f"帧过短（{len(frame)} 字节，不足 698 最小长度）")
    if frame[0] != 0x68:
        raise ValueError(f"起始符非 68H：0x{frame[0]:02X}")
    if frame[-1] != 0x16:
        raise ValueError("结束符非 16H")

    L = frame[1] | (frame[2] << 8)
    if len(frame) != L + 2:
        raise ValueError(f"长度域 L={L} 与帧长 {len(frame)} 不符（应为 {L + 2}）")

    sa_head = frame[4]
    sa_len = (sa_head & 0x0F) + 2
    if len(frame) < 4 + sa_len + 1 + 2 + 2 + 1:
        raise ValueError("地址域长度越界")
    ca = frame[4 + sa_len]
    addr = frame[4:4 + sa_len]

    hcs_pos = 4 + sa_len + 1
    hcs = frame[hcs_pos:hcs_pos + 2]
    if not _check_fcs(frame[1:4 + sa_len + 1], hcs):
        raise ValueError("HCS 校验失败")

    fcs_pos = len(frame) - 3
    fcs = frame[fcs_pos:fcs_pos + 2]
    if not _check_fcs(frame[1:fcs_pos], fcs):
        raise ValueError("FCS 校验失败")

    apdu = frame[hcs_pos + 2:fcs_pos]
    if not apdu:
        raise ValueError("APDU 为空")

    security: Optional[int] = None
    inner_apdu: Optional[bytes] = None
    service = apdu[0]
    apdu_used = apdu

    if apdu[0] in (APDU_SECURITY_REQUEST, APDU_SECURITY_RESPONSE):
        if len(apdu) < 3:
            raise ValueError("SECURITY-APDU 长度不足")
        choice = apdu[1]
        if choice != 0x00:
            raise ValueError(f"SECURITY 应用数据单元类型非明文：0x{choice:02X}")
        inner_len, n = _read_ber_len(apdu, 2)
        start = 2 + n
        if start + inner_len > len(apdu):
            raise ValueError("内层 APDU 长度越界")
        inner_apdu = apdu[start:start + inner_len]
        if not inner_apdu:
            raise ValueError("内层 APDU 为空")
        security = apdu[0]
        service = inner_apdu[0]
        apdu_used = inner_apdu

    piid: Optional[int] = None
    oad: Optional[bytes] = None
    oads: Optional[tuple[bytes, ...]] = None
    request_type: Optional[int] = None
    list_num: Optional[int] = None
    data: Optional[bytes] = None

    if service == APDU_GET_REQUEST:
        if len(apdu_used) < 2:
            raise ValueError("GET-Request 长度不足")
        request_type = apdu_used[1]
        if request_type == 1:                       # GetRequestNormal: PIID + OAD + timeTag
            if len(apdu_used) < 7:
                raise ValueError("GetRequestNormal 长度不足")
            piid = apdu_used[2]
            oad = apdu_used[3:7]
        elif request_type in (2, 3):                # GetRequest WithList（STA 采集实测口径 02，兼容 03）
            if len(apdu_used) < 4:
                raise ValueError("GetRequest WithList 长度不足")
            piid = apdu_used[2]
            list_num = apdu_used[3]
            if len(apdu_used) < 4 + 4 * list_num:
                raise ValueError("GetRequest WithList OAD 不完整")
            oads = tuple(apdu_used[4 + 4 * i:8 + 4 * i] for i in range(list_num))
    elif service == APDU_REPORT_NOTIFICATION:
        if len(apdu_used) < 2:
            raise ValueError("REPORT-Notification 长度不足")
        request_type = apdu_used[1]
        if request_type == 1:                       # REPORT-Notification list
            if len(apdu_used) < 8:
                raise ValueError("REPORT-Notification list 长度不足")
            piid = apdu_used[2]
            list_num = apdu_used[3]
            oad = apdu_used[4:8]
            data = apdu_used[8:]

    return Parsed698(
        raw=frame,
        control=frame[3],
        addr=addr,
        ca=ca,
        apdu=apdu,
        inner_apdu=inner_apdu,
        security=security,
        service=service,
        piid=piid,
        oad=oad,
        oads=oads,
        request_type=request_type,
        list_num=list_num,
        data=data,
    )


def _security_wrap_response(inner: bytes) -> bytes:
    """SECURITY-Response(明文) 包装：90 00 <内层长度> + 内层 APDU。"""
    if len(inner) < 0x80:
        return bytes([APDU_SECURITY_RESPONSE, 0x00, len(inner)]) + inner
    if len(inner) <= 0xFF:
        return bytes([APDU_SECURITY_RESPONSE, 0x00, 0x81, len(inner)]) + inner
    return bytes([APDU_SECURITY_RESPONSE, 0x00, 0x82,
                  (len(inner) >> 8) & 0xFF, len(inner) & 0xFF]) + inner


def _build_jzjc_inner_apdu(piid: int, descriptor: bytes) -> bytes:
    """43000200 应答明文 APDU 定长头：
    85 | 01 | PIID | 43 00 02 00 | 01(结果=数据) | 0A(visible-string) | N | 描述符。
    """
    desc = bytes(descriptor)
    return (bytes([APDU_GET_RESPONSE, 0x01, piid & 0xFF]) + JZJC_OAD
            + bytes([0x01, 0x0A, len(desc)]) + desc)


def build_jzjc_response(parsed: Parsed698, descriptor: bytes) -> bytes:
    """构造 43000200 应答完整线上帧（含 FE×4、HCS/FCS）。

    C=0xC3（服务器响应），SA=parsed.addr，CA=parsed.ca；请求是安全请求时加
    90 00 <len> 包装，否则直接回内层。
    """
    inner = _build_jzjc_inner_apdu(parsed.piid if parsed.piid is not None else 1,
                                   descriptor)
    apdu = _security_wrap_response(inner) if parsed.security is not None else inner
    frame = build_frame(apdu, parsed.addr, ca=parsed.ca, control=CTRL_SERVER_RESPONSE)
    return FE_PREAMBLE + frame


def _validate_oad(oad: bytes) -> bytes:
    oad = bytes(oad)
    if len(oad) != 4:
        raise ValueError(f"OAD 须为 4 字节：{oad.hex().upper()}")
    return oad


def build_get_normal_response(parsed: Parsed698, oad: bytes, data: bytes) -> bytes:
    """构造 GET-Response-Normal 完整线上帧（含 FE×4、HCS/FCS）。

    内层 APDU = 85 01 PIID OAD 01 data（OAD 后的 01=响应选择"数据"，与
    jzjc 应答定长头口径一致）；请求是安全请求时加 90 00 <len> 包装。
    """
    oad = _validate_oad(oad)
    piid = parsed.piid if parsed.piid is not None else 1
    inner = (bytes([APDU_GET_RESPONSE, 0x01, piid & 0xFF]) + oad
             + b"\x01" + bytes(data))
    apdu = _security_wrap_response(inner) if parsed.security is not None else inner
    frame = build_frame(apdu, parsed.addr, ca=parsed.ca, control=CTRL_SERVER_RESPONSE)
    return FE_PREAMBLE + frame


def build_get_list_response(parsed: Parsed698,
                            items: list[tuple[bytes, bytes]]) -> bytes:
    """构造 GET-Response-WithList 完整线上帧（含 FE×4、HCS/FCS）。

    内层 APDU = 85 02 PIID num [OAD 01 data]*，条目布局对齐 STA 应答结构
    more_frame_698_ack_struct（minute_assert/mclt_698.OadItem）：OAD(4, 大端)
    + 0x01(响应选择=数据) + data（长度不上线，由任务配置决定）。
    请求是安全请求时加 90 00 <len> 包装。
    """
    piid = parsed.piid if parsed.piid is not None else 1
    inner = bytearray([APDU_GET_RESPONSE, 0x02, piid & 0xFF, len(items)])
    for oad, data in items:
        inner += _validate_oad(oad) + b"\x01" + bytes(data)
    apdu = _security_wrap_response(bytes(inner)) if parsed.security is not None \
        else bytes(inner)
    frame = build_frame(apdu, parsed.addr, ca=parsed.ca, control=CTRL_SERVER_RESPONSE)
    return FE_PREAMBLE + frame


def build_event_report(target_addr: bytes, oad: bytes, data: bytes,
                       piid: int = 1) -> bytes:
    """构造 REPORT-Notification list 完整线上帧（含 FE×4、HCS/FCS）。

    APDU = 88 01 <piid> 01 <OAD 4B> <data>；SA=0x05+target_addr（目标 STA 地址），
    CA=0，控制域 0x83（服务器主动上报：DIR=1, PRM=0）。
    """
    target_addr = bytes(target_addr)
    if len(target_addr) != 6:
        raise ValueError(f"target_addr 须为 6 字节：{target_addr.hex().upper()}")
    oad = bytes(oad)
    if len(oad) != 4:
        raise ValueError(f"OAD 须为 4 字节：{oad.hex().upper()}")
    data = bytes(data)
    apdu = bytes([APDU_REPORT_NOTIFICATION, 0x01, piid & 0xFF, 0x01]) + oad + data
    sa = b"\x05" + target_addr
    frame = build_frame(apdu, sa, ca=0x00, control=CTRL_SERVER_REPORT)
    return FE_PREAMBLE + frame


def build_get_request_43000200(addr: bytes, piid: int = 1,
                               security: bool = True) -> bytes:
    """构造 STA 即装即采 43000200 读请求（测试/调试用）。

    security=True：SECURITY-Request 明文包 GET-Request，RN 用 16×00（与 STA
    chongqing_make_698_frame 模板一致）；SA=0x05+addr，C=0x43，CA=0。
    """
    addr = bytes(addr)
    if len(addr) != 6:
        raise ValueError(f"addr 须为 6 字节：{addr.hex().upper()}")
    inner = bytes([APDU_GET_REQUEST, 0x01, piid & 0xFF]) + JZJC_OAD + b"\x00"
    if security:
        apdu = (bytes([APDU_SECURITY_REQUEST, 0x00, len(inner)]) + inner
                + b"\x01\x10" + b"\x00" * 16)
    else:
        apdu = inner
    frame = build_frame(apdu, b"\x05" + addr, ca=0x00, control=CTRL_CLIENT_REQUEST)
    return FE_PREAMBLE + frame
