"""Q/GDW 10376.2（原 1376.2）采集终端与通信模块接口适配器。

帧格式（AFN/PN-FN 信封，与 DL/T 698-2009 / 1376.1 同源）：
    68H | L(2B,LE) | 68H | AFN(1B) | SEQ(1B) |
    终端地址RTUA(6B,线上低字节在前) | 主站地址MSAA(1B) | 密码PW(2B) |
    用户数据(含 DAD + 嵌套的 645/698 单/多帧) | CS(1B) | 16H
- L = 除首尾两个 68H 之外的总字节数 = len(frame) - 2。
- CS = 从 L 起到 CS 前所有字节之和 mod 256。

关键职责（设计书 FR-07 / FR-13）：
- 解信封（AFN/SEQ/RTUA/PW）；
- 在用户数据区**递归调用 645/698 适配器**解出内部嵌套的单/多帧，
  内部帧的解码复用既有适配器（含字典语义），本适配器不做协议逻辑。
"""
import os

from parser_lib.core.adapter import ProtocolAdapter, ProtocolFrame, DataField, ExtractResult

# 应用层功能码 AFN（依据 Q/GDW 10376.2—2019 表7 应用层功能码定义，
# 代替旧 1376.2-2013/698 那套错配命名）
_AFN_NAMES = {
    0x00: "确认/否认", 0x01: "初始化", 0x02: "数据转发",
    0x03: "查询数据", 0x04: "链路接口检测", 0x05: "控制命令",
    0x06: "主动上报", 0x10: "路由查询", 0x11: "路由设置",
    0x12: "路由控制", 0x13: "路由数据转发", 0x14: "路由数据抄读",
    0x15: "文件传输", 0xF0: "内部调试", 0xF1: "并发抄表",
}

# 转发类 AFN：用户数据含 DAD(2B) + 嵌套的 645/698 帧
# 主要是 02H 数据转发（F1:转发通信协议数据帧），13H 路由数据转发亦携带 645 帧
_FORWARD_AFNS = {0x02, 0x05, 0x06, 0x10, 0x11, 0x13, 0xF1}
_NETWORK_AFNS = {0x02, 0x10, 0x11, 0x12, 0x13}
_ROUTE_AFNS = {0x10, 0x11, 0x12, 0x13}

_DUALMODE_PORT_NAMES = {
    0x11: "普通业务",
    0x12: "升级业务",
    0x1A: "鉴权安全",
}

_DUALMODE_SECURITY_NAMES = {
    0x0: "明文传输",
    0x1: "数据机密性保护",
    0x2: "数据完整性保护",
    0x3: "数据全面保护",
}

_DUALMODE_MESSAGE_NAMES = {
    0x0001: "终端主动抄表",
    0x0002: "路由主动抄表",
    0x0003: "终端主动并发抄表",
    0x0004: "校时",
    0x0006: "通信测试",
    0x0008: "事件上报",
    0x0011: "查询从节点主动注册",
    0x0012: "启动从节点主动注册",
    0x0013: "停止从节点主动注册",
    0x0020: "确认/否认",
    0x0030: "开始升级",
    0x0031: "停止升级",
    0x0032: "传输文件数据",
    0x0033: "传输文件数据(单播转本地广播)",
    0x0034: "查询站点升级状态",
    0x0035: "执行升级",
    0x0036: "查询站点信息",
    0x0040: "抄控器-CCO",
    0x0041: "抄控器数据透传串口转发",
    0x00A0: "鉴权安全",
    0x00A1: "台区户变关系识别",
    0x00A2: "查询ID信息",
    0x00A3: "精准校时",
    0x00A4: "配电信息上报",
}

_nested = None  # 懒加载的 (645适配器, 698适配器)


def _get_nested():
    """懒加载 645/698 适配器（带字典），用于递归解内部帧。"""
    global _nested
    if _nested is None:
        from parser_lib.core.metadata import MetadataStore
        from parser_lib.adapters.adapter_645 import DLT645Adapter
        from parser_lib.adapters.adapter_698 import DLT69845Adapter
        store = MetadataStore()
        here = os.path.dirname(__file__)
        store.load_protocol("645", os.path.join(here, "..", "adapter_645", "metadata"))
        store.load_protocol("698.45", os.path.join(here, "..", "adapter_698", "metadata"))
        _nested = (DLT645Adapter(metadata_store=store), DLT69845Adapter(metadata_store=store))
    return _nested


def build_frame(afn: int, seq: int, rtsa: bytes, msaa: int, pw: int,
               userdata: bytes, start: int = 0x68) -> bytes:
    """构造一个 1376.2 完整帧（自动计算 L 与 CS）。

    双 68 结构下 L = 整个帧长度 - 2（即除首尾两个 68H 之外的总字节数）。
    """
    # mid = 第 2 个 68H + 载荷（AFN/SEQ/RTUA/MSAA/PW/用户数据）
    mid = bytes([start, afn & 0xFF, seq & 0xFF]) + bytes(rtsa[:6]) \
        + bytes([msaa & 0xFF]) + bytes([(pw >> 8) & 0xFF, pw & 0xFF]) \
        + bytes(userdata)
    pre = bytes([start]) + mid        # 1st 68 + mid（不含 L 字段 / CS / 16H）
    n = len(pre) + 2 + 1 + 1       # +2(L字段) +1(CS) +1(16H)
    L = n - 2                       # 双 68 结构：L = n - 2
    body = bytes([start, L & 0xFF, (L >> 8) & 0xFF]) + mid
    cs = sum(body[1:]) % 256
    return body + bytes([cs, 0x16])


def _scan_nested(payload: bytes):
    """在用户数据区扫描并递归解码内部 645/698 帧（单/多帧）。"""
    d645, d698 = _get_nested()
    candidates = [d645, d698]
    out = []
    i = 0
    n = len(payload)
    while i < n:
        best, best_adp, best_s = None, None, -1.0
        for adp in candidates:
            res = adp.try_extract(payload[i:])
            if res is None:
                continue
            s = adp.confidence(res.raw)
            if s > best_s:
                best, best_adp, best_s = res, adp, s
        if best is None:
            i += 1
            continue
        pf = best_adp.decode(best.raw)
        out.append(pf)
        i += best.consumed
    return out


def _hex_be(data: bytes) -> str:
    """线上低字节在前的地址字段，展示为人读高字节在前。"""
    return data[::-1].hex().upper()


def _parse_dualmode_43(payload: bytes):
    """解析双模 4-3 通用报文头。

    结构：端口号(1B) | 报文ID(2B, little-endian) | 控制字(1B) | 业务报文。
    业务报文头中的源/目的 NA 当前按前 4 字节解析为 2B 源 NA + 2B 目的 NA；
    不同业务的后续字段先保留原始载荷，并继续扫描内嵌 645/698。
    """
    if len(payload) < 4 or payload[0] not in _DUALMODE_PORT_NAMES:
        return None

    port = payload[0]
    raw_msg_id = payload[1] | (payload[2] << 8)
    security = (raw_msg_id >> 12) & 0x0F
    msg_id = raw_msg_id & 0x0FFF
    if security not in _DUALMODE_SECURITY_NAMES:
        return None
    control = payload[3]
    business = payload[4:]

    items = [
        DataField(
            name="4-3报文端口号",
            value=f"0x{port:02X} ({_DUALMODE_PORT_NAMES[port]})",
            hex=f"{port:02X}",
            raw=port,
            desc="双模4-3通用报文头",
        ),
        DataField(
            name="4-3报文ID",
            value=f"0x{msg_id:04X} ({_DUALMODE_MESSAGE_NAMES.get(msg_id, '未知报文ID')})",
            hex=f"{payload[1]:02X}{payload[2]:02X}",
            raw=msg_id,
            desc=f"原始ID=0x{raw_msg_id:04X}; 安全机制={_DUALMODE_SECURITY_NAMES.get(security, f'保留({security})')}",
        ),
        DataField(
            name="4-3报文控制字",
            value=f"0x{control:02X}",
            hex=f"{control:02X}",
            raw=control,
            desc="默认0",
        ),
    ]

    body_payload = business
    if len(business) >= 4:
        src_na = business[:2]
        dst_na = business[2:4]
        body_payload = business[4:]
        items.extend([
            DataField(
                name="源NA",
                value=_hex_be(src_na),
                hex=src_na.hex(),
                raw=src_na.hex(),
                desc="双模4-3业务报文头网络地址",
            ),
            DataField(
                name="目的NA",
                value=_hex_be(dst_na),
                hex=dst_na.hex(),
                raw=dst_na.hex(),
                desc="双模4-3业务报文头网络地址",
            ),
        ])

    items.append(DataField(
        name="4-3业务载荷",
        value=body_payload.hex(),
        hex=body_payload.hex(),
        raw=body_payload.hex(),
        desc="源/目的NA之后的业务数据；按帧定界继续扫描内嵌645/698",
    ))

    nested = _scan_nested(body_payload)
    if not nested:
        nested = _scan_nested(business)

    return {
        "port": port,
        "message_id": msg_id,
        "security": security,
        "control": control,
        "business": business,
        "payload": body_payload,
        "items": items,
        "nested": nested,
    }


def _append_nested(frame: ProtocolFrame, nested: list, afn_name: str, source: str):
    for idx, pf in enumerate(nested):
        summary = f"{pf.structure}"
        if pf.address:
            summary += f" · 地址{pf.address}"
        frame.items.append(DataField(
            name=f"嵌套帧[{idx}] · {pf.structure}",
            value=summary,
            hex=pf.raw_hex,
            raw=pf.raw_hex,
            desc=f"{source}递归解出（AFN={afn_name}）",
        ))
        frame.nested.append(pf)


def _parse_network_payload(frame: ProtocolFrame, afn: int, afn_name: str, userdata: bytes) -> bool:
    """解析 10376.2 网络/路由相关 AFN 的用户数据。"""
    if afn not in _NETWORK_AFNS:
        return False

    frame.items.append(DataField(
        name="10376.2网络层分支",
        value=f"AFN=0x{afn:02X} ({afn_name})",
        hex=f"{afn:02X}",
        raw=afn,
        desc="当前优先实现数据转发与路由类AFN: 02H/10H/11H/12H/13H",
    ))

    data = userdata
    has_dad = False
    if len(userdata) >= 2:
        has_dad = True
        dad = userdata[:2]
        data = userdata[2:]
        frame.items.append(DataField(
            name="数据单元标识DAD",
            value=dad.hex().upper(),
            hex=dad.hex(),
            raw=dad.hex(),
            desc="10376.2用户数据前2字节，作为转发/路由类数据单元标识",
        ))

    if afn in _ROUTE_AFNS:
        frame.items.append(DataField(
            name="路由类AFN",
            value=afn_name,
            hex=data.hex(),
            raw=data.hex(),
            desc="路由查询/设置/控制/数据转发类载荷",
        ))
    elif afn == 0x02:
        frame.items.append(DataField(
            name="数据转发载荷",
            value=data.hex(),
            hex=data.hex(),
            raw=data.hex(),
            desc="AFN=02H数据转发载荷；可承载双模4-3或直接承载645/698",
        ))

    dualmode = _parse_dualmode_43(data)
    if dualmode is None and not has_dad:
        dualmode = _parse_dualmode_43(userdata)

    if dualmode:
        frame.items.extend(dualmode["items"])
        _append_nested(frame, dualmode["nested"], afn_name, "双模4-3业务载荷内")
        return True

    nested = _scan_nested(data)
    if nested:
        _append_nested(frame, nested, afn_name, "10376.2网络/转发载荷内")
        return True

    if data:
        frame.items.append(DataField(
            name="网络/路由载荷(原始)",
            value=data.hex(),
            hex=data.hex(),
            raw=data.hex(),
            desc="未识别到双模4-3通用头或内嵌645/698，保留原始字节",
        ))
    return True


class QGDW103762Adapter(ProtocolAdapter):
    protocol = "1376.2"

    def try_extract(self, buf: bytes):
        n = len(buf)
        if n < 14 or buf[0] != 0x68 or buf[3] != 0x68 or buf[-1] != 0x16:
            return None
        L = buf[1] | (buf[2] << 8)
        if L != n - 2:
            return None
        # 基本长度校验：AFN+SEQ+RTUA(6)+MSAA+PW+CS = 11
        if L < 12:
            return None
        return ExtractResult(raw=buf[:], consumed=n)

    def confidence(self, raw: bytes) -> float:
        # 1376.2 帧结构（FT1.2 AFN/PN-FN 信封）：68 | L(2B) | 68 | AFN | SEQ | RTUA(6) | MSAA | PW(2) | 用户数据 | CS | 16
        # 识别特征（索引 95 帧类别 · 10376.2 族）：
        #   1) 第二 68H 固定在 pos3（与 698 的第3字节是控制域、645 的 pos3 是地址字节天然区分）；
        #   2) AFN 位于 pos4，为合法应用层功能码（_AFN_NAMES）时更可信；
        #   3) CS 校验通过 → 最高置信。
        n = len(raw)
        if n < 14 or raw[0] != 0x68 or raw[3] != 0x68 or raw[-1] != 0x16:
            return 0.0
        L = raw[1] | (raw[2] << 8)
        if L != n - 2 or L < 12:
            return 0.0
        # 防御：若用户数据里嵌套 645/698 帧，信封层识别不受其影响；但此处仅鉴定信封本身。
        # 隔离 645：645 的 pos3 是地址字节，绝不可能是 0x68，上方已过滤。
        # 隔离 698：698 的 pos3 是控制域，绝不可能是 0x68，上方已过滤。
        afn = raw[4]
        afn_known = afn in _AFN_NAMES
        cs = sum(raw[1:-2]) % 256
        if cs == raw[-2]:
            # CS 通过：AFN 合法 → 1.0；AFN 不在字典（少数私有/扩展码）→ 0.9 仍高置信
            return 1.0 if afn_known else 0.9
        # CS 失败：结构成立但校验不过，给中等置信，交由上层比对其他适配器
        return 0.5

    def decode(self, raw: bytes) -> ProtocolFrame:
        frame = ProtocolFrame(structure="1376.2", raw_hex=raw.hex())
        warnings = []
        afn = raw[4]
        seq = raw[5]
        rtsa = raw[6:12]
        msaa = raw[12]
        pw = raw[13] | (raw[14] << 8)
        userdata = raw[15:-2]  # 去掉 CS(倒数第2) 与 16H(末)

        afn_name = _AFN_NAMES.get(afn, f"AFN-0x{afn:02X}")
        frame.fields.append(DataField(name="AFN", value=f"0x{afn:02X} ({afn_name})",
            hex=f"{afn:02X}", raw=afn))
        # 同步暴露到 items（ProtocolFrame 设计：DI/OAD/AFN 等数据标识归 items）
        frame.items.append(DataField(name="AFN", value=f"0x{afn:02X} ({afn_name})",
            hex=f"{afn:02X}", raw=afn, desc="应用层功能码"))
        frame.fields.append(DataField(name="SEQ", value=f"0x{seq:02X}",
            hex=f"{seq:02X}", raw=seq, desc=("分帧" if (seq & 0x80) else "完整")))
        # 终端地址线上低字节在前，展示时反转回人读顺序
        addr_show = rtsa[::-1].hex().upper()
        frame.fields.append(DataField(name="终端地址RTUA", value=addr_show,
            hex=rtsa.hex(), raw=rtsa.hex()))
        frame.fields.append(DataField(name="主站地址MSAA", value=f"0x{msaa:02X}",
            hex=f"{msaa:02X}", raw=msaa))
        frame.fields.append(DataField(name="密码PW", value=f"0x{pw:04X}",
            hex=f"{pw:04X}", raw=pw))

        cs = sum(raw[1:-2]) % 256
        cs_ok = (cs == raw[-2])
        frame.fields.append(DataField(name="校验和CS", value=f"0x{raw[-2]:02X}",
            hex=f"{raw[-2]:02X}", raw=raw[-2],
            desc="校验" + ("通过" if cs_ok else f"失败(计算0x{cs:02X})")))
        if not cs_ok:
            warnings.append(f"CS校验失败: 帧内0x{raw[-2]:02X} vs 计算0x{cs:02X}")

        handled = _parse_network_payload(frame, afn, afn_name, userdata)

        # 非网络/路由优先 AFN 保持原行为：递归解内部嵌套的 645/698 帧
        nested = [] if handled else _scan_nested(userdata)
        if nested:
            _append_nested(frame, nested, afn_name, "1376.2信封内")
        elif not handled:
            frame.items.append(DataField(name="用户数据(原始)", value=userdata.hex(),
                hex=userdata.hex(), raw=userdata.hex(),
                desc="未在其中识别到嵌套的 645/698 帧（或本帧不含转发载荷）"))

        frame.warnings = warnings
        return frame
