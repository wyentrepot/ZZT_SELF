"""Q/GDW 10376.2—2019 集中器本地通信模块接口适配器（单 68 标准帧）。

帧格式（FT1.2 异步式传输帧，Q/GDW 10376.2—2019 / 1376.2 同构）：
    68H | L(2B,BIN) | C(1B) | 用户数据(L1) | CS(1B) | 16H
其中用户数据 = R(信息域) | A(地址域) | AFN(应用功能码) | 应用数据。

- L = 用户数据长度 L1 + 6（起始68 + 长度2 + 控制域1 + 校验和1 + 结束16 = 6 固定字节）。
- C 控制域：D7=DIR(0下行/1上行)，D6=PRM(0从动站/1启动站)，D5~D0=通信方式。
- R 信息域：下行 6B / 上行 6B（含路由/信道/信号品质/报文序列号等）。
- A 地址域：源A1(6B BCD) + 中继A2(6B×中继级别) + 目的A3(6B BCD)；通信模块标识=0 时无地址域。
- AFN/Fn 应用层：AFN 1B + 数据单元标识 DT1/DT2（Fn 映射）+ 数据单元。
- CS = 控制域 C 与用户数据区所有字节的 8 位算术和（模 256，忽略进位）。

设计（ADR-44）：从双 68 信封彻底重构为单 68 标准帧，根除"应用层正确、
链路层错配"的历史问题。内嵌 645/698 继续复用现有适配器递归解析。
"""
import os
from typing import Optional

from parser_lib.core.adapter import ProtocolAdapter, ProtocolFrame, DataField, ExtractResult

# ---------------------------------------------------------------------------
# 常量表
# ---------------------------------------------------------------------------

# 应用层功能码 AFN（Q/GDW 10376.2—2019 表7）
_AFN_NAMES = {
    0x00: "确认/否认", 0x01: "初始化", 0x02: "数据转发",
    0x03: "查询数据", 0x04: "链路接口检测", 0x05: "控制命令",
    0x06: "主动上报", 0x10: "路由查询", 0x11: "路由设置",
    0x12: "路由控制", 0x13: "路由数据转发", 0x14: "路由数据抄读",
    0x15: "文件传输", 0xF0: "内部调试", 0xF1: "并发抄表",
}

# 通信方式（控制域 D5~D0）
_COMM_MODE_NAMES = {
    0: "保留", 1: "集中式路由载波通信", 2: "分布式路由载波通信",
    3: "HPLC载波通信", 10: "微功率无线通信", 20: "以太网通信",
}

# 通信协议类型（数据转发/上报通用）
_PROTOCOL_TYPE_NAMES = {
    0x00: "透明传输", 0x01: "DL/T 645—1997", 0x02: "DL/T 645—2007",
    0x03: "DL/T 698.45", 0x04: "从节点停复电事件", 0x05: "台区改切拒绝节点上报",
}

# 否认错误状态字（AFN=00H F2）
_DENY_ERROR_NAMES = {
    0: "通信超时", 1: "无效数据单元", 2: "长度错", 3: "校验错误",
    4: "信息类不存在", 5: "格式错误", 6: "表号重复", 7: "表号不存在",
    8: "电表应用层无应答", 9: "主节点忙", 10: "主节点不支持此命令",
    11: "从节点不应答", 12: "从节点不在网内", 109: "超过最大并发数",
    110: "超过单个帧最大允许的电表协议报文条数", 111: "正在抄读该表",
}

# 从节点设备类型（AFN=06H F5）
_DEVICE_TYPE_NAMES = {
    0x00: "采集器", 0x01: "电能表", 0x02: "HPLC通信单元",
    0x03: "窄带载波通信单元", 0x04: "微功率无线通信单元",
    0x05: "微功率+HPLC通信单元", 0x06: "微功率+窄带通信单元",
}

# 嵌套适配器懒加载缓存
_nested = None


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


# ---------------------------------------------------------------------------
# Fn ↔ DT 映射（Q/GDW 10376.2 数据单元标识）
# 数据单元标识 DT1(1B) + DT2(1B)：DT2=信息类组，DT1 按位表示组内 8 种信息类型。
# Fn 映射：Fn = DT2*8 + base，其中 base 由 DT1 的置位位决定（D0→F1 ... D7→F8）。
# ---------------------------------------------------------------------------
_DT1_TO_BASE = {1: 1, 2: 2, 4: 3, 8: 4, 16: 5, 32: 6, 64: 7, 128: 8}


def fn_to_dt(fn: int) -> tuple:
    """Fn 码 -> (DT1, DT2)。"""
    fn = int(fn) & 0xFF
    if fn < 1:
        fn = 1
    dt2 = (fn - 1) >> 3
    rem = fn & 0x07
    dt1 = 1 << (rem - 1) if rem != 0 else 128
    return dt1, dt2


def dt_to_fn(dt1: int, dt2: int) -> int:
    """(DT1, DT2) -> Fn 码；无法识别返回 0。"""
    base = _DT1_TO_BASE.get(int(dt1))
    if base is None:
        return 0
    return int(dt2) * 8 + base


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _bcd_to_str(data: bytes) -> str:
    """BCD 字节 -> 数字字符串（每字节高低半字节各一数字）。"""
    return "".join(f"{(b >> 4) & 0xF}{b & 0xF}" for b in data)


def _str_to_bcd(s: str, nbytes: int) -> bytes:
    """数字字符串 -> BCD 字节（不足左补0，超长截断）。"""
    s = s.strip().replace("-", "")
    if len(s) < nbytes * 2:
        s = "0" * (nbytes * 2 - len(s)) + s
    s = s[: nbytes * 2]
    out = bytearray()
    for i in range(0, len(s), 2):
        out.append((int(s[i], 16) << 4) | int(s[i + 1], 16))
    return bytes(out)


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


def _append_nested(frame: ProtocolFrame, nested: list, source: str):
    for idx, pf in enumerate(nested):
        summary = f"{pf.structure}"
        if pf.address:
            summary += f" · 地址{pf.address}"
        frame.items.append(DataField(
            name=f"嵌套帧[{idx}] · {pf.structure}",
            value=summary,
            hex=pf.raw_hex,
            raw=pf.raw_hex,
            desc=f"{source}递归解出",
        ))
        frame.nested.append(pf)

# ---------------------------------------------------------------------------
# 控制域 C / 信息域 R / 地址域 A 编解码
# ---------------------------------------------------------------------------

def _pack_control(direction: str, prm: Optional[int], comm_mode: int) -> int:
    """打包控制域 C。direction: "down"(0) / "up"(1)。"""
    dir_bit = 1 if direction == "up" else 0
    if prm is None:
        prm = 0 if direction == "up" else 1  # 上行=从动站(0)，下行=启动站(1)
    return (dir_bit << 7) | ((prm & 0x01) << 6) | (comm_mode & 0x3F)


def _parse_control(c: int) -> dict:
    """解析控制域 C。"""
    return {
        "dir": (c >> 7) & 0x01,
        "prm": (c >> 6) & 0x01,
        "comm_mode": c & 0x3F,
        "direction": "up" if (c >> 7) & 0x01 else "down",
        "comm_mode_name": _COMM_MODE_NAMES.get(c & 0x3F, f"保留({c & 0x3F})"),
    }


def _pack_info(direction: str, info: dict) -> bytes:
    """打包信息域 R（下行 6B / 上行 6B）。缺省字段用 0。"""
    if direction == "up":
        b0 = ((info.get("relay_level", 0) & 0x0F) << 4) \
             | ((info.get("module_id", 0) & 0x03) << 2) \
             | (info.get("route_flag", 0) & 0x01)
        b1 = info.get("channel", 0) & 0x0F
        b2 = ((info.get("meter_channel", 0) & 0x0F) << 4) | (info.get("phase", 0) & 0x0F)
        b3 = ((info.get("sig_quality", 0) & 0x0F) << 4) | (info.get("sig_quality", 0) & 0x0F)
        b4 = info.get("event_flag", 0) & 0x01
        b5 = info.get("seq", 0) & 0xFF
        return bytes([b0, b1, b2, b3, b4, b5])
    b0 = ((info.get("relay_level", 0) & 0x0F) << 4) \
         | ((info.get("conflict_detect", 0) & 0x01) << 3) \
         | ((info.get("module_id", 0) & 0x03) << 1) \
         | (info.get("sub_node", 0) & 0x01)
    b1 = ((info.get("ecc", 0) & 0x0F) << 4) | (info.get("channel", 0) & 0x0F)
    b2 = info.get("expect_reply_len", 0) & 0xFF
    rate = info.get("rate", 0) & 0x7FFF
    rate_unit = info.get("rate_unit", 0) & 0x01
    b3 = ((rate_unit & 0x01) << 7) | ((rate >> 8) & 0x7F)
    b4 = rate & 0xFF
    b5 = info.get("seq", 0) & 0xFF
    return bytes([b0, b1, b2, b3, b4, b5])


def _parse_info(direction: str, info: bytes) -> dict:
    """解析信息域 R。"""
    if len(info) < 6:
        info = bytes(info) + b"\x00" * (6 - len(info))
    if direction == "up":
        b0, b1, b2, b3, b4, b5 = info[:6]
        return {
            "relay_level": (b0 >> 4) & 0x0F,
            "module_id": (b0 >> 2) & 0x03,
            "route_flag": b0 & 0x01,
            "channel": b1 & 0x0F,
            "meter_channel": (b2 >> 4) & 0x0F,
            "phase": b2 & 0x0F,
            "sig_quality": (b3 >> 4) & 0x0F,
            "event_flag": b4 & 0x01,
            "seq": b5,
        }
    b0, b1, b2, b3, b4, b5 = info[:6]
    rate = ((b3 & 0x7F) << 8) | b4
    return {
        "relay_level": (b0 >> 4) & 0x0F,
        "conflict_detect": (b0 >> 3) & 0x01,
        "module_id": (b0 >> 1) & 0x03,
        "sub_node": b0 & 0x01,
        "ecc": (b1 >> 4) & 0x0F,
        "channel": b1 & 0x0F,
        "expect_reply_len": b2,
        "rate_unit": (b3 >> 7) & 0x01,
        "rate": rate,
        "seq": b5,
    }


def _pack_address(addr: dict) -> bytes:
    """打包地址域 A。addr: {src, relay:[...], dst}，均为 BCD 数字字符串。"""
    out = bytearray()
    src = addr.get("src") or ""
    if src:
        out += _str_to_bcd(src, 6)
    for r in (addr.get("relay") or []):
        out += _str_to_bcd(str(r), 6)
    dst = addr.get("dst") or ""
    if dst:
        out += _str_to_bcd(str(dst), 6)
    return bytes(out)


def _parse_address(data: bytes) -> dict:
    """解析地址域 A（可变长，按 6B 切）。返回 {src, relay[], dst}。"""
    n = len(data)
    if n == 0:
        return {"src": "", "relay": [], "dst": "", "has_address": False}
    src = _bcd_to_str(data[0:6])
    relay = []
    i = 6
    while i + 6 < n:
        relay.append(_bcd_to_str(data[i:i + 6]))
        i += 6
    dst = _bcd_to_str(data[i:i + 6]) if i < n else ""
    return {"src": src, "relay": relay, "dst": dst, "has_address": True}

# ---------------------------------------------------------------------------
# 应用数据解析（按 AFN/Fn 解析数据单元，填入 items）
# ---------------------------------------------------------------------------

def _app_items(afn: int, fn: int, data: bytes) -> list:
    """按 AFN/Fn 解析应用数据单元为 DataField 列表（可空）。"""
    items = []

    def add(name, value, hex_str="", desc=""):
        items.append(DataField(name=name, value=value, hex=hex_str,
                               raw=value, desc=desc))

    # AFN=00H 确认/否认
    if afn == 0x00:
        if fn == 1 and len(data) >= 6:
            ch_status = data[0:4]
            wait = (data[4] << 8) | data[5]
            cmd_state = "已处理" if (ch_status[0] >> 7) & 1 else "未处理"
            add("命令状态", cmd_state, ch_status.hex(), "确认/否认帧")
            add("信道状态", ch_status.hex(), ch_status.hex(), "1~31信道忙闲")
            add("等待时间", f"{wait}s", f"{wait:04X}", "等待时间(秒)")
        elif fn == 2 and len(data) >= 1:
            err = data[0]
            add("错误状态字", f"{err} ({_DENY_ERROR_NAMES.get(err, '保留')})",
                f"{err:02X}", "否认原因")
    # AFN=01H 初始化：无数据单元
    elif afn == 0x02:
        # 数据转发 F1：通信协议类型(1B) + 报文长度L(1B) + 报文内容(L)
        if fn == 1 and len(data) >= 2:
            proto = data[0]
            plen = data[1]
            payload = data[2:2 + plen]
            add("通信协议类型",
                f"0x{proto:02X} ({_PROTOCOL_TYPE_NAMES.get(proto, '保留')})",
                f"{proto:02X}", "转发帧承载的协议类型")
            add("报文长度", f"{plen}B", f"{plen:02X}", "转发报文长度")
            add("转发报文", payload.hex(), payload.hex(),
                "转发内容（内嵌645/698，递归解析）")
    # AFN=03H 查询数据
    elif afn == 0x03:
        if fn == 1 and len(data) >= 9:
            add("厂商代码", data[0:2].decode("ascii", "replace"), data[0:2].hex())
            add("芯片代码", data[2:4].decode("ascii", "replace"), data[2:4].hex())
            day, month, year = data[4], data[5], data[6]
            add("版本日期", f"20{year:02X}年{month:02X}月{day:02X}日",
                f"{data[4]:02X} {data[5]:02X} {data[6]:02X}")
            ver = data[7:9]
            add("版本", f"V{ver[0]:02X}.{ver[1]:02X}", ver.hex())
        elif fn == 2 and len(data) >= 1:
            add("噪声强度", f"{data[0] & 0x0F} (0~15)", f"{data[0]:02X}")
        elif fn == 4 and len(data) >= 6:
            add("主节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
        elif fn == 5 and len(data) >= 4:
            add("主节点状态字", data[0:2].hex(), data[0:2].hex())
            add("通信速率", f"{data[2]:02X} {data[3]:02X}", data[2:4].hex())
        elif fn == 8 and len(data) >= 2:
            add("无线信道组", f"{data[0]}", f"{data[0]:02X}")
            add("无线主节点发射功率", f"{data[1]}", f"{data[1]:02X}")
        elif fn == 10 and len(data) >= 6:
            add("本地通信模式字", data[0:6].hex(), data[0:6].hex())
    # AFN=05H 控制命令
    elif afn == 0x05:
        if fn == 1 and len(data) >= 6:
            add("主节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
        elif fn == 2 and len(data) >= 1:
            add("事件上报状态", "允许" if data[0] else "禁止", f"{data[0]:02X}")
        elif fn == 3 and len(data) >= 2:
            ctrl = data[0]
            plen = data[1]
            payload = data[2:2 + plen]
            add("控制字", f"0x{ctrl:02X} ({_PROTOCOL_TYPE_NAMES.get(ctrl, '相位识别')})",
                f"{ctrl:02X}", "广播控制字")
            add("报文长度", f"{plen}B", f"{plen:02X}")
            add("广播报文", payload.hex(), payload.hex())
        elif fn == 4 and len(data) >= 1:
            add("最大超时时间", f"{data[0]}s", f"{data[0]:02X}")
        elif fn == 6 and len(data) >= 1:
            add("台区识别使能", "允许" if data[0] else "禁止", f"{data[0]:02X}")
        elif fn == 101 and len(data) >= 6:
            sec, minute, hour, day, month, year = data[0:6]
            add("中心节点时间",
                f"20{year:02X}-{month:02X}-{day:02X} {hour:02X}:{minute:02X}:{sec:02X}",
                data[0:6].hex())
    # AFN=06H 主动上报
    elif afn == 0x06:
        if fn == 1 and len(data) >= 1:
            n = data[0]
            add("上报从节点数量", f"{n}", f"{n:02X}")
            pos = 1
            for i in range(n):
                if pos + 9 > len(data):
                    break
                add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                    data[pos:pos + 6].hex())
                add(f"从节点{i + 1}协议类型",
                    f"0x{data[pos + 6]:02X} ({_PROTOCOL_TYPE_NAMES.get(data[pos + 6], '保留')})",
                    f"{data[pos + 6]:02X}")
                seq = (data[pos + 7] << 8) | data[pos + 8]
                add(f"从节点{i + 1}序号", f"{seq}", f"{seq:04X}")
                pos += 9
        elif fn == 2 and len(data) >= 6:
            seq = (data[0] << 8) | data[1]
            proto = data[2]
            up_len = (data[3] << 8) | data[4]
            plen = data[5]
            payload = data[6:6 + plen]
            add("从节点序号", f"{seq}", f"{seq:04X}")
            add("通信协议类型",
                f"0x{proto:02X} ({_PROTOCOL_TYPE_NAMES.get(proto, '保留')})", f"{proto:02X}")
            add("上行时长", f"{up_len}s", f"{up_len:04X}")
            add("报文长度", f"{plen}B", f"{plen:02X}")
            add("上报报文", payload.hex(), payload.hex())
        elif fn == 3 and len(data) >= 1:
            t = data[0]
            names = {1: "抄表任务结束", 2: "搜表任务结束", 3: "台区识别任务结束"}
            add("路由工作任务变动类型", names.get(t, f"保留({t})"), f"{t:02X}")
    # AFN=10H 路由查询
    elif afn == 0x10:
        if fn == 1 and len(data) >= 4:
            add("从节点总数量", f"{(data[0] << 8) | data[1]}", data[0:2].hex())
            add("路由支持最大从节点数量", f"{(data[2] << 8) | data[3]}", data[2:4].hex())
        elif fn == 2 and len(data) >= 3:
            start = (data[0] << 8) | data[1]
            n = data[2]
            add("从节点起始序号", f"{start}", f"{start:04X}")
            add("从节点数量", f"{n}", f"{n:02X}")
    return items


def _scan_payload_nested(payload: bytes):
    """对数据转发/上报的报文内容递归解内嵌 645/698。"""
    return _scan_nested(payload)

# ---------------------------------------------------------------------------
# 构帧（单 68 标准帧）
# ---------------------------------------------------------------------------

def build_frame(
    direction: str = "down",
    comm_mode: int = 3,
    prm: Optional[int] = None,
    info: Optional[dict] = None,
    address: Optional[dict] = None,
    afn: int = 0x03,
    fn: int = 1,
    appdata: bytes = b"",
    end: int = 0x16,
) -> bytes:
    """构造一个 Q/GDW 10376.2 单 68 标准帧（自动计算 L 与 CS）。

    direction: "down"(DIR=0) | "up"(DIR=1)。
    comm_mode: 通信方式（1集中式/2分布式/3HPLC/10微功率/20以太网），默认 3 HPLC。
    info: 信息域 R 字段 dict（缺省全 0，seq 默认 1）。
    address: 地址域 A dict {src, relay[], dst}（BCD 字符串）；缺省=无地址域。
    afn/fn: 应用功能码与信息类标识。
    appdata: 应用数据单元原始字节（或内嵌 645/698 帧 bytes）。
    """
    c = _pack_control(direction, prm, comm_mode)
    info = dict(info or {})
    # 地址域 A 存在与否由通信模块标识 module_id 决定（0=无地址域，1=有地址域）。
    # 若调用方提供了 address 却未显式指定 module_id，自动置 1。
    addr_bytes = _pack_address(address or {})
    if addr_bytes and info.get("module_id") is None:
        info["module_id"] = 1
    info_bytes = _pack_info(direction, info)
    dt1, dt2 = fn_to_dt(fn)
    # 用户数据 = R + A + AFN + DT1 + DT2 + 应用数据
    userdata = info_bytes + addr_bytes + bytes([afn & 0xFF, dt1, dt2]) + bytes(appdata)
    L = len(userdata) + 6  # +起始68 +长度2 +控制域1 +校验和1 +结束16
    frame = bytes([0x68, L & 0xFF, (L >> 8) & 0xFF, c]) + userdata
    cs = sum(frame[3:]) % 256  # 控制域起至CS前
    return frame + bytes([cs, end & 0xFF])


def build_13762_frame(
    afn: int,
    fn: int = 1,
    appdata: bytes = b"",
    direction: str = "down",
    comm_mode: int = 3,
    info: Optional[dict] = None,
    address: Optional[dict] = None,
) -> bytes:
    """兼容旧签名：构造单 68 标准帧（afn/fn 前置参数风格）。"""
    return build_frame(
        direction=direction, comm_mode=comm_mode, info=info, address=address,
        afn=afn, fn=fn, appdata=appdata,
    )


# ---------------------------------------------------------------------------
# 主适配器
# ---------------------------------------------------------------------------

class QGDW103762Adapter(ProtocolAdapter):
    protocol = "1376.2"

    def try_extract(self, buf: bytes):
        """切出一帧单 68 标准帧；半包/非本协议返回 None。

        单 68 结构：68 | L(2B) | C | 用户数据 | CS | 16。
        - L = len(用户数据) + 6 = 整帧长 - 1（比双68少一个68）。
        - 第 3 字节是控制域 C（非 0x68），以此与 645/698 区分。
        """
        n = len(buf)
        if n < 12 or buf[0] != 0x68 or buf[-1] != 0x16:
            return None
        if buf[3] == 0x68:  # 双 68 帧（非本协议）
            return None
        L = buf[1] | (buf[2] << 8)
        if L != n:  # 单68：L = 整帧长（68+L2+C+用户数据+CS+16）
            return None
        if L < 10:
            return None
        return ExtractResult(raw=buf[:], consumed=n)

    def confidence(self, raw: bytes) -> float:
        """对一帧单 68 打分。"""
        n = len(raw)
        if n < 12 or raw[0] != 0x68 or raw[-1] != 0x16 or raw[3] == 0x68:
            return 0.0
        L = raw[1] | (raw[2] << 8)
        if L != n or L < 10:  # 单68：L = 整帧长
            return 0.0
        # 控制域 D5~D0 为合法通信方式，或 AFN 在字典内 → 加分
        c = raw[3]
        comm = c & 0x3F
        afn_known = self._afn_pos(raw) in _AFN_NAMES if n >= 14 else False
        cs = sum(raw[3:-2]) % 256
        if cs == raw[-2]:
            return 1.0 if (comm in _COMM_MODE_NAMES or afn_known) else 0.9
        return 0.5

    @staticmethod
    def _afn_pos(raw: bytes) -> int:
        """AFN 位于用户数据内部（R+A+AFN），需先跳过 R(6) 与 A(可变)。
        简化：A 地址域从 info.module_id 判断——module_id=0 无地址域。
        """
        # 用户数据从 pos4 起：R(6) | A(?) | AFN | DT1 | DT2 | ...
        c = raw[3]
        direction = "up" if (c >> 7) & 0x01 else "down"
        info = _parse_info(direction, raw[4:10])
        addr_len = 0
        if info["module_id"] == 1:
            # 有地址域：从 pos10 起读 A1(6) + 中继 + A3(6)，取一个 6B 地址
            addr_len = 6
            if info["relay_level"] > 0:
                addr_len += 6 * info["relay_level"]
            addr_len += 6
        return raw[10 + addr_len] if 10 + addr_len < len(raw) else 0

    def decode(self, raw: bytes) -> ProtocolFrame:
        """解码单 68 标准帧为 ProtocolFrame。"""
        frame = ProtocolFrame(structure="1376.2", raw_hex=raw.hex())
        warnings = []

        L = raw[1] | (raw[2] << 8)
        c = raw[3]
        ctl = _parse_control(c)
        userdata = raw[4:len(raw) - 2]  # 去掉 CS 与 16H

        # 信息域 R（6B）
        direction = ctl["direction"]
        info_raw = userdata[0:6]
        info = _parse_info(direction, info_raw)
        pos = 6

        # 地址域 A（module_id=1 时有）
        address = {"src": "", "relay": [], "dst": "", "has_address": False}
        addr_raw = b""
        if info["module_id"] == 1:
            addr_len = 6 + 6 * info["relay_level"] + 6
            addr_raw = userdata[pos:pos + addr_len]
            address = _parse_address(addr_raw)
            pos += addr_len

        # AFN + DT1 + DT2
        if pos + 3 > len(userdata):
            frame.warnings = warnings + ["用户数据不足：缺 AFN/DT"]
            return frame
        afn = userdata[pos]
        dt1 = userdata[pos + 1]
        dt2 = userdata[pos + 2]
        fn = dt_to_fn(dt1, dt2)
        appdata = userdata[pos + 3:]

        afn_name = _AFN_NAMES.get(afn, f"AFN-0x{afn:02X}")

        # 填充 fields
        frame.fields.append(DataField(name="长度L", value=L, hex=f"{L:04X}", raw=L))
        frame.fields.append(DataField(
            name="控制域C",
            value=f"0x{c:02X} (DIR={ctl['dir']} PRM={ctl['prm']} 通信方式={ctl['comm_mode_name']})",
            hex=f"{c:02X}", raw=c,
            desc=f"方向={ctl['direction']}; 启动标志={'启动站' if ctl['prm'] else '从动站'}"))
        frame.fields.append(DataField(
            name="信息域R", value=info_raw.hex(), hex=info_raw.hex(), raw=info_raw.hex(),
            desc=f"relay_level={info['relay_level']} module_id={info['module_id']} seq={info['seq']}"))
        frame.fields.append(DataField(
            name="地址域A", value=addr_raw.hex() if addr_raw else "(无)",
            hex=addr_raw.hex() if addr_raw else "", raw=addr_raw.hex() if addr_raw else ""))
        frame.fields.append(DataField(
            name="AFN", value=f"0x{afn:02X} ({afn_name})", hex=f"{afn:02X}", raw=afn))
        frame.items.append(DataField(
            name="AFN", value=f"0x{afn:02X} ({afn_name})", hex=f"{afn:02X}", raw=afn,
            desc="应用层功能码"))
        frame.fields.append(DataField(
            name="数据单元标识", value=f"DT1=0x{dt1:02X} DT2=0x{dt2:02X}",
            hex=f"{dt1:02X} {dt2:02X}", raw=(dt1, dt2)))
        frame.fields.append(DataField(name="FN", value=f"F{fn}", hex=f"{fn:02X}", raw=fn))

        # CS 校验
        cs = sum(raw[3:-2]) % 256
        cs_ok = (cs == raw[-2])
        frame.fields.append(DataField(
            name="校验和CS", value=f"0x{raw[-2]:02X}", hex=f"{raw[-2]:02X}", raw=raw[-2],
            desc="校验" + ("通过" if cs_ok else f"失败(计算0x{cs:02X})")))
        if not cs_ok:
            warnings.append(f"CS校验失败: 帧内0x{raw[-2]:02X} vs 计算0x{cs:02X}")

        # 应用数据解析
        if appdata:
            frame.items.extend(_app_items(afn, fn, appdata))
            # 数据转发/上报类：递归解内嵌 645/698
            if afn in (0x02, 0x06, 0x13, 0xF1):
                nested = _scan_nested(appdata)
                if nested:
                    _append_nested(frame, nested, "1376.2应用数据内")
        else:
            frame.items.append(DataField(name="应用数据", value="(无)", hex="", raw=""))

        frame.warnings = warnings
        return frame


# ---------------------------------------------------------------------------
# JSON 接口（纯函数，无 IO）
# ---------------------------------------------------------------------------

def _json_field(f: DataField) -> dict:
    return {"name": f.name, "value": f.value, "hex": f.hex,
            "raw": f.raw, "desc": f.desc, "unit": f.unit}


def _frame_to_json(frame: ProtocolFrame) -> dict:
    nested = []
    for n in frame.nested:
        nested.append({
            "structure": n.structure,
            "fields": {f.name: _json_field(f) for f in n.fields},
            "items": [_json_field(f) for f in n.items],
            "nested": [_json_field(f) for f in n.nested],
        })
    return {
        "structure": frame.structure,
        "raw_hex": frame.raw_hex,
        "fields": {f.name: _json_field(f) for f in frame.fields},
        "items": [_json_field(f) for f in frame.items],
        "nested": nested,
        "warnings": list(frame.warnings),
    }


def build_frame_json(req: dict) -> dict:
    """JSON → bytes 构帧接口。

    req:
        {
          "direction": "down",
          "control": {"prm": 1, "comm_mode": 3},
          "info": {...},
          "address": {"src": "...", "relay": [], "dst": "..."},
          "afn": "03",
          "fn": "F1" | 1,
          "data": {"raw": "AABBCC"} | {"nested_645": {...}} | {"nested_698": {...}}
        }
    """
    try:
        control = req.get("control") or {}
        info = req.get("info") or {}
        address = req.get("address")
        afn = int(str(req.get("afn", "03")), 16)
        fn_raw = req.get("fn", 1)
        fn = int(fn_raw.replace("F", "")) if isinstance(fn_raw, str) else int(fn_raw)
        data = req.get("data") or {}
        appdata = b""
        if "raw" in data:
            appdata = bytes.fromhex(str(data["raw"]).replace(" ", ""))
        elif "nested_645" in data:
            from parser_lib.adapters.adapter_645 import build_frame as build_645
            nd = data["nested_645"]
            addr = nd.get("addr", "000000000000")
            ctrl = int(str(nd.get("control", "01")), 16)
            # payload 语义 = 逻辑数据域（未加密）。645 传输需 +33H 加密，
            # 但读通信地址(0x03)/广播校时(0x08) 不加 33H（协议规定）。
            payload = bytes.fromhex(str(nd.get("payload", "")).replace(" ", ""))
            if ctrl not in (0x03, 0x08):
                payload = bytes((b + 0x33) & 0xFF for b in payload)
            appdata = build_645(_str_to_bcd(str(addr), 6), ctrl, payload)
        elif "nested_698" in data:
            from parser_lib.adapters.adapter_698 import build_frame as build_698
            nd = data["nested_698"]
            addr = _str_to_bcd(str(nd.get("addr", "000000000000")), 6)
            apdu = bytes.fromhex(str(nd.get("apdu", "")).replace(" ", ""))
            appdata = build_698(apdu, addr, ca=nd.get("ca", 0))
        frame = build_frame(
            direction=req.get("direction", "down"),
            comm_mode=control.get("comm_mode", 3),
            prm=control.get("prm"),
            info=info,
            address=address,
            afn=afn,
            fn=fn,
            appdata=appdata,
        )
        return {
            "action": "build",
            "ok": True,
            "frame_hex": " ".join(f"{b:02X}" for b in frame),
            "frame_bytes": list(frame),
            "length": len(frame),
            "cs": frame[-2],
            "warnings": [],
        }
    except Exception as e:
        return {"action": "build", "ok": False, "error": repr(e)}


def decode_frame_json(req: dict) -> dict:
    """bytes/hex → JSON 解析接口。

    req: {"frame": "68 ... 16"} 或 {"frame_bytes": [104, ...]}
    """
    try:
        frame = req.get("frame")
        if frame is None:
            raw = bytes(req.get("frame_bytes", []))
        elif isinstance(frame, list):
            raw = bytes(frame)
        else:
            raw = bytes.fromhex(str(frame).replace(" ", ""))
        pf = QGDW103762Adapter().decode(raw)
        return {"action": "parse", "ok": True, **_frame_to_json(pf)}
    except Exception as e:
        return {"action": "parse", "ok": False, "error": repr(e)}
