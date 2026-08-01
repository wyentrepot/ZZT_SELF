"""DL/T 698.45 面向对象数据交换协议适配器（原型）。

链路层帧：68H | 长度域L(2B) | 控制域C(1B) | 地址域A | 帧头校验HCS(2B) | 链路用户数据(APDU) | 帧校验FCS(2B) | 16H
- 长度域 L：低字节在前，表示"除起始字符和结束字符之外"的总字节数。
- 地址域 A：服务器地址SA（首字节低4位+2 = 总字节数） + 客户机地址CA(1B)。
- HCS/FCS：CRC-16（PPP FCS-16，初始0xFFFF，输出取反，低字节在前）。

APDU 遵循 A-XDR 编码，本适配器至少实现：
- 链路层解析 + 控制域/地址域语义；
- APDU 类型识别（LINK/CONNECT/RELEASE/GET/SET/ACTION/REPORT/PROXY/SECURITY，按 DL/T 698.45 §6.3.4 对齐）；
- GET-Request/Response Normal 的 OAD 与 Data 解析；
- 常见 Data 类型（NULL/array/structure/octet-string/visible-string/long-unsigned/double-long等）。
"""
import os
from parser_lib.core.adapter import ProtocolAdapter, ProtocolFrame, DataField, ExtractResult

# 附录 C 给出的 PPP FCS-16 查找表
_FCSTAB = [
    0x0000, 0x1189, 0x2312, 0x329b, 0x4624, 0x57ad, 0x6536, 0x74bf,
    0x8c48, 0x9dc1, 0xaf5a, 0xbed3, 0xca6c, 0xdbe5, 0xe97e, 0xf8f7,
    0x1081, 0x0108, 0x3393, 0x221a, 0x56a5, 0x472c, 0x75b7, 0x643e,
    0x9cc9, 0x8d40, 0xbfdb, 0xae52, 0xdaed, 0xcb64, 0xf9ff, 0xe876,
    0x2102, 0x308b, 0x0210, 0x1399, 0x6726, 0x76af, 0x4434, 0x55bd,
    0xad4a, 0xbcc3, 0x8e58, 0x9fd1, 0xeb6e, 0xfae7, 0xc87c, 0xd9f5,
    0x3183, 0x200a, 0x1291, 0x0318, 0x77a7, 0x662e, 0x54b5, 0x453c,
    0xbdcb, 0xac42, 0x9ed9, 0x8f50, 0xfbef, 0xea66, 0xd8fd, 0xc974,
    0x4204, 0x538d, 0x6116, 0x709f, 0x0420, 0x15a9, 0x2732, 0x36bb,
    0xce4c, 0xdfc5, 0xed5e, 0xfcd7, 0x8868, 0x99e1, 0xab7a, 0xbaf3,
    0x5285, 0x430c, 0x7197, 0x601e, 0x14a1, 0x0528, 0x37b3, 0x263a,
    0xdecd, 0xcf44, 0xfddf, 0xec56, 0x98e9, 0x8960, 0xbbfb, 0xaa72,
    0x6306, 0x728f, 0x4014, 0x519d, 0x2522, 0x34ab, 0x0630, 0x17b9,
    0xef4e, 0xfec7, 0xcc5c, 0xddd5, 0xa96a, 0xb8e3, 0x8a78, 0x9bf1,
    0x7387, 0x620e, 0x5095, 0x411c, 0x35a3, 0x242a, 0x16b1, 0x0738,
    0xffcf, 0xee46, 0xdcdd, 0xcd54, 0xb9eb, 0xa862, 0x9af9, 0x8b70,
    0x8408, 0x9581, 0xa71a, 0xb693, 0xc22c, 0xd3a5, 0xe13e, 0xf0b7,
    0x0840, 0x19c9, 0x2b52, 0x3adb, 0x4e64, 0x5fed, 0x6d76, 0x7cff,
    0x9489, 0x8500, 0xb79b, 0xa612, 0xd2ad, 0xc324, 0xf1bf, 0xe036,
    0x18c1, 0x0948, 0x3bd3, 0x2a5a, 0x5ee5, 0x4f6c, 0x7df7, 0x6c7e,
    0xa50a, 0xb483, 0x8618, 0x9791, 0xe32e, 0xf2a7, 0xc03c, 0xd1b5,
    0x2942, 0x38cb, 0x0a50, 0x1bd9, 0x6f66, 0x7eef, 0x4c74, 0x5dfd,
    0xb58b, 0xa402, 0x9699, 0x8710, 0xf3af, 0xe226, 0xd0bd, 0xc134,
    0x39c3, 0x284a, 0x1ad1, 0x0b58, 0x7fe7, 0x6e6e, 0x5cf5, 0x4d7c,
    0xc60c, 0xd785, 0xe51e, 0xf497, 0x8028, 0x91a1, 0xa33a, 0xb2b3,
    0x4a44, 0x5bcd, 0x6956, 0x78df, 0x0c60, 0x1de9, 0x2f72, 0x3efb,
    0xd68d, 0xc704, 0xf59f, 0xe416, 0x90a9, 0x8120, 0xb3bb, 0xa232,
    0x5ac5, 0x4b4c, 0x79d7, 0x685e, 0x1ce1, 0x0d68, 0x3ff3, 0x2e7a,
    0xe70e, 0xf687, 0xc41c, 0xd595, 0xa12a, 0xb0a3, 0x8238, 0x93b1,
    0x6b46, 0x7acf, 0x4854, 0x59dd, 0x2d62, 0x3ceb, 0x0e70, 0x1ff9,
    0xf78f, 0xe606, 0xd49d, 0xc514, 0xb1ab, 0xa022, 0x92b9, 0x8330,
    0x7bc7, 0x6a4e, 0x58d5, 0x495c, 0x3de3, 0x2c6a, 0x1ef1, 0x0f78,
]


def _crc16(data: bytes, init: int = 0xFFFF) -> int:
    fcs = init
    for b in data:
        fcs = (fcs >> 8) ^ _FCSTAB[(fcs ^ b) & 0xFF]
    return fcs


def _fcs_bytes(data: bytes) -> bytes:
    """计算 CRC 并返回低字节在前的 2 字节 FCS。"""
    v = _crc16(data) ^ 0xFFFF
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def _check_fcs(data_without_fcs: bytes, fcs: bytes) -> bool:
    """校验 data + fcs 是否通过（结果应为 0xF0B8）。"""
    return _crc16(data_without_fcs + fcs) == 0xF0B8


# APDU 服务类型 ↔ 名称，严格对齐 DL/T 698.45（IMA 规范 §6.3.4.1~6.3.4.4）。
# 编码规则：CHOICE 标签 [N] 即字节值；响应侧 = 请求侧 + 0x80（即 [N+128]）。
_APDU_NAMES = {
    # LINK-APDU（预连接） §6.3.4.1
    0x01: "LINK-Request", 0x81: "LINK-Response",
    # Client-APDU §6.3.4.2
    0x02: "CONNECT-Request", 0x82: "CONNECT-Response",
    0x03: "RELEASE-Request", 0x83: "RELEASE-Response",
    0x05: "GET-Request", 0x85: "GET-Response",
    0x06: "SET-Request", 0x86: "SET-Response",
    0x07: "ACTION-Request", 0x87: "ACTION-Response",
    0x08: "REPORT-Response", 0x88: "REPORT-Notification",
    0x09: "PROXY-Request", 0x89: "PROXY-Response",
    # Server-APDU §6.3.4.3
    0x84: "RELEASE-Notification",
    # SECURITY-APDU §6.3.4.4
    0x10: "SECURITY-Request", 0x90: "SECURITY-Response",
}

_GET_REQUEST_TYPES = {1: "GetRequestNormal", 2: "GetRequestNormalList", 3: "GetRequestRecord", 4: "GetRequestRecordList", 5: "GetRequestNext"}
_GET_RESPONSE_TYPES = {1: "GetResponseNormal", 2: "GetResponseNormalList", 3: "GetResponseRecord", 4: "GetResponseRecordList", 5: "GetResponseNext"}
_SET_REQUEST_TYPES = {1: "SetRequestNormal", 2: "SetRequestNormalList", 3: "SetThenGetRequestNormalList"}
_SET_RESPONSE_TYPES = {1: "SetResponseNormal", 2: "SetResponseNormalList", 3: "SetThenGetResponseNormalList"}
_ACTION_REQUEST_TYPES = {1: "ActionRequestNormal", 2: "ActionRequestNormalList", 3: "ActionThenGetRequestNormalList"}
_ACTION_RESPONSE_TYPES = {1: "ActionResponseNormal", 2: "ActionResponseNormalList", 3: "ActionThenGetResponseNormalList"}
_PROXY_REQUEST_TYPES = {1: "ProxyGetRequestList", 2: "ProxySetRequestList", 3: "ProxyGetRequestRecordList", 4: "ProxySetThenGetRequestList"}
_PROXY_RESPONSE_TYPES = {1: "ProxyGetResponseList", 2: "ProxySetResponseList", 3: "ProxyGetResponseRecordList", 4: "ProxySetThenGetResponseList"}

_AXDR_TYPE_NAMES = {
    0: "null", 1: "array", 2: "structure", 3: "bool",
    4: "bit-string", 5: "double-long", 6: "double-long-unsigned",
    9: "octet-string", 10: "visible-string", 12: "utf8-string",
    15: "integer", 16: "long", 17: "unsigned", 18: "long-unsigned",
    20: "long64", 21: "long64-unsigned", 22: "enum",
    23: "float32", 24: "float64",
    25: "date-time", 26: "date", 27: "time", 28: "date-time-s",
}


def build_frame(apdu: bytes, addr_bytes: bytes, ca: int = 0x00, control: int = 0x43) -> bytes:
    """构造一个 698.45 完整链路层帧（自动计算 HCS/FCS）。"""
    # L = 2(len) + 1(C) + len(addr_bytes) + 1(CA) + 2(HCS) + len(apdu) + 2(FCS)
    L = 2 + 1 + len(addr_bytes) + 1 + 2 + len(apdu) + 2
    body = bytes([0x68]) + bytes([L & 0xFF, (L >> 8) & 0xFF]) + bytes([control]) + addr_bytes + bytes([ca])
    hcs = _fcs_bytes(body[1:])          # 帧头 = L + C + addr_bytes + ca
    body += hcs + apdu
    fcs = _fcs_bytes(body[1:])          # 整帧 = L..APDU
    body += fcs + bytes([0x16])
    return body


def _parse_data(data: bytes, offset: int = 0):
    """递归解析 A-XDR Data，返回 (value, unit, consumed)。未支持类型返回 raw hex。"""
    if offset >= len(data):
        return ("<empty>", None, 0)
    tag = data[offset]
    n = len(data)
    def read_int_be(bs):
        val = 0
        for b in bs:
            val = (val << 8) | b
        return val
    def read_int_le(bs):
        val = 0
        for i, b in enumerate(bs):
            val |= b << (8 * i)
        return val

    # 基础类型（固定长度）
    if tag == 0:
        return (None, None, 1)
    if tag == 3:
        return (bool(data[offset+1] & 1), None, 2) if offset+1 < n else ("<raw>", None, 1)
    if tag == 5:
        return (int.from_bytes(data[offset+1:offset+5], 'big', signed=True), None, 5) if offset+4 < n else ("<raw>", None, 1)
    if tag == 6:
        return (int.from_bytes(data[offset+1:offset+5], 'big', signed=False), None, 5) if offset+4 < n else ("<raw>", None, 1)
    if tag == 15:
        return (int.from_bytes(data[offset+1:offset+2], 'big', signed=True), None, 2) if offset+1 < n else ("<raw>", None, 1)
    if tag == 16:
        return (int.from_bytes(data[offset+1:offset+3], 'big', signed=True), None, 3) if offset+2 < n else ("<raw>", None, 1)
    if tag == 17:
        return (data[offset+1], None, 2) if offset+1 < n else ("<raw>", None, 1)
    if tag == 18:
        return (int.from_bytes(data[offset+1:offset+3], 'big', signed=False), None, 3) if offset+2 < n else ("<raw>", None, 1)
    if tag == 20:
        return (int.from_bytes(data[offset+1:offset+9], 'big', signed=True), None, 9) if offset+8 < n else ("<raw>", None, 1)
    if tag == 21:
        return (int.from_bytes(data[offset+1:offset+9], 'big', signed=False), None, 9) if offset+8 < n else ("<raw>", None, 1)
    if tag == 22:
        return (data[offset+1], None, 2) if offset+1 < n else ("<raw>", None, 1)
    if tag == 23:
        return ("<float32>", None, 5) if offset+4 < n else ("<raw>", None, 1)
    if tag == 24:
        return ("<float64>", None, 9) if offset+8 < n else ("<raw>", None, 1)
    if tag == 25:
        return (data[offset+1:offset+11].hex(), None, 11) if offset+10 < n else ("<raw>", None, 1)
    if tag == 26:
        return (data[offset+1:offset+6].hex(), None, 6) if offset+5 < n else ("<raw>", None, 1)
    if tag == 27:
        return (data[offset+1:offset+4].hex(), None, 4) if offset+3 < n else ("<raw>", None, 1)
    if tag == 28:  # date-time-s: year(2B BE) + month + day + hour + min + sec
        if offset + 7 < n:
            year = (data[offset+1] << 8) | data[offset+2]
            return (f"{year:04d}-{data[offset+3]:02d}-{data[offset+4]:02d} {data[offset+5]:02d}:{data[offset+6]:02d}:{data[offset+7]:02d}", None, 8)
        return ("<raw>", None, 1)
    # 长度前缀类型
    if tag == 9:   # octet-string
        if offset+1 >= n: return ("<raw>", None, 1)
        ln = data[offset+1]
        return (data[offset+2:offset+2+ln].hex(), None, 2+ln) if offset+1+ln < n else ("<raw>", None, 1)
    if tag == 10:  # visible-string
        if offset+1 >= n: return ("<raw>", None, 1)
        ln = data[offset+1]
        raw = data[offset+2:offset+2+ln]
        try:
            txt = raw.decode('ascii')
        except Exception:
            txt = raw.hex()
        return (txt, None, 2+ln) if offset+1+ln < n else ("<raw>", None, 1)
    if tag == 12:  # UTF8-string
        if offset+1 >= n: return ("<raw>", None, 1)
        ln = data[offset+1]
        raw = data[offset+2:offset+2+ln]
        try:
            txt = raw.decode('utf-8')
        except Exception:
            txt = raw.hex()
        return (txt, None, 2+ln) if offset+1+ln < n else ("<raw>", None, 1)
    if tag == 4:   # bit-string
        if offset+1 >= n: return ("<raw>", None, 1)
        ln = data[offset+1]
        return (data[offset+2:offset+2+ln].hex(), None, 2+ln) if offset+1+ln < n else ("<raw>", None, 1)
    if tag == 1:   # array
        if offset+1 >= n: return ("<raw>", None, 1)
        cnt = data[offset+1]
        vals = []
        pos = offset + 2
        for _ in range(cnt):
            v, u, c = _parse_data(data, pos)
            vals.append(v)
            pos += c
        return (vals, None, pos - offset)
    if tag == 2:   # structure
        if offset+1 >= n: return ("<raw>", None, 1)
        cnt = data[offset+1]
        vals = []
        pos = offset + 2
        for _ in range(cnt):
            v, u, c = _parse_data(data, pos)
            vals.append(v)
            pos += c
        return (vals, None, pos - offset)
    # OAD / OI / ROAD / OMD 等
    if tag == 80:  # OI
        return (data[offset+1:offset+3].hex(), None, 3) if offset+2 < n else ("<raw>", None, 1)
    if tag == 81:  # OAD
        return (data[offset+1:offset+5].hex(), None, 5) if offset+4 < n else ("<raw>", None, 1)
    if tag == 82:  # ROAD
        if offset+1 >= n: return ("<raw>", None, 1)
        oad = data[offset+1:offset+5].hex()
        ln = data[offset+5]
        rows = [data[offset+6+4*i:offset+10+4*i].hex() for i in range(ln)] if offset+5+4*ln < n else []
        return ({"OAD": oad, "rows": rows}, None, 6+4*ln)
    # 未支持类型，返回 raw
    return (data[offset+1:].hex(), None, len(data) - offset)


def _oad_key(oad_bytes: bytes) -> str:
    return oad_bytes.hex().upper()


class DLT69845Adapter(ProtocolAdapter):
    protocol = "698.45"

    def __init__(self, metadata_store=None):
        self.metadata_store = metadata_store

    # ---------- 切帧（含 HCS/FCS 校验） ----------
    def try_extract(self, buf: bytes):
        n = len(buf)
        start = 0
        if n < 4 or buf[0] != 0x68:
            return None
        # 1376.2 帧在第 3 字节也是 0x68（68..L..68..），698 此处是控制域，
        # 据此在切帧阶段就拒绝，使其回退给 1376.2 适配器路由。
        if start + 3 < n and buf[start + 3] == 0x68:
            return None
        # 长度域 L（小端）
        L = buf[start + 1] | (buf[start + 2] << 8)
        if L < 12 or L > 2000:
            return None
        frame_end = start + L + 1
        if frame_end >= n or buf[frame_end] != 0x16:
            return None
        # 取完整帧
        raw = buf[start:frame_end + 1]
        # SA 长度
        SA_head = raw[4]
        SA_len = (SA_head & 0x0F) + 2
        if len(raw) < 4 + SA_len + 8:
            return None
        # 校验（可选：HCS/FCS 错仍可切出，但 confidence 会降低）
        hcs_pos = 4 + SA_len + 1
        hcs = raw[hcs_pos:hcs_pos + 2]
        fcs_pos = frame_end - 2  # FCS 位置
        fcs = raw[fcs_pos:fcs_pos + 2]
        hcs_ok = _check_fcs(raw[1:4 + SA_len + 1], hcs)
        fcs_ok = _check_fcs(raw[1:fcs_pos], fcs)
        # 结构基本成立即切出
        return ExtractResult(raw=raw, consumed=frame_end + 1 - start)

    def confidence(self, raw: bytes) -> float:
        # 698.45 帧结构：68 | L(2) | C | SA | CA | HCS | APDU | FCS | 16
        # 识别特征（索引 95 帧类别 · 698.45 族）：起始 68H、结束 16H、L=帧长-2、
        # 第 3 字节是控制域(非 0x68)、APDU 首字节为合法服务类型标签。
        if len(raw) < 12 or raw[0] != 0x68 or raw[-1] != 0x16:
            return 0.0
        # 1376.2 帧第二 68H 在 pos3（68..L..68..），698 此处是控制域，绝不可能是 0x68
        if raw[3] == 0x68:
            return 0.0
        # 防御：若 pos7==0x68 且整体符合 645 结构（12+L，L=pos9），那是 645 帧，不要抢占
        if raw[7] == 0x68:
            L645 = raw[9] if len(raw) > 9 else -1
            if 0 <= L645 <= 200 and len(raw) == 12 + L645:
                return 0.0
        try:
            L = raw[1] | (raw[2] << 8)
            if len(raw) != L + 2:
                return 0.0
            SA_head = raw[4]
            SA_len = (SA_head & 0x0F) + 2
            if len(raw) < 4 + SA_len + 3:
                return 0.0
            hcs_pos = 4 + SA_len + 1
            fcs_pos = len(raw) - 3
            # APDU 服务类型标签（识别特征）：合法则更可信
            apdu_tag = raw[hcs_pos + 2] if fcs_pos > hcs_pos + 2 else None
            known_apdu = apdu_tag in _APDU_NAMES
            if not _check_fcs(raw[1:4 + SA_len + 1], raw[hcs_pos:hcs_pos + 2]):
                return 0.3
            if not _check_fcs(raw[1:fcs_pos], raw[fcs_pos:fcs_pos + 2]):
                return 0.5
            return 1.0 if known_apdu else 0.85
        except Exception:
            return 0.0

    # ---------- 解码 ----------
    def decode(self, raw: bytes) -> ProtocolFrame:
        frame = ProtocolFrame(structure="698.45", raw_hex=raw.hex())
        warnings = []
        L = raw[1] | (raw[2] << 8)
        C = raw[3]
        SA_head = raw[4]
        SA_len = (SA_head & 0x0F) + 2
        SA_bytes = raw[4:4 + SA_len]
        CA = raw[4 + SA_len]
        hcs_pos = 4 + SA_len + 1
        HCS = raw[hcs_pos:hcs_pos + 2]
        fcs_pos = len(raw) - 3
        FCS = raw[fcs_pos:fcs_pos + 2]
        APDU = raw[hcs_pos + 2:fcs_pos]

        # 控制域
        DIR = "服务器发出" if (C & 0x80) else "客户机发出"
        PRM = "启动站" if (C & 0x40) else "从动站"
        fragmented = bool(C & 0x20)
        func_code = C & 0x0F
        func_name = {1: "链路管理", 3: "用户数据"}.get(func_code, f"功能码{func_code}")

        frame.fields.append(DataField(name="长度域L", value=L, hex=f"{L:04X}", raw=L))
        frame.fields.append(DataField(name="控制域C", value=f"0x{C:02X}", hex=f"{C:02X}", raw=C,
            desc=f"{DIR};{PRM};{'分帧' if fragmented else '完整'};{func_name}"))
        # SA 逻辑地址 = 地址部分字节反转
        sa_addr = SA_bytes[1:]
        logical_addr = sa_addr[::-1].hex().upper()
        sa_type = "通配地址" if (SA_head & 0x40) else "单地址"
        frame.fields.append(DataField(name="服务器地址SA", value=SA_bytes.hex().upper(), hex=SA_bytes.hex(), raw=SA_bytes.hex(),
            desc=f"逻辑地址:{logical_addr};{sa_type};长度={SA_len}B"))
        frame.fields.append(DataField(name="客户机地址CA", value=f"0x{CA:02X}", hex=f"{CA:02X}", raw=CA))

        hcs_ok = _check_fcs(raw[1:4 + SA_len + 1], HCS)
        fcs_ok = _check_fcs(raw[1:fcs_pos], FCS)
        frame.fields.append(DataField(name="帧头校验HCS", value=f"0x{HCS.hex().upper()}", hex=HCS.hex(),
            desc="校验" + ("通过" if hcs_ok else "失败")))
        frame.fields.append(DataField(name="帧校验FCS", value=f"0x{FCS.hex().upper()}", hex=FCS.hex(),
            desc="校验" + ("通过" if fcs_ok else "失败")))
        if not hcs_ok:
            warnings.append("HCS校验失败")
        if not fcs_ok:
            warnings.append("FCS校验失败")

        # APDU
        if not APDU:
            warnings.append("APDU为空")
            frame.warnings = warnings
            return frame

        apdu_tag = APDU[0]
        apdu_name = _APDU_NAMES.get(apdu_tag, f"APDU-0x{apdu_tag:02X}")
        frame.fields.append(DataField(name="APDU类型", value=apdu_name, hex=f"{apdu_tag:02X}", raw=apdu_tag))

        self._parse_apdu(APDU, frame, warnings)
        frame.warnings = warnings
        return frame

    def _parse_apdu(self, apdu: bytes, frame: ProtocolFrame, warnings: list):
        tag = apdu[0]
        if tag == 0x05:   # GET-Request
            self._parse_get_request(apdu, frame, warnings)
        elif tag == 0x85: # GET-Response
            self._parse_get_response(apdu, frame, warnings)
        elif tag == 0x06: # SET-Request
            self._parse_set_request(apdu, frame, warnings)
        elif tag == 0x86: # SET-Response
            self._parse_set_response(apdu, frame, warnings)
        elif tag == 0x07: # ACTION-Request
            self._parse_action_request(apdu, frame, warnings)
        elif tag == 0x87: # ACTION-Response
            self._parse_action_response(apdu, frame, warnings)
        elif tag == 0x09: # PROXY-Request
            self._parse_proxy_request(apdu, frame, warnings)
        elif tag == 0x89: # PROXY-Response
            self._parse_proxy_response(apdu, frame, warnings)
        elif tag in (0x10, 0x90):
            self._parse_security(apdu, frame, warnings)
        elif tag in (0x01, 0x81, 0x02, 0x82, 0x03, 0x83, 0x84,
                     0x08, 0x88):
            # 其他已识别类型：仅记录原始 APDU，不产生 warning
            frame.items.append(DataField(name="APDU数据", value=apdu.hex(), hex=apdu.hex(), raw=apdu.hex()))
        else:
            warnings.append(f"未识别的APDU类型 0x{tag:02X}")
            frame.items.append(DataField(name="APDU原始", value=apdu.hex(), hex=apdu.hex(), raw=apdu.hex()))

    def _parse_security(self, apdu: bytes, frame: ProtocolFrame, warnings: list):
        """解析 SECURITY-APDU (§6.3.4.4)：解包内层 APDU 并递归解析。"""
        tag = apdu[0]
        is_response = (tag == 0x90)
        if len(apdu) < 3:
            warnings.append("SECURITY-APDU长度不足"); return

        # 应用数据单元 CHOICE: [0]=明文 [1]=密文 [2]=异常DAR
        app_choice = apdu[1]
        choice_names = {0: "明文APDU", 1: "密文APDU", 2: "异常错误"}
        pos = 2

        if app_choice == 2:  # 异常错误 DAR
            dar = apdu[pos] if pos < len(apdu) else None
            frame.fields.append(DataField(name="应用数据类型", value=f"异常错误(DAR=0x{dar:02X})" if dar is not None else "异常错误",
                hex=f"{app_choice:02X}", raw=app_choice))
            return

        # 明文/密文: octet-string (BER 长度编码)
        if pos >= len(apdu):
            warnings.append("SECURITY数据不完整"); return
        len_byte = apdu[pos]; pos += 1
        if len_byte < 0x80:
            inner_len = len_byte
        elif len_byte == 0x81:
            inner_len = apdu[pos] if pos < len(apdu) else 0; pos += 1
        elif len_byte == 0x82:
            inner_len = ((apdu[pos] << 8) | apdu[pos+1]) if pos + 1 < len(apdu) else 0; pos += 2
        else:
            warnings.append(f"SECURITY长度编码不支持: 0x{len_byte:02X}"); return

        inner_apdu = apdu[pos:pos + inner_len]
        pos += inner_len

        frame.fields.append(DataField(name="应用数据类型", value=choice_names.get(app_choice, f"未知(0x{app_choice:02X})"),
            hex=f"{app_choice:02X}", raw=app_choice))
        frame.fields.append(DataField(name="内层APDU长度", value=inner_len, hex=f"{inner_len:04X}", raw=inner_len))

        # 数据验证信息
        if pos < len(apdu):
            if is_response:
                # Response: OPTIONAL, present标志(0x01=有)
                present = apdu[pos]; pos += 1
                if present == 0x01 and pos < len(apdu):
                    verify_choice = apdu[pos]; pos += 1
                    if verify_choice == 0:  # [0] MAC
                        mac_len = apdu[pos] if pos < len(apdu) else 0; pos += 1
                        mac = apdu[pos:pos + mac_len]; pos += mac_len
                        frame.fields.append(DataField(name="数据验证信息", value="MAC",
                            hex=f"{present:02X}", raw=present, desc=f"MAC({mac_len}B): {mac.hex().upper()}"))
                    else:
                        frame.fields.append(DataField(name="数据验证信息", value=f"未知类型(0x{verify_choice:02X})",
                            hex=f"{verify_choice:02X}", raw=verify_choice))
                else:
                    frame.fields.append(DataField(name="数据验证信息", value="无",
                        hex=f"{present:02X}", raw=present))
            else:
                # Request: 非OPTIONAL, 直接CHOICE
                verify_choice = apdu[pos]; pos += 1
                verify_names = {0: "SID_MAC", 1: "RN", 2: "RN_MAC", 3: "SID"}
                frame.fields.append(DataField(name="数据验证类型",
                    value=verify_names.get(verify_choice, f"0x{verify_choice:02X}"),
                    hex=f"{verify_choice:02X}", raw=verify_choice))
                if pos < len(apdu):
                    verify_data = apdu[pos:]
                    frame.fields.append(DataField(name="验证数据", value=verify_data.hex().upper(),
                        hex=verify_data.hex(), raw=verify_data.hex()))

        # 递归解析内层 APDU
        if inner_apdu:
            inner_tag = inner_apdu[0]
            inner_name = _APDU_NAMES.get(inner_tag, f"APDU-0x{inner_tag:02X}")
            frame.fields.append(DataField(name="内层APDU类型", value=inner_name,
                hex=f"{inner_tag:02X}", raw=inner_tag))
            self._parse_apdu(inner_apdu, frame, warnings)

    def _parse_get_request(self, apdu, frame, warnings):
        if len(apdu) < 2:
            warnings.append("GET-Request长度不足"); return
        req_type = apdu[1]
        frame.fields.append(DataField(name="请求类型", value=f"0x{req_type:02X} ({_GET_REQUEST_TYPES.get(req_type, '未知')})",
            hex=f"{req_type:02X}", raw=req_type))
        if req_type == 1 and len(apdu) >= 7:  # GetRequestNormal
            piid = apdu[2]
            oad = apdu[3:7]
            time_tag = apdu[7] if len(apdu) > 7 else None
            frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
            self._add_oad_item(oad, frame, None, time_tag)
        elif req_type == 2 and len(apdu) >= 4:  # GetRequestNormalList
            piid = apdu[2]
            count = apdu[3]
            frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
            frame.fields.append(DataField(name="OAD个数", value=count, hex=f"{count:02X}", raw=count))
            pos = 4
            for i in range(count):
                if pos + 4 > len(apdu): break
                oad = apdu[pos:pos+4]
                self._add_oad_item(oad, frame, f"OAD[{i+1}]", None)
                pos += 4
        elif req_type == 3:  # GetRequestRecord
            self._parse_get_request_record(apdu, frame, warnings)
        else:
            frame.items.append(DataField(name="GET请求数据", value=apdu[2:].hex(), hex=apdu[2:].hex(), raw=apdu[2:].hex()))

    def _parse_get_response(self, apdu, frame, warnings):
        if len(apdu) < 2:
            warnings.append("GET-Response长度不足"); return
        resp_type = apdu[1]
        frame.fields.append(DataField(name="响应类型", value=f"0x{resp_type:02X} ({_GET_RESPONSE_TYPES.get(resp_type, '未知')})",
            hex=f"{resp_type:02X}", raw=resp_type))
        if resp_type == 1 and len(apdu) >= 7:  # GetResponseNormal
            piid = apdu[2]
            oad = apdu[3:7]
            data = apdu[7:]
            frame.fields.append(DataField(name="PIID-ACD", value=piid, hex=f"{piid:02X}", raw=piid))
            # Get-Result: tag=0 → DAR; tag=1 → Data
            if data:
                tag = data[0]
                if tag == 0:  # 错误
                    dar = data[1] if len(data) > 1 else None
                    frame.items.append(DataField(name="数据访问结果DAR", value=f"错误(0x{dar:02X})" if dar else "错误",
                        hex=data.hex(), raw=data.hex(), desc="读取失败"))
                elif tag == 1:  # Data
                    val, unit, consumed = _parse_data(data, 1)
                    self._add_oad_item(oad, frame, val, None, unit)
                else:
                    val, unit, consumed = _parse_data(data)
                    self._add_oad_item(oad, frame, val, None, unit)
        elif resp_type == 2 and len(apdu) >= 4:  # GetResponseNormalList
            piid = apdu[2]
            count = apdu[3]
            frame.fields.append(DataField(name="PIID-ACD", value=piid, hex=f"{piid:02X}", raw=piid))
            frame.fields.append(DataField(name="OAD个数", value=count, hex=f"{count:02X}", raw=count))
            pos = 4
            for i in range(count):
                if pos + 4 > len(apdu): break
                oad = apdu[pos:pos+4]
                pos += 4
                if pos < len(apdu) and apdu[pos] == 1:  # [1] Data
                    val, unit, consumed = _parse_data(apdu, pos + 1)
                    self._add_oad_item(oad, frame, val, None, unit)
                    pos += 1 + consumed
                else:
                    self._add_oad_item(oad, frame, None, None)
        elif resp_type == 3:  # GetResponseRecord
            self._parse_get_response_record(apdu, frame, warnings)
        else:
            frame.items.append(DataField(name="GET响应", value=apdu[1:].hex(), hex=apdu[1:].hex(), raw=apdu[1:].hex()))

    def _parse_set_request(self, apdu, frame, warnings):
        if len(apdu) < 2:
            warnings.append("SET-Request长度不足"); return
        req_type = apdu[1]
        frame.fields.append(DataField(name="请求类型", value=f"0x{req_type:02X} ({_SET_REQUEST_TYPES.get(req_type, '未知')})",
            hex=f"{req_type:02X}", raw=req_type))
        if req_type == 1 and len(apdu) >= 7:  # SetRequestNormal: PIID + OAD + Data
            piid = apdu[2]
            oad = apdu[3:7]
            frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
            data = apdu[7:]
            if data:
                val, unit, consumed = _parse_data(data, 0)
                self._add_oad_item(oad, frame, val, None, unit)
            else:
                self._add_oad_item(oad, frame, None, None)
        elif len(apdu) > 2:
            frame.items.append(DataField(name="SET数据", value=apdu[2:].hex(), hex=apdu[2:].hex(), raw=apdu[2:].hex()))

    def _parse_set_response(self, apdu, frame, warnings):
        if len(apdu) < 3:
            warnings.append("SET-Response长度不足"); return
        resp_type = apdu[1]
        frame.fields.append(DataField(name="响应类型", value=f"0x{resp_type:02X} ({_SET_RESPONSE_TYPES.get(resp_type, '未知')})",
            hex=f"{resp_type:02X}", raw=resp_type))
        piid = apdu[2]
        frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
        if resp_type == 1 and len(apdu) >= 7:  # SetResponseNormal: PIID + OAD + Result
            oad = apdu[3:7]
            self._add_oad_item(oad, frame, None, None)
            self._parse_result_data(apdu[7:], frame, "SET结果")
        else:
            self._parse_result_data(apdu[3:], frame, "SET结果")

    def _parse_action_request(self, apdu, frame, warnings):
        if len(apdu) < 2:
            warnings.append("ACTION-Request长度不足"); return
        req_type = apdu[1]
        frame.fields.append(DataField(name="请求类型", value=f"0x{req_type:02X} ({_ACTION_REQUEST_TYPES.get(req_type, '未知')})",
            hex=f"{req_type:02X}", raw=req_type))
        if req_type == 1 and len(apdu) >= 7:  # ActionRequestNormal: PIID + OMD + Data
            piid = apdu[2]
            omd = apdu[3:7]
            frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
            frame.items.append(DataField(name="OMD", value=omd.hex().upper(), hex=omd.hex(), raw=omd.hex()))
            self._parse_result_data(apdu[7:], frame, "ACTION数据")
        elif len(apdu) > 2:
            frame.items.append(DataField(name="ACTION数据", value=apdu[2:].hex(), hex=apdu[2:].hex(), raw=apdu[2:].hex()))

    def _parse_action_response(self, apdu, frame, warnings):
        if len(apdu) < 3:
            warnings.append("ACTION-Response长度不足"); return
        resp_type = apdu[1]
        frame.fields.append(DataField(name="响应类型", value=f"0x{resp_type:02X} ({_ACTION_RESPONSE_TYPES.get(resp_type, '未知')})",
            hex=f"{resp_type:02X}", raw=resp_type))
        piid = apdu[2]
        frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
        if resp_type == 1 and len(apdu) >= 7:  # ActionResponseNormal: PIID + OMD + Result
            omd = apdu[3:7]
            frame.items.append(DataField(name="OMD", value=omd.hex().upper(), hex=omd.hex(), raw=omd.hex()))
            self._parse_result_data(apdu[7:], frame, "ACTION结果")
        else:
            self._parse_result_data(apdu[3:], frame, "ACTION结果")

    def _parse_proxy_request(self, apdu, frame, warnings):
        """解析 PROXY-Request (§6.3.4.2)：PIID + 超时 + 服务器列表 + 时间标签。

        结构：tag(1B) + sub-type(1B) + PIID(1B) + timeout(2B) + server_count(1B)
              + N × [TSA(tag+addr) + OAD_count(1B) + OADs(4B each)] + time_tag(1B, OPTIONAL)
        """
        if len(apdu) < 3:
            warnings.append("PROXY-Request长度不足"); return
        req_type = apdu[1]
        frame.fields.append(DataField(name="请求类型",
            value=f"0x{req_type:02X} ({_PROXY_REQUEST_TYPES.get(req_type, '未知')})",
            hex=f"{req_type:02X}", raw=req_type))
        piid = apdu[2]
        frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))

        pos = 3
        # 超时时间 (2 bytes)
        if pos + 2 <= len(apdu):
            timeout = (apdu[pos] << 8) | apdu[pos + 1]
            frame.fields.append(DataField(name="超时时间", value=timeout,
                hex=f"{timeout:04X}", raw=timeout))
            pos += 2

        # 服务器个数
        if pos < len(apdu):
            server_count = apdu[pos]; pos += 1
            frame.fields.append(DataField(name="服务器个数", value=server_count,
                hex=f"{server_count:02X}", raw=server_count))

            for i in range(server_count):
                if pos + 2 > len(apdu):
                    break
                # TSA: tag(1B) + length(1B) + address(length bytes)
                tsa_tag = apdu[pos]
                tsa_len = apdu[pos + 1]
                tsa_total = 2 + tsa_len
                if pos + tsa_total > len(apdu):
                    # TSA 越界，存储剩余数据
                    frame.items.append(DataField(name=f"服务器[{i}]原始数据",
                        value=apdu[pos:].hex(), hex=apdu[pos:].hex(), raw=apdu[pos:].hex()))
                    pos = len(apdu)
                    break
                tsa = apdu[pos + 2:pos + 2 + tsa_len]
                frame.fields.append(DataField(name=f"服务器TSA[{i}]",
                    value=tsa.hex().upper(),
                    hex=apdu[pos:pos + tsa_total].hex(), raw=tsa.hex(),
                    desc=f"TSA(tag=0x{tsa_tag:02X})"))
                pos += tsa_total

                # OAD个数
                if pos >= len(apdu):
                    break
                oad_count = apdu[pos]; pos += 1
                frame.fields.append(DataField(name=f"服务器[{i}]OAD个数",
                    value=oad_count, hex=f"{oad_count:02X}", raw=oad_count))
                for j in range(oad_count):
                    if pos + 4 > len(apdu):
                        break
                    oad = apdu[pos:pos + 4]; pos += 4
                    self._add_oad_item(oad, frame, None, None)

        # 时间标签 (OPTIONAL)
        if pos < len(apdu):
            time_tag = apdu[pos]; pos += 1
            frame.fields.append(DataField(name="时间标签域",
                value="无" if time_tag == 0 else f"有(0x{time_tag:02X})",
                hex=f"{time_tag:02X}", raw=time_tag))

        # 剩余数据
        if pos < len(apdu):
            frame.items.append(DataField(name="PROXY附加数据",
                value=apdu[pos:].hex(), hex=apdu[pos:].hex(), raw=apdu[pos:].hex()))

    def _parse_proxy_response(self, apdu, frame, warnings):
        """解析 PROXY-Response (§6.3.4.2)：PIID + 服务器响应列表 + 时间标签。"""
        if len(apdu) < 3:
            warnings.append("PROXY-Response长度不足"); return
        resp_type = apdu[1]
        frame.fields.append(DataField(name="响应类型",
            value=f"0x{resp_type:02X} ({_PROXY_RESPONSE_TYPES.get(resp_type, '未知')})",
            hex=f"{resp_type:02X}", raw=resp_type))
        piid = apdu[2]
        frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))
        # ProxyResponse 结构复杂（含每服务器 DAR/Data），当前存储原始数据
        if len(apdu) > 3:
            frame.items.append(DataField(name="PROXY响应数据",
                value=apdu[3:].hex(), hex=apdu[3:].hex(), raw=apdu[3:].hex()))

    def _parse_result_data(self, data: bytes, frame: ProtocolFrame, label: str):
        """解析 DAR/Data 二选一：tag=0 → 错误(DAR)；tag=1 → Data。"""
        if not data:
            return
        tag = data[0]
        if tag == 0:  # DAR 访问错误
            dar = data[1] if len(data) > 1 else None
            frame.items.append(DataField(name=label, value=f"访问错误(0x{dar:02X})" if dar is not None else "访问错误",
                hex=data.hex(), raw=data.hex(), desc="数据访问结果(DAR)"))
        elif tag == 1:  # Data
            val, unit, consumed = _parse_data(data, 1)
            frame.items.append(DataField(name=label, value=val, unit=unit, hex=data.hex(), raw=data.hex()))
        else:
            frame.items.append(DataField(name=label, value=data.hex(), hex=data.hex(), raw=data.hex()))

    def _lookup_oad_name(self, oad: bytes) -> str:
        """查 OAD 字典返回中文名，查不到返回 OAD(hex)。"""
        oad_key = _oad_key(oad)
        if self.metadata_store:
            meta = self.metadata_store.lookup("698.45", oad_key)
            if meta:
                return meta.get("name", f"OAD({oad_key})")
        return f"OAD({oad_key})"

    def _parse_get_request_record(self, apdu: bytes, frame: ProtocolFrame, warnings: list):
        """解析 GetRequestRecord (req_type=3)：PIID + OAD + RSD + RCSD + OPTIONAL timeTag。

        GetRecord ::= SEQUENCE { OAD, RSD, RCSD }
        RSD ::= CHOICE [0]NULL / [1]Selector1{OAD,Data} / [5]Selector5{date-time-s,PS} / ...
        """
        pos = 2  # 跳过 tag(05) + req_type(03)
        if pos >= len(apdu):
            warnings.append("GetRequestRecord长度不足"); return

        # PIID
        piid = apdu[pos]; pos += 1
        frame.fields.append(DataField(name="PIID", value=piid, hex=f"{piid:02X}", raw=piid))

        # OAD (冻结对象)
        if pos + 4 > len(apdu):
            warnings.append("GetRequestRecord OAD不完整"); return
        oad = apdu[pos:pos + 4]; pos += 4
        self._add_oad_item(oad, frame, None, None)

        # RSD (Record Selection Descriptor, §6.3.3.7)
        if pos >= len(apdu):
            warnings.append("GetRequestRecord RSD缺失"); return
        rsd_method = apdu[pos]; pos += 1
        rsd_names = {0: "不选择", 1: "指定值(Selector1)", 2: "范围(Selector2)",
                     3: "范围(Selector3)", 4: "范围(Selector4)", 5: "时间+表集合(Selector5)",
                     6: "Selector6", 7: "Selector7", 8: "Selector8", 9: "Selector9", 10: "Selector10"}
        frame.fields.append(DataField(name="RSD选择方法", value=rsd_names.get(rsd_method, f"方法{rsd_method}"),
            hex=f"{rsd_method:02X}", raw=rsd_method))

        if rsd_method == 0:
            pass  # NULL, no additional data
        elif rsd_method == 1:
            # Selector1: OAD(4B) + Data
            if pos + 4 > len(apdu):
                warnings.append("RSD Selector1 OAD不完整"); return
            rsd_oad = apdu[pos:pos + 4]; pos += 4
            rsd_name = self._lookup_oad_name(rsd_oad)
            frame.fields.append(DataField(name="RSD对象", value=rsd_oad.hex().upper(),
                hex=rsd_oad.hex(), raw=rsd_oad.hex(), desc=rsd_name))
            # Data = A-XDR value
            if pos < len(apdu):
                val, unit, consumed = _parse_data(apdu, pos)
                frame.fields.append(DataField(name="RSD选择值", value=val, unit=unit,
                    hex=apdu[pos:pos+consumed].hex(), raw=apdu[pos:pos+consumed].hex()))
                pos += consumed
        elif rsd_method == 5:
            # Selector5: date-time-s(7B, no tag) + PS
            if pos + 7 <= len(apdu):
                year = (apdu[pos] << 8) | apdu[pos + 1]
                month, day, hour, minute, second = apdu[pos + 2:pos + 7]
                frame.fields.append(DataField(name="RSD起始时间",
                    value=f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
                    hex=apdu[pos:pos+7].hex(), raw=apdu[pos:pos+7].hex()))
                pos += 7
            # PS (Population Set): [3] SEQUENCE OF TSA
            if pos < len(apdu):
                ps_tag = apdu[pos]; pos += 1
                if ps_tag == 3 and pos < len(apdu):
                    tsa_count = apdu[pos]; pos += 1
                    frame.fields.append(DataField(name="RSD表集合数", value=tsa_count,
                        hex=f"{tsa_count:02X}", raw=tsa_count))
                    for i in range(tsa_count):
                        if pos + 2 > len(apdu): break
                        tsa_tag = apdu[pos]; pos += 1
                        tsa_len = apdu[pos]; pos += 1
                        if pos + tsa_len > len(apdu): break
                        tsa = apdu[pos:pos + tsa_len]; pos += tsa_len
                        frame.fields.append(DataField(name=f"TSA[{i}]",
                            value=tsa.hex().upper(), hex=tsa.hex(), raw=tsa.hex()))
        else:
            # Other methods: best-effort, store remaining as raw
            if pos < len(apdu):
                frame.fields.append(DataField(name="RSD原始数据", value=apdu[pos:].hex(),
                    hex=apdu[pos:].hex(), raw=apdu[pos:].hex()))
                pos = len(apdu)

        # RCSD (Record Column Selection Descriptor, §6.3.3.8)
        if pos >= len(apdu):
            return
        rcsd_count = apdu[pos]; pos += 1
        frame.fields.append(DataField(name="RCSD对象个数", value=rcsd_count, hex=f"{rcsd_count:02X}", raw=rcsd_count))

        csd_oads = []
        for i in range(rcsd_count):
            if pos + 5 > len(apdu):
                warnings.append(f"RCSD CSD[{i}]数据不完整"); break
            csd_type = apdu[pos]
            csd_oad = apdu[pos + 1:pos + 5]
            csd_oads.append(csd_oad)
            pos += 5
            csd_name = self._lookup_oad_name(csd_oad)
            type_name = "OAD" if csd_type == 0 else ("OMD" if csd_type == 1 else f"type{csd_type}")
            frame.fields.append(DataField(name=f"读取列CSD[{i}]", value=csd_oad.hex().upper(),
                hex=csd_oad.hex(), raw=csd_oad.hex(),
                desc=f"{type_name}: {csd_name}"))

        # 时间标签 (OPTIONAL)
        if pos < len(apdu):
            time_tag = apdu[pos]; pos += 1
            frame.fields.append(DataField(name="时间标签域",
                value="无" if time_tag == 0 else f"有(0x{time_tag:02X})",
                hex=f"{time_tag:02X}", raw=time_tag))

    def _parse_get_response_record(self, apdu: bytes, frame: ProtocolFrame, warnings: list):
        """解析 GetResponseRecord (resp_type=3)：PIID-ACD + OAD + RCSD + 记录 + 跟随上报 + 时间标签。"""
        pos = 2  # 跳过 tag(85) + resp_type(03)
        if pos >= len(apdu):
            warnings.append("GetResponseRecord长度不足"); return

        # PIID-ACD
        piid = apdu[pos]; pos += 1
        frame.fields.append(DataField(name="PIID-ACD", value=piid, hex=f"{piid:02X}", raw=piid))

        # OAD (冻结对象)
        oad = apdu[pos:pos + 4]; pos += 4
        self._add_oad_item(oad, frame, None, None)

        # RCSD: count + N × CSD
        rcsd_count = apdu[pos]; pos += 1
        frame.fields.append(DataField(name="RCSD对象个数", value=rcsd_count, hex=f"{rcsd_count:02X}", raw=rcsd_count))

        csd_oads = []
        for i in range(rcsd_count):
            if pos + 5 > len(apdu):
                warnings.append(f"RCSD CSD[{i}]数据不完整"); break
            csd_type = apdu[pos]
            csd_oad = apdu[pos + 1:pos + 5]
            csd_oads.append(csd_oad)
            pos += 5
            csd_name = self._lookup_oad_name(csd_oad)
            type_name = "OAD" if csd_type == 0 else ("OMD" if csd_type == 1 else f"type{csd_type}")
            csd_full_hex = f"{csd_type:02x}" + csd_oad.hex()
            frame.fields.append(DataField(name=f"CSD[{i}]", value=csd_oad.hex().upper(),
                hex=csd_full_hex, raw=csd_oad.hex(),
                desc=f"{type_name}: {csd_name}"))

        # 记录: M(记录条数) × [N(行数) × (RCSD个数据项)]
        if pos >= len(apdu):
            return
        record_count = apdu[pos]; pos += 1
        frame.fields.append(DataField(name="记录条数", value=record_count, hex=f"{record_count:02X}", raw=record_count))

        for r in range(record_count):
            if pos >= len(apdu):
                break
            row_count = apdu[pos]; pos += 1
            for row in range(row_count):
                for i in range(rcsd_count):
                    if pos >= len(apdu):
                        break
                    val, unit, consumed = _parse_data(apdu, pos)
                    oad_i = csd_oads[i] if i < len(csd_oads) else b'\x00\x00\x00\x00'
                    data_hex = apdu[pos:pos + consumed].hex() if consumed > 0 else ""
                    type_tag = _AXDR_TYPE_NAMES.get(apdu[pos], "") if consumed > 0 else ""
                    self._add_oad_item(oad_i, frame, val, None, unit, data_hex, type_tag)
                    pos += consumed

        # 跟随上报域 (OPTIONAL)
        if pos < len(apdu):
            following = apdu[pos]; pos += 1
            frame.fields.append(DataField(name="跟随上报域",
                value="无" if following == 0 else f"有(0x{following:02X})",
                hex=f"{following:02X}", raw=following))

        # 时间标签域 (OPTIONAL)
        if pos < len(apdu):
            time_tag = apdu[pos]; pos += 1
            frame.fields.append(DataField(name="时间标签域",
                value="无" if time_tag == 0 else f"有(0x{time_tag:02X})",
                hex=f"{time_tag:02X}", raw=time_tag))

    def _add_oad_item(self, oad: bytes, frame: ProtocolFrame, value=None, time_tag=None, unit=None, data_hex="", type_tag=""):
        oad_key = _oad_key(oad)
        oi = oad_key[:4]
        attr = oad[2]
        idx = oad[3]
        meta = self.metadata_store.lookup("698.45", oad_key) if self.metadata_store else None
        if meta:
            name = meta.get("name", f"OAD({oad_key})")
            unit = unit or meta.get("unit")
            scale = meta.get("scale", 0)
            if scale and isinstance(value, (int, float)):
                value = value / (10 ** scale)
            desc = meta.get("desc", "")
        else:
            name = f"OAD({oad_key})"
            desc = f"OI={oi} 属性={attr} 索引={idx}"
        if value is None:
            value = oad_key
        # data_hex 非空时为数据项(存数据hex+类型标签)；为空时为OAD引用(存OAD key+元数据desc)
        final_hex = data_hex if data_hex else oad_key
        final_desc = type_tag if type_tag else desc
        frame.items.append(DataField(name=name, value=value, unit=unit, hex=final_hex, raw=oad_key, desc=final_desc))
        if time_tag is not None:
            frame.fields.append(DataField(name="时间标签", value=time_tag, hex=f"{time_tag:02X}", raw=time_tag))
