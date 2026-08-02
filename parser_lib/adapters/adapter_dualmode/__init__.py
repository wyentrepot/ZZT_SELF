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
    0x00E2: "采集任务配置",
    0x00E3: "采集任务数据读取",
    0x00E4: "采集任务数据上报",
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
        raw_msg_id = buf[1] | (buf[2] << 8)
        msg_id, _ = _message_parts(raw_msg_id)
        if msg_id == 0x00E4:
            # 分钟采集数据上报：主动上报格式按 4 + 报文头长度 + 转发报文长度
            # 完整消费，而不是停在第一条内嵌 645/698 帧处。
            business = buf[4:]
            if len(business) >= 8:
                header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
                start_flag = (business[1] >> 5) & 0x01
                if start_flag == 1 and header_len >= 4:
                    forward_len = int.from_bytes(business[6:8], "little")
                    end = 4 + header_len + forward_len
                    if 8 <= end <= len(buf):
                        return ExtractResult(raw=buf[:end], consumed=end)
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
        elif msg_id == 0x00E4:
            self._parse_minute_report(frame, business)
        elif msg_id == 0x00E2:
            self._parse_minute_config(frame, business)
        elif msg_id == 0x00E3:
            self._parse_minute_read(frame, business)
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
        # 抄表类方向位在选项字（byte7）bit0：0 下行、1 上行（非事件报文的 byte1 bit4）
        direction = business[7] & 0x01

        frame.fields.append(DataField(
            name="协议版本号", value=ver, hex=f"{ver:02X}", raw=ver,
            desc="固定为1",
        ))
        frame.fields.append(DataField(
            name="报文头长度", value=header_len, hex=f"{header_len:02X}", raw=header_len,
            desc="业务报文头(不含DATA)字节数；DATA 自此偏移开始",
        ))
        frame.fields.append(DataField(
            name="方向", value="下行" if direction == 0 else "上行",
            hex=f"{direction:02X}", raw=direction,
            desc="抄表类方向位在选项字(byte7)bit0：0下行/1上行",
        ))
        frame.fields.append(DataField(
            name="配置字/应答状态", value=f"0x{config:01X}", hex=f"{config:01X}", raw=config,
            desc="下行:未应答/否认重试标志+最大重试次数；上行:应答状态",
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

    def _append(self, frame, name, value, hex_str, raw, desc=""):
        frame.fields.append(DataField(
            name=name, value=value, hex=hex_str, raw=raw, desc=desc,
        ))

    def _parse_minute_report(self, frame, business):
        """采集任务数据上报（0x00E4）主动上报格式解析。

        主动上报头（8 字节）：协议版本号(6)+报文头长度(6)+方向位(1)+启动位(1)+
        报文序号(32)+转发报文长度(16)；转发报文内容为分钟级数据：前导字段(1)+
        源MAC(6)+任务号(1)+协议类型/电表类型/响应结果(1)+冻结时刻(6)+
        报文条数(1)+数据长度(16)+数据内容。

        并发抄读格式（格式一，启动位=0）业务布局不同且无转发报文长度，本期
        仅展示原始业务报文并扫描内嵌帧（见下方 start_flag 分支）。
        """
        if len(business) < 2:
            frame.warnings.append("业务报文过短，无法读取分钟采集上报头（需≥2字节）")
            if business:
                frame.items.append(DataField(
                    name="业务报文(原始)", value=business.hex(), hex=business.hex(),
                    raw=business.hex(), desc="双模4-3分钟采集上报业务报文",
                ))
            return

        ver = business[0] & 0x3F
        header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
        direction = (business[1] >> 4) & 0x01
        start_flag = (business[1] >> 5) & 0x01

        if start_flag != 1:
            # 并发抄读格式（格式一）业务布局不同且无转发报文长度，切帧仍按
            # 内嵌帧扫描（与 try_extract 一致）；本期仅展示原始业务报文并
            # 扫描内嵌帧，不展开业务字段。
            frame.warnings.append(
                "并发抄读格式（启动位=0）本期仅展示原始业务报文并扫描内嵌帧"
            )
            self._parse_generic_business(frame, business)
            return

        if len(business) < 8:
            frame.warnings.append("业务报文过短，无法解析主动上报头（需≥8字节）")
            if business:
                frame.items.append(DataField(
                    name="业务报文(原始)", value=business.hex(), hex=business.hex(),
                    raw=business.hex(), desc="双模4-3分钟采集上报业务报文",
                ))
            return

        sequence = int.from_bytes(business[2:6], "little")
        forward_len = int.from_bytes(business[6:8], "little")

        self._append(frame, "协议版本号", ver, f"{ver:02X}", ver, "固定为1")
        self._append(
            frame, "分钟采集类型", "主动上报", f"{start_flag:02X}",
            "主动上报", "启动位为1时按主动上报格式展开",
        )
        self._append(frame, "报文头长度", header_len, f"{header_len:02X}", header_len,
                     "业务报文头(不含转发内容)字节数")
        self._append(frame, "方向", "上行" if direction else "下行",
                     f"{direction:02X}", direction)
        self._append(frame, "启动位", start_flag, f"{start_flag:02X}", start_flag,
                     "1：主动上报格式")
        self._append(frame, "报文序号", f"0x{sequence:08X}",
                     business[2:6].hex().upper(), sequence,
                     "与下行报文序号保持一致")
        self._append(frame, "转发报文长度", forward_len, f"{forward_len:04X}",
                     forward_len, "分钟级报文相关")

        if header_len > len(business):
            frame.warnings.append(
                f"报文头长度({header_len})超出业务报文长度({len(business)})"
            )
            forwarded = b""
        else:
            forwarded = business[header_len:]

        if len(forwarded) < forward_len:
            frame.warnings.append(
                f"转发报文长度({forward_len})超出可用字节({len(forwarded)})"
            )
            forwarded = forwarded[:forward_len]
        else:
            forwarded = forwarded[:forward_len]

        if not forwarded:
            return

        self._append(frame, "前导字段", f"0x{forwarded[0]:02X}", f"{forwarded[0]:02X}",
                     forwarded[0],
                     "源MAC前的未文档化字节，命名待协议文档确认")
        if len(forwarded) < 7:
            frame.warnings.append("转发报文不足7字节，缺少源MAC")
            return
        src_mac = forwarded[1:7]
        self._append(frame, "源MAC地址", ":".join(f"{b:02X}" for b in src_mac),
                     src_mac.hex().upper(), src_mac.hex().upper(),
                     "模块MAC（转发内容源MAC）")
        if len(forwarded) < 9:
            frame.warnings.append("转发报文不足9字节，缺少任务号/协议类型")
            return
        task_no = forwarded[7]
        packed = forwarded[8]
        proto_type = packed & 0x07
        meter_type = (packed >> 3) & 0x03
        result = (packed >> 5) & 0x07
        self._append(frame, "任务号", task_no, f"{task_no:02X}", task_no,
                     "采集任务号")
        self._append(frame, "协议类型", proto_type,
                     f"{proto_type:02X} ({self._PROTO_NAMES.get(proto_type, '保留')})",
                     proto_type)
        self._append(frame, "电表类型", meter_type, f"{meter_type:02X}", meter_type,
                     "0：单相；1：三相；2：其他")
        self._append(frame, "响应结果", result, f"{result:02X}", result,
                     "0：响应成功；1：任务不存在；2：无冻结数据；3：其他原因")
        if len(forwarded) < 15:
            frame.warnings.append("转发报文不足15字节，缺少冻结时刻")
            return
        freeze = forwarded[9:15]
        freeze_str = "-".join(f"{b:02X}" for b in freeze)
        self._append(frame, "冻结时刻", freeze_str, freeze.hex().upper(),
                     freeze.hex().upper(),
                     "冻结时间点（YY-MM-DD-HH-MM-SS）")
        if len(forwarded) < 18:
            frame.warnings.append("转发报文不足18字节，缺少报文条数/数据长度")
            return
        count = forwarded[15]
        data_len = int.from_bytes(forwarded[16:18], "little")
        self._append(frame, "上报数量", count, f"{count:02X}", count,
                     "645协议下配置多个DI时回复多条报文")
        self._append(frame, "数据长度", data_len, f"{data_len:04X}", data_len)
        data = forwarded[18:18 + data_len]
        if len(data) < data_len:
            frame.warnings.append(
                f"数据长度({data_len})超出可用字节({len(data)})"
            )

        if not data:
            return
        nested = _scan_nested(data)
        for idx, pf in enumerate(nested):
            summary = f"{pf.structure}"
            if pf.address:
                summary += f" · 地址{pf.address}"
            frame.items.append(DataField(
                name=f"分钟数据嵌套帧[{idx}] · {pf.structure}",
                value=summary, hex=pf.raw_hex, raw=pf.raw_hex,
                desc="分钟级数据区内递归解出",
            ))
            frame.nested.append(pf)
        if not nested:
            frame.items.append(DataField(
                name="分钟数据(原始)", value=data.hex(), hex=data.hex(),
                raw=data.hex(), desc="未能按协议类型解出内嵌帧",
            ))

    def _parse_minute_config(self, frame, business):
        """采集任务配置（0x00E2）下行报文解析。

        业务头：协议版本号(6)+报文头长度(6)+保留(4)+报文序号(32)+目的MAC(48)+
        任务号(8)+启动/删除标志(1)+协议类型(3)+表类型(2)+保留(2)+
        采集周期(8)+数据项个数n(8)+n×(数据项标识(32)+回复长度(8))。
        """
        if len(business) < 16:
            frame.warnings.append("业务报文过短，无法解析采集任务配置（需≥16字节）")
            return
        ver = business[0] & 0x3F
        header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
        sequence = int.from_bytes(business[2:6], "little")
        self._append(frame, "协议版本号", ver, f"{ver:02X}", ver, "固定为1")
        self._append(frame, "报文头长度", header_len, f"{header_len:02X}", header_len)
        self._append(frame, "方向", "下行", "00", 0, "CCO → STA")
        self._append(frame, "报文序号", f"0x{sequence:08X}",
                     business[2:6].hex().upper(), sequence)
        if len(business) < 13:
            frame.warnings.append("业务报文不足13字节，缺少目的MAC/任务号")
            return
        dst_mac = business[6:12]
        self._append(frame, "目的MAC地址", ":".join(f"{b:02X}" for b in dst_mac),
                     dst_mac.hex().upper(), dst_mac.hex().upper())
        task_no = business[12]
        flag = business[13]
        period = business[14]
        item_count = business[15]
        self._append(frame, "任务号", task_no, f"{task_no:02X}", task_no,
                     "采集任务号0~15有效，0xFF代表全部任务")
        self._append(
            frame, "启动/删除标志", "启用" if flag & 0x01 else "删除",
            f"{flag & 0x01:02X}", flag & 0x01,
        )
        proto_type = (flag >> 1) & 0x07
        meter_type = (flag >> 4) & 0x03
        self._append(frame, "协议类型", proto_type,
                     f"{proto_type:02X} ({self._PROTO_NAMES.get(proto_type, '保留')})",
                     proto_type)
        self._append(frame, "表类型", meter_type, f"{meter_type:02X}", meter_type,
                     "0x00：单相表；0x01：三相表；0x02：其他表计")
        self._append(frame, "采集周期", period, f"{period:02X}", period, "单位分钟")
        self._append(frame, "数据项个数", item_count, f"{item_count:02X}", item_count)
        # 数据项：n × (标识4B + 回复长度1B)
        items_start = 16
        for idx in range(item_count):
            offset = items_start + idx * 5
            if offset + 5 > len(business):
                frame.warnings.append(
                    f"数据项[{idx}]越界，已解析 {idx} 项"
                )
                break
            di_id = business[offset:offset + 4]
            di_len = business[offset + 4]
            self._append(frame, f"数据项{idx}标识", di_id.hex().upper(),
                         di_id.hex().upper(), di_id.hex().upper())
            self._append(frame, f"数据项{idx}回复长度", di_len, f"{di_len:02X}",
                         di_len)

    def _parse_minute_read(self, frame, business):
        """采集任务数据读取（0x00E3）下行报文解析。

        业务头：协议版本号(6)+报文头长度(6)+方向位(1)+保留(3)+报文序号(32)+
        协议类型(4)+电表类型(1)+保留(3)+目的MAC(48)+任务号(8)+冻结时刻(48)。
        """
        if len(business) < 20:
            frame.warnings.append("业务报文过短，无法解析采集任务数据读取（需≥20字节）")
            return
        ver = business[0] & 0x3F
        header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
        direction = (business[1] >> 4) & 0x01
        sequence = int.from_bytes(business[2:6], "little")
        self._append(frame, "协议版本号", ver, f"{ver:02X}", ver, "固定为1")
        self._append(frame, "报文头长度", header_len, f"{header_len:02X}", header_len)
        self._append(frame, "方向", "上行" if direction else "下行",
                     f"{direction:02X}", direction, "0：下行；1：上行")
        self._append(frame, "报文序号", f"0x{sequence:08X}",
                     business[2:6].hex().upper(), sequence)
        if len(business) < 7:
            frame.warnings.append("业务报文不足7字节，缺少协议类型/目的MAC")
            return
        proto_type = business[6] & 0x0F
        meter_type = (business[6] >> 4) & 0x01
        self._append(frame, "协议类型", proto_type,
                     f"{proto_type:02X} ({self._PROTO_NAMES.get(proto_type, '保留')})",
                     proto_type)
        self._append(frame, "电表类型", meter_type, f"{meter_type:02X}", meter_type,
                     "0：单相；1：三相")
        if len(business) < 13:
            frame.warnings.append("业务报文不足13字节，缺少目的MAC/任务号")
            return
        dst_mac = business[7:13]
        self._append(frame, "目的MAC地址", ":".join(f"{b:02X}" for b in dst_mac),
                     dst_mac.hex().upper(), dst_mac.hex().upper())
        task_no = business[13]
        self._append(frame, "任务号", task_no, f"{task_no:02X}", task_no, "采集任务号")
        if len(business) < 20:
            frame.warnings.append("业务报文不足20字节，缺少冻结时刻")
            return
        freeze = business[14:20]
        freeze_str = "-".join(f"{b:02X}" for b in freeze)
        self._append(frame, "冻结时刻", freeze_str, freeze.hex().upper(),
                     freeze.hex().upper(),
                     "冻结时间点（YY-MM-DD-HH-MM-SS）")
