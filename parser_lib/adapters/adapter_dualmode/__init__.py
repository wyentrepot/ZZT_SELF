"""双模通信互联互通 4-3 应用层报文适配器。

通用报文头（4 字节）：
    报文端口号(1B) | 报文ID(2B, little-endian) | 控制字(1B) | 业务报文

注意：4-3 应用层通用头**不含 MAC/NA**；MAC 地址（6B）属于 4-2 数据链路层信封
（本期后置）。业务报文布局随报文ID 而异：
  - 抄表类（0x0001/0x0002/0x0003）：协议版本号(6)+报文头长度(6)+规约类型(4)+
    转发数据长度(12)+报文序号(2)+超时(1)+选项字(1)，DATA 自「报文头长度」偏移起，
    按规约类型递归解 645/698。
  - 其他报文：业务布局各异（注册/事件/台区含 MAC），本期展示原始业务字节并尽力
    扫描内嵌 645/698，MAC/NA 精确字段后置。
"""
import os

from parser_lib.core.adapter import ProtocolAdapter, ProtocolFrame, DataField, ExtractResult

_PORT_NAMES = {
    0x11: "普通业务",
    0x12: "升级业务",
    0x1A: "鉴权安全",
}

_SECURITY_NAMES = {
    0x0: "明文传输",
    0x1: "数据机密性保护",
    0x2: "数据完整性保护",
    0x3: "数据全面保护",
}

_MESSAGE_NAMES = {
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

_nested = None


def _get_nested():
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


def _display_le(data: bytes) -> str:
    return data[::-1].hex().upper()


def _message_parts(raw_msg_id: int):
    security = (raw_msg_id >> 12) & 0x0F
    msg_id = raw_msg_id & 0x0FFF
    return msg_id, security


def _valid_header(buf: bytes) -> bool:
    if len(buf) < 4 or buf[0] not in _PORT_NAMES:
        return False
    raw_msg_id = buf[1] | (buf[2] << 8)
    msg_id, security = _message_parts(raw_msg_id)
    if security not in _SECURITY_NAMES or msg_id not in _MESSAGE_NAMES:
        return False

    # A payload byte equal to 0x11/0x12/0x1A is not enough to establish a
    # dual-mode 4-3 envelope.  The application identifier must also belong to
    # the selected port; otherwise an inner 698 frame later in the payload can
    # turn arbitrary residual bytes into a false envelope.
    upgrade_ids = set(range(0x0030, 0x0037))
    if buf[0] == 0x12:
        return msg_id in upgrade_ids
    if buf[0] == 0x1A:
        return msg_id == 0x00A0
    return msg_id not in upgrade_ids and msg_id != 0x00A0


def _find_first_nested(buf: bytes, start: int = 4):
    d645, d698 = _get_nested()
    best = None
    for i in range(start, len(buf)):
        if buf[i] != 0x68:
            continue
        for adp in (d698, d645):
            res = adp.try_extract(buf[i:])
            if res is None:
                continue
            score = adp.confidence(res.raw)
            if score <= 0:
                continue
            candidate = (i, i + res.consumed, adp, res, score)
            if best is None or candidate[4] > best[4]:
                best = candidate
        if best is not None:
            return best
    return None


def _scan_nested(payload: bytes):
    d645, d698 = _get_nested()
    out = []
    i = 0
    while i < len(payload):
        best = None
        for adp in (d698, d645):
            res = adp.try_extract(payload[i:])
            if res is None:
                continue
            score = adp.confidence(res.raw)
            if score > 0 and (best is None or score > best[2]):
                best = (adp, res, score)
        if best is None:
            i += 1
            continue
        adp, res, _ = best
        out.append(adp.decode(res.raw))
        i += res.consumed
    return out


class DualMode43Adapter(ProtocolAdapter):
    protocol = "双模4-3"

    def try_extract(self, buf: bytes):
        if not _valid_header(buf):
            return None
        nested = _find_first_nested(buf, 4)
        if nested is None:
            return None
        _, end, _, _, _ = nested
        return ExtractResult(raw=buf[:end], consumed=end)

    def confidence(self, raw: bytes) -> float:
        if not _valid_header(raw):
            return 0.0
        nested = _find_first_nested(raw, 4)
        if nested is None:
            return 0.4
        raw_msg_id = raw[1] | (raw[2] << 8)
        msg_id, _ = _message_parts(raw_msg_id)
        return 1.0 if msg_id in _MESSAGE_NAMES else 0.85

    # 转发数据规约类型 → 名称（§3.4 表7）
    _PROTO_NAMES = {
        0: "透明传输",
        1: "DL/T645-1997",
        2: "DL/T645-2007",
        3: "DL/T698.45",
    }

    def decode(self, raw: bytes) -> ProtocolFrame:
        frame = ProtocolFrame(structure="双模4-3", raw_hex=raw.hex())
        port = raw[0]
        raw_msg_id = raw[1] | (raw[2] << 8)
        msg_id, security = _message_parts(raw_msg_id)
        control = raw[3]
        business = raw[4:]

        frame.fields.append(DataField(
            name="报文端口号",
            value=f"0x{port:02X} ({_PORT_NAMES.get(port, '未知端口')})",
            hex=f"{port:02X}",
            raw=port,
            desc="双模4-3通用报文头",
        ))
        frame.fields.append(DataField(
            name="报文ID",
            value=f"0x{msg_id:04X} ({_MESSAGE_NAMES.get(msg_id, '未知报文ID')})",
            hex=f"{raw[1]:02X}{raw[2]:02X}",
            raw=msg_id,
            desc=f"原始ID=0x{raw_msg_id:04X}; 安全机制={_SECURITY_NAMES.get(security)}",
        ))
        frame.fields.append(DataField(
            name="报文控制字",
            value=f"0x{control:02X}",
            hex=f"{control:02X}",
            raw=control,
            desc="默认0",
        ))

        if msg_id in (0x0001, 0x0002, 0x0003):
            self._parse_meter_business(frame, business, msg_id)
        else:
            self._parse_generic_business(frame, business)
        return frame

    def _parse_meter_business(self, frame, business, msg_id):
        """抄表类业务报文（终端/路由/并发抄表）头部解析 + DATA 递归。

        头部布局（§3.1/§3.2）：协议版本号(6)+报文头长度(6)+配置字/应答状态(4)+
        转发数据规约类型(4)+转发数据长度(12)+报文序号(2)+设备超时(1)+选项字(1)，
        DATA 自「报文头长度」偏移起。
        """
        if len(business) < 8:
            frame.warnings.append("业务报文过短，无法解析抄表报文头（需≥8字节）")
            if business:
                frame.items.append(DataField(
                    name="业务报文(原始)", value=business.hex(),
                    hex=business.hex(), raw=business.hex(),
                    desc="双模4-3抄表业务报文",
                ))
            return
        ver = business[0] & 0x3F
        header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
        config = (business[1] >> 4) & 0x0F
        proto_type = business[2] & 0x0F
        # 12-bit length is little-endian across the high nibble of byte 2
        # and byte 3: 0x83 0x06 => 0x068 (104), not 0x806.
        fwd_len = (business[3] << 4) | (business[2] >> 4)
        seq = business[4] | (business[5] << 8)
        timeout = business[6]
        option = business[7]

        frame.fields.append(DataField(
            name="协议版本号", value=ver, hex=f"{ver:02X}", raw=ver,
            desc="固定为1",
        ))
        frame.fields.append(DataField(
            name="报文头长度", value=header_len, hex=f"{header_len:02X}", raw=header_len,
            desc="业务报文头(不含DATA)字节数；DATA 自此偏移开始",
        ))
        frame.fields.append(DataField(
            name="配置字/应答状态", value=f"0x{config:01X}", hex=f"{config:01X}", raw=config,
            desc="并发抄表:未应答/否认重试标志+最大重试次数；上行:应答状态",
        ))
        frame.fields.append(DataField(
            name="转发数据规约类型",
            value=f"0x{proto_type} ({self._PROTO_NAMES.get(proto_type, '保留')})",
            hex=f"{proto_type:02X}", raw=proto_type,
        ))
        frame.fields.append(DataField(
            name="转发数据长度", value=fwd_len, hex=f"{fwd_len:03X}", raw=fwd_len,
            desc="文档表3存在OCR错位嫌疑，DATA 起点以报文头长度为准",
        ))
        frame.fields.append(DataField(
            name="报文序号", value=f"0x{seq:04X}",
            hex=f"{business[4]:02X}{business[5]:02X}", raw=seq,
        ))
        frame.fields.append(DataField(
            name="设备超时时间", value=f"{timeout * 100}ms",
            hex=f"{timeout:02X}", raw=timeout, desc="单位100ms",
        ))
        frame.fields.append(DataField(
            name="选项字", value=f"0x{option:02X}", hex=f"{option:02X}", raw=option,
        ))

        if header_len > len(business):
            frame.warnings.append(
                f"报文头长度({header_len})超出业务报文长度({len(business)})"
            )
            data = b""
        else:
            data = business[header_len:]

        if not data:
            return
        self._recurse_data(frame, data, proto_type, msg_id)

    def _recurse_data(self, frame, data, proto_type, msg_id):
        """按规约类型把 DATA 递归解成内嵌 645/698 帧。"""
        d645, d698 = _get_nested()
        if proto_type == 3:
            adp, label = d698, "698.45"
        elif proto_type in (1, 2):
            adp, label = d645, "645"
        else:
            frame.items.append(DataField(
                name="DATA(透明传输)", value=data.hex(), hex=data.hex(),
                raw=data.hex(), desc="规约类型0：透传，无内嵌协议帧",
            ))
            return

        out = []
        i = 0
        while i < len(data):
            res = adp.try_extract(data[i:])
            if res is None:
                i += 1
                continue
            if adp.confidence(res.raw) <= 0:
                i += 1
                continue
            out.append(adp.decode(res.raw))
            i += res.consumed

        for idx, pf in enumerate(out):
            summary = f"{pf.structure}"
            if pf.address:
                summary += f" · 地址{pf.address}"
            frame.items.append(DataField(
                name=f"DATA嵌套帧[{idx}] · {pf.structure}",
                value=summary, hex=pf.raw_hex, raw=pf.raw_hex,
                desc=f"规约类型{proto_type}({label})承载的内嵌帧",
            ))
            frame.nested.append(pf)
        if not out:
            frame.items.append(DataField(
                name="DATA(原始)", value=data.hex(), hex=data.hex(),
                raw=data.hex(), desc="未能按规约类型解出内嵌帧",
            ))

    def _parse_generic_business(self, frame, business):
        """非抄表类业务报文：展示原始字节并尽力扫描内嵌 645/698。

        注册(0x0011)/事件(0x0008)/台区户变(0x00A1)等业务布局各异且部分含 MAC，
        精确字段后置实现；本期仅展示原始业务报文并尽力递归。
        """
        if not business:
            return
        frame.items.append(DataField(
            name="业务报文(原始)", value=business.hex(), hex=business.hex(),
            raw=business.hex(),
            desc="非抄表类报文：业务布局各异，本期展示原始字节并扫描内嵌645/698",
        ))
        nested = _scan_nested(business)
        for idx, pf in enumerate(nested):
            summary = f"{pf.structure}"
            if pf.address:
                summary += f" · 地址{pf.address}"
            frame.items.append(DataField(
                name=f"嵌套帧[{idx}] · {pf.structure}",
                value=summary, hex=pf.raw_hex, raw=pf.raw_hex,
                desc="业务报文内递归解出",
            ))
            frame.nested.append(pf)
