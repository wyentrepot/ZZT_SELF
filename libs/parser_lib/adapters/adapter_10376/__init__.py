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

# 05H-F3 广播控制字（03H=相位识别功能，与通用协议类型表不同）
_BROADCAST_CTRL_NAMES = {0x00: "透明传输", 0x01: "DL/T 645—1997",
                         0x02: "DL/T 645—2007", 0x03: "相位识别功能"}
# 宽带载波频段（03H-F16 上行 / 05H-F16 下行）
_BAND_NAMES = {0: "1.953~11.96MHz", 1: "2.441~5.615MHz",
               2: "0.781~2.930MHz", 3: "1.758~2.930MHz"}
# 无线主节点发射功率（03H-F8 上行 / 05H-F5 下行，255=保持不变）
_POWER_NAMES = {0: "最高", 1: "次高", 2: "次低", 3: "最低"}
# 10H-F4 路由工作步骤
_WORK_STEP_NAMES = {1: "初始", 2: "直抄", 3: "中继", 4: "监控",
                    5: "广播", 6: "广播召读", 7: "读侦听", 8: "空闲"}
# 10H-F21 HPLC 节点角色（拓扑信息高4位）
_NODE_ROLE_NAMES = {0x0: "无效", 0x1: "末梢节点STA", 0x2: "代理节点PCO", 0x4: "主节点CCO"}
# 10H-F40 流水线查询设备类型
_PIPELINE_DEV_NAMES = {1: "抄控器", 2: "CCO", 3: "电表通信单元", 4: "中继器",
                       5: "II型采集器", 6: "I型采集器", 7: "三相表通信单元"}
# 15H-F1 文件标识
_FILE_ID_NAMES = {0x00: "清除下装文件", 0x03: "本地通信模块升级文件",
                  0x07: "主节点和子节点模块升级", 0x08: "子节点模块升级"}
# 模块ID号格式（03H-F12 / 10H-F7）
_ID_FORMAT_NAMES = {0: "组合格式", 1: "BCD", 2: "BIN", 3: "ASCII"}
# 06H-F3 路由工作任务变动类型
_ROUTE_TASK_CHANGE = {1: "抄表任务结束", 2: "搜表任务结束", 3: "台区识别任务结束"}
# 06H-F5 停复电事件类型（通信协议类型=04H）
_POWER_EVENT_NAMES = {1: "停电事件", 2: "复电事件"}

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

def _proto_name(proto: int) -> str:
    return f"0x{proto:02X} ({_PROTOCOL_TYPE_NAMES.get(proto, '保留')})"


def _app_items(afn: int, fn: int, data: bytes, direction: str = "down") -> list:
    """按 AFN/Fn 解析应用数据单元为 DataField 列表（可空）。

    全量覆盖 Q/GDW 10376.2—2019 §4/§5 定义的 73 个 Fn（AFN=F0H 厂家自定义
    及 10H-F104 上行格式文档未给出，以原始 hex 展示）。双向格式不同的 Fn
    按 direction 分别解析。多字节 BIN 一律小端（标准备注1：低字节在前）。
    """
    up = direction == "up"
    items = []

    def add(name, value, hex_str="", desc=""):
        items.append(DataField(name=name, value=value, hex=hex_str,
                               raw=value, desc=desc))

    def le16(b):
        return b[0] | (b[1] << 8)

    def rate_text(b) -> str:
        w = b[0] | (b[1] << 8)
        return f"{w & 0x7FFF} ({'kbps' if w >> 15 else 'bps'})"

    def decode_id(b: bytes, fmt: int) -> str:
        if fmt == 1:
            return _bcd_to_str(b)
        if fmt == 3:
            return b.decode("ascii", "replace")
        return b.hex()  # 0=组合 / 2=BIN

    # AFN=00H 确认/否认（上行应答）
    if afn == 0x00:
        if fn == 1 and len(data) >= 6:
            ch_status = data[0:4]
            wait = le16(data[4:6])
            cmd_state = "已处理" if (ch_status[0] >> 7) & 1 else "未处理"
            add("命令状态", cmd_state, ch_status.hex(), "确认帧 D7=命令状态")
            add("信道状态", ch_status.hex(), ch_status.hex(), "1~31信道忙闲位图")
            add("等待时间", f"{wait}s", f"{wait:04X}", "等待时间(秒)")
        elif fn == 2 and len(data) >= 1:
            err = data[0]
            add("错误状态字", f"{err} ({_DENY_ERROR_NAMES.get(err, '保留')})",
                f"{err:02X}", "否认原因")
    # AFN=01H 初始化：F1/F2/F3 均无数据单元
    elif afn == 0x01:
        add("初始化命令",
            {1: "硬件初始化(复位)", 2: "参数区初始化(清除从节点档案)",
             3: "数据区初始化(清除从节点通信信息)"}.get(fn, f"F{fn}"),
            "", "无数据单元")
    # AFN=02H 数据转发 F1：通信协议类型(1B) + 报文长度L(1B) + 报文内容(L)，上下行同构
    elif afn == 0x02:
        if fn == 1 and len(data) >= 2:
            proto, plen = data[0], data[1]
            payload = data[2:2 + plen]
            add("通信协议类型", _proto_name(proto), f"{proto:02X}",
                "转发帧承载的协议类型")
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
        elif fn == 3 and len(data) >= 2:
            if not up:  # 下行：开始节点指针 + 数量(N≤16)
                add("开始节点指针", f"{data[0]}", f"{data[0]:02X}")
                add("读取节点的数量", f"{data[1]}", f"{data[1]:02X}", "N≤16")
            else:  # 上行：总数量 + 本帧数量 + 每节点(地址6B+品质/中继1B+侦听次数1B)
                add("侦听到的从节点总数量", f"{data[0]}", f"{data[0]:02X}")
                n = data[1]
                add("本帧传输的从节点数量", f"{n}", f"{n:02X}")
                pos = 2
                for i in range(n):
                    if pos + 8 > len(data):
                        break
                    add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                        data[pos:pos + 6].hex())
                    q = data[pos + 6]
                    add(f"从节点{i + 1}信号品质", f"{q >> 4}", f"{q:02X}", "高4位")
                    add(f"从节点{i + 1}中继级别", f"{q & 0x0F}", "", "低4位")
                    add(f"从节点{i + 1}侦听次数", f"{data[pos + 7]}",
                        f"{data[pos + 7]:02X}")
                    pos += 8
        elif fn == 4 and len(data) >= 6:
            add("主节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
        elif fn == 5 and len(data) >= 2:
            st = data[0]
            n = st & 0x0F
            add("主节点状态字", data[0:2].hex(), data[0:2].hex(),
                f"周期抄表模式={(st >> 6) & 0x3} 主节点信道特征={(st >> 4) & 0x3}"
                f" 速率数量={n} 信道数量={data[1] & 0x0F}")
            pos = 2
            for i in range(n):
                if pos + 2 > len(data):
                    break
                add(f"通信速率{i + 1}", rate_text(data[pos:pos + 2]),
                    data[pos:pos + 2].hex(), "D15=速率单位标识(kbps/bps)")
                pos += 2
        elif fn == 6 and len(data) >= 1:
            if not up:
                add("持续时间", f"{data[0]}min", f"{data[0]:02X}", "干扰持续时间")
            else:
                add("干扰状态", "有干扰" if data[0] else "无干扰", f"{data[0]:02X}")
        elif fn == 7 and len(data) >= 1:
            add("最大超时时间", f"{data[0]}s", f"{data[0]:02X}",
                "从节点监控最大超时时间")
        elif fn == 8 and len(data) >= 2:
            add("无线信道组", f"{data[0]}", f"{data[0]:02X}")
            add("无线主节点发射功率", f"{data[1]}", f"{data[1]:02X}")
        elif fn == 9 and len(data) >= 2:
            if not up:
                proto, plen = data[0], data[1]
                add("通信协议类型", _proto_name(proto), f"{proto:02X}")
                add("报文长度", f"{plen}B", f"{plen:02X}")
                add("报文内容", data[2:2 + plen].hex(), data[2:2 + plen].hex(),
                    "广播报文内容")
            else:
                add("广播通信延迟时间", f"{le16(data[0:2])}s",
                    data[0:2].hex(), "BIN 2B 小端")
                if len(data) >= 4:
                    proto, plen = data[2], data[3]
                    add("通信协议类型", _proto_name(proto), f"{proto:02X}")
                    add("报文长度", f"{plen}B", f"{plen:02X}")
                    add("报文内容", data[4:4 + plen].hex(), data[4:4 + plen].hex())
        elif fn == 10 and len(data) >= 6:
            add("本地通信模式字", data[0:6].hex(), data[0:6].hex())
            if len(data) >= 39:
                d = data
                add("从节点监控最大超时时间", f"{d[6]}s", f"{d[6]:02X}")
                add("广播命令最大超时时间", f"{le16(d[7:9])}s", d[7:9].hex())
                add("最大支持的报文长度", f"{le16(d[9:11])}B", d[9:11].hex())
                add("文件传输最大单包长度", f"{le16(d[11:13])}B", d[11:13].hex())
                add("升级操作等待时间", f"{d[13]}s", f"{d[13]:02X}")
                add("主节点地址", _bcd_to_str(d[14:20]), d[14:20].hex())
                add("支持的最大从节点数量", f"{le16(d[20:22])}", d[20:22].hex())
                add("当前从节点数量", f"{le16(d[22:24])}", d[22:24].hex())
                add("协议发布日期", f"20{_bcd_to_str(d[24:25])}-{_bcd_to_str(d[25:26])}"
                    f"-{_bcd_to_str(d[26:27])}", d[24:27].hex(), "BCD YYMMDD")
                add("协议最后备案日期", f"20{_bcd_to_str(d[27:28])}-{_bcd_to_str(d[28:29])}"
                    f"-{_bcd_to_str(d[29:30])}", d[27:30].hex(), "BCD YYMMDD")
                add("厂商代码及版本信息", d[30:39].hex(), d[30:39].hex(), "9B")
                pos, i = 39, 1
                while pos + 2 <= len(d):
                    add(f"通信速率{i}", rate_text(d[pos:pos + 2]),
                        d[pos:pos + 2].hex())
                    pos += 2
                    i += 1
        elif fn == 11 and len(data) >= 1:
            add("AFN功能码", f"0x{data[0]:02X}", f"{data[0]:02X}")
            if up and len(data) >= 33:
                bitmap = data[1:33]
                supported = [i + 1 for i in range(255)
                             if (bitmap[i >> 3] >> (i & 7)) & 1]
                add("支持的数据单元",
                    ",".join(f"F{x}" for x in supported) or "(无)",
                    bitmap.hex(), "32B位图 D0=F1 D254=F255")
        elif fn == 12 and len(data) >= 4:
            add("模块厂商代码", data[0:2].decode("ascii", "replace"), data[0:2].hex())
            idlen, idfmt = data[2], data[3]
            add("模块ID号长度", f"{idlen}", f"{idlen:02X}")
            add("模块ID号格式", _ID_FORMAT_NAMES.get(idfmt, f"保留({idfmt})"),
                f"{idfmt:02X}")
            mid = data[4:4 + idlen]
            add("模块ID号", decode_id(mid, idfmt), mid.hex())
        elif fn == 16 and len(data) >= 1:
            add("宽带载波频段", f"{data[0]} ({_BAND_NAMES.get(data[0], '保留')})",
                f"{data[0]:02X}")
        elif fn == 100 and len(data) >= 1:
            add("场强门限", f"{data[0]}", f"{data[0]:02X}", "取值50~120，默认96")
    # AFN=04H 链路接口检测
    elif afn == 0x04:
        if fn == 1 and len(data) >= 1:
            add("持续时间", "停止发送" if data[0] == 0 else f"{data[0]}s",
                f"{data[0]:02X}", "0=停止发送；持续交替发送0和1")
        elif fn == 2:
            add("从节点点名", "(无数据单元)", "")
        elif fn == 3 and len(data) >= 9:
            add("测试通信速率", f"{data[0]}", f"{data[0]:02X}")
            add("目标地址", _bcd_to_str(data[1:7]), data[1:7].hex())
            add("通信协议类型", _proto_name(data[7]), f"{data[7]:02X}")
            plen = data[8]
            add("报文长度", f"{plen}B", f"{plen:02X}")
            add("报文内容", data[9:9 + plen].hex(), data[9:9 + plen].hex())
    # AFN=05H 控制命令（上行应答均为 00H 确认/否认）
    elif afn == 0x05:
        if fn == 1 and len(data) >= 6:
            add("主节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
        elif fn == 2 and len(data) >= 1:
            add("事件上报状态", "允许" if data[0] else "禁止", f"{data[0]:02X}")
        elif fn == 3 and len(data) >= 2:
            ctrl, plen = data[0], data[1]
            add("控制字", f"0x{ctrl:02X} ({_BROADCAST_CTRL_NAMES.get(ctrl, '保留')})",
                f"{ctrl:02X}", "广播控制字")
            add("报文长度", f"{plen}B", f"{plen:02X}")
            add("广播报文", data[2:2 + plen].hex(), data[2:2 + plen].hex())
        elif fn == 4 and len(data) >= 1:
            add("最大超时时间", f"{data[0]}s", f"{data[0]:02X}")
        elif fn == 5 and len(data) >= 2:
            ch, pw = data[0], data[1]
            ch_desc = "自动选择" if ch == 254 else "保持不变" if ch == 255 else f"{ch}组"
            add("无线信道组", f"{ch} ({ch_desc})", f"{ch:02X}", "0~63组")
            pw_desc = _POWER_NAMES.get(pw, "保持不变" if pw == 255 else "保留")
            add("无线主节点发射功率", f"{pw} ({pw_desc})", f"{pw:02X}")
        elif fn == 6 and len(data) >= 1:
            add("台区识别使能", "允许" if data[0] else "禁止", f"{data[0]:02X}")
        elif fn == 16 and len(data) >= 1:
            add("宽带载波频段", f"{data[0]} ({_BAND_NAMES.get(data[0], '保留')})",
                f"{data[0]:02X}")
        elif fn == 100 and len(data) >= 1:
            add("场强门限", f"{data[0]}", f"{data[0]:02X}", "取值50~120，默认96")
        elif fn == 101 and len(data) >= 6:
            sec, minute, hour, day, month, year = data[0:6]
            add("中心节点时间",
                f"20{year:02X}-{month:02X}-{day:02X} {hour:02X}:{minute:02X}:{sec:02X}",
                data[0:6].hex(), "BCD 秒分时日月年")
        elif fn == 200 and len(data) >= 1:
            add("拒绝节点上报", "允许" if data[0] else "禁止", f"{data[0]:02X}",
                "2019版扩展")
    # AFN=06H 主动上报（上行）
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
                add(f"从节点{i + 1}协议类型", _proto_name(data[pos + 6]),
                    f"{data[pos + 6]:02X}")
                seq = le16(data[pos + 7:pos + 9])
                add(f"从节点{i + 1}序号", f"{seq}", f"{seq:04X}")
                pos += 9
        elif fn == 2 and len(data) >= 6:
            seq = le16(data[0:2])
            proto = data[2]
            up_len = le16(data[3:5])
            plen = data[5]
            payload = data[6:6 + plen]
            add("从节点序号", f"{seq}", f"{seq:04X}")
            add("通信协议类型", _proto_name(proto), f"{proto:02X}")
            add("上行时长", f"{up_len}s", f"{up_len:04X}")
            add("报文长度", f"{plen}B", f"{plen:02X}")
            add("上报报文", payload.hex(), payload.hex())
        elif fn == 3 and len(data) >= 1:
            add("路由工作任务变动类型",
                _ROUTE_TASK_CHANGE.get(data[0], f"保留({data[0]})"), f"{data[0]:02X}")
        elif fn == 4 and len(data) >= 1:
            n = data[0]
            add("上报从节点数量", f"{n}", f"{n:02X}")
            pos = 1
            for i in range(n):
                if pos + 11 > len(data):
                    break
                add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                    data[pos:pos + 6].hex())
                add(f"从节点{i + 1}协议类型", _proto_name(data[pos + 6]),
                    f"{data[pos + 6]:02X}")
                seq = le16(data[pos + 7:pos + 9])
                add(f"从节点{i + 1}序号", f"{seq}", f"{seq:04X}")
                add(f"从节点{i + 1}设备类型",
                    f"0x{data[pos + 9]:02X} ({_DEVICE_TYPE_NAMES.get(data[pos + 9], '保留')})",
                    f"{data[pos + 9]:02X}")
                m = data[pos + 10]
                add(f"从节点{i + 1}下接从节点数量", f"{m}", f"{m:02X}")
                pos += 11
                if m >= 1 and pos < len(data):
                    mm = data[pos]
                    add(f"从节点{i + 1}本帧传输节点数量", f"{mm}", f"{mm:02X}")
                    pos += 1
                    for j in range(mm):
                        if pos + 7 > len(data):
                            break
                        add(f"从节点{i + 1}下接节点{j + 1}地址",
                            _bcd_to_str(data[pos:pos + 6]), data[pos:pos + 6].hex())
                        add(f"从节点{i + 1}下接节点{j + 1}协议类型",
                            _proto_name(data[pos + 6]), f"{data[pos + 6]:02X}")
                        pos += 7
        elif fn == 5 and len(data) >= 3:
            dt, proto, plen = data[0], data[1], data[2]
            content = data[3:3 + plen]
            add("从节点设备类型",
                f"0x{dt:02X} ({_DEVICE_TYPE_NAMES.get(dt, '保留')})", f"{dt:02X}")
            add("通信协议类型", _proto_name(proto), f"{proto:02X}")
            add("报文长度", f"{plen}B", f"{plen:02X}")
            if proto == 0x04 and content:  # 停复电事件
                if dt == 0x00:  # 采集器停电：表地址6B(BIN) + 带电状态1B ×N
                    add("事件类型", "采集器停电事件", "", "设备类型=00H")
                    for i in range(len(content) // 7):
                        base = i * 7
                        add(f"电能表{i + 1}地址", content[base:base + 6].hex(),
                            content[base:base + 6].hex())
                        st = content[base + 6]
                        add(f"电能表{i + 1}带电状态",
                            "未停电" if st else "停电", f"{st:02X}")
                else:
                    ev = content[0]
                    add("事件类型", _POWER_EVENT_NAMES.get(ev, f"保留({ev})"),
                        f"{ev:02X}")
                    addrs = content[1:]
                    for i in range(len(addrs) // 6):
                        add(f"通信单元{i + 1}地址",
                            _bcd_to_str(addrs[i * 6:(i + 1) * 6]),
                            addrs[i * 6:(i + 1) * 6].hex())
            elif proto == 0x05 and content:  # 台区改切拒绝节点
                n = content[0]
                add("本次上报个数", f"{n}", f"{n:02X}", "n≤32")
                for i in range(n):
                    base = 1 + i * 7
                    if base + 7 > len(content):
                        break
                    add(f"被拒节点{i + 1}地址", content[base:base + 6].hex(),
                        content[base:base + 6].hex())
                    add(f"被拒节点{i + 1}设备类型",
                        f"0x{content[base + 6]:02X}", f"{content[base + 6]:02X}")
            add("报文内容", content.hex(), content.hex(), "事件报文内容")
        elif fn == 230 and len(data) >= 10:
            # 安徽分钟级采集扩展 F230 主动上报
            # 结构：任务号(1B) + 从节点地址(6B BCD) + 通信协议类型(1B)
            #       + 采集时间(6B BCD 秒分时日月年) + 报文长度(2B 小端) + 报文内容(变长)
            add("任务号", f"{data[0]}", f"{data[0]:02X}")
            add("从节点地址", _bcd_to_str(data[1:7]), data[1:7].hex())
            add("通信协议类型", _proto_name(data[7]), f"{data[7]:02X}")
            if len(data) >= 13:
                sec, minute, hour, day, month, year = data[8:14]
                time_str = f"20{year:02X}年{month:02X}月{day:02X}日 {hour:02X}:{minute:02X}:{sec:02X}"
                add("采集时间", time_str, data[8:14].hex(), "BCD 秒分时日月年")
            if len(data) >= 16:
                plen = le16(data[14:16])
                content = data[16:16 + plen]
                add("报文长度", f"{plen}B", data[14:16].hex(), "BIN 2B 小端")
                add("报文内容", content.hex(), content.hex(),
                    "内嵌 645/698 报文（嵌套帧见下）")
    # AFN=10H 路由查询
    elif afn == 0x10:
        if fn == 1 and len(data) >= 4:
            add("从节点总数量", f"{le16(data[0:2])}", data[0:2].hex(), "BIN 2B 小端")
            add("路由支持最大从节点数量", f"{le16(data[2:4])}", data[2:4].hex())
        elif fn in (2, 5, 6) and len(data) >= 3:
            name = {2: "从节点", 5: "未抄读成功从节点", 6: "主动注册从节点"}[fn]
            if not up:
                add(f"{name}起始序号", f"{le16(data[0:2])}", data[0:2].hex())
                add(f"{name}数量", f"{data[2]}", f"{data[2]:02X}")
            else:
                add(f"{name}总数量", f"{le16(data[0:2])}", data[0:2].hex())
                n = data[2]
                add(f"本次应答的{name}数量", f"{n}", f"{n:02X}")
                pos = 3
                for i in range(n):
                    if pos + 8 > len(data):
                        break
                    add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                        data[pos:pos + 6].hex())
                    w = le16(data[pos + 6:pos + 8])
                    add(f"从节点{i + 1}信息", f"0x{w:04X}", data[pos + 6:pos + 8].hex(),
                        f"信号品质={(w >> 4) & 0xF} 中继级别={w & 0xF}"
                        f" 相线={(w >> 8) & 0xF} 协议类型={(w >> 12) & 0x7}")
                    pos += 8
        elif fn == 3:
            if not up and len(data) >= 6:
                add("从节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
            elif up and len(data) >= 1:
                n = data[0]
                add("提供路由的从节点总数量", f"{n}", f"{n:02X}")
                pos = 1
                for i in range(n):
                    if pos + 8 > len(data):
                        break
                    add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                        data[pos:pos + 6].hex())
                    w = le16(data[pos + 6:pos + 8])
                    add(f"从节点{i + 1}信息", f"0x{w:04X}", data[pos + 6:pos + 8].hex(),
                        f"信号品质={(w >> 4) & 0xF} 中继级别={w & 0xF}")
                    pos += 8
        elif fn == 4 and len(data) >= 17:
            st = data[0]
            add("运行状态字", f"0x{st:02X}", f"{st:02X}",
                f"纠错编码={(st >> 4) & 0xF} 上报事件={(st >> 3) & 1}"
                f" 工作={(st >> 2) & 1} 路由完成={(st >> 1) & 1}")
            add("从节点总数量", f"{le16(data[1:3])}", data[1:3].hex())
            add("已抄从节点数量", f"{le16(data[3:5])}", data[3:5].hex())
            add("中继抄到从节点数量", f"{le16(data[5:7])}", data[5:7].hex())
            wk = data[7]
            add("工作开关", f"0x{wk:02X}", f"{wk:02X}",
                f"当前状态={ {0: '抄表', 1: '搜表', 2: '升级', 3: '其他'}[wk >> 6] }"
                f" 台区识别={(wk >> 4) & 0x3} 上报事件={(wk >> 3) & 1}"
                f" 注册允许={(wk >> 2) & 1} 工作状态={'学习' if (wk >> 1) & 1 else '抄表'}")
            add("通信速率", rate_text(data[8:10]), data[8:10].hex())
            for p in range(3):
                add(f"第{p + 1}相中继级别", f"{data[10 + p]}", f"{data[10 + p]:02X}")
            for p in range(3):
                v = data[13 + p]
                add(f"第{p + 1}相工作步骤",
                    f"{v} ({_WORK_STEP_NAMES.get(v, '保留')})", f"{v:02X}")
        elif fn == 7 and len(data) >= 3:
            if not up:
                add("从节点起始序号", f"{le16(data[0:2])}", data[0:2].hex())
                add("从节点数量", f"{data[2]}", f"{data[2]:02X}")
            else:
                add("从节点总数量", f"{le16(data[0:2])}", data[0:2].hex())
                n = data[2]
                add("本次应答的从节点数量", f"{n}", f"{n:02X}")
                pos = 3
                for i in range(n):
                    if pos + 11 > len(data):
                        break
                    add(f"从节点{i + 1}地址", data[pos:pos + 6].hex(),
                        data[pos:pos + 6].hex(), "BIN 6B")
                    nt = data[pos + 6]
                    add(f"从节点{i + 1}节点类型", f"0x{nt:02X}", f"{nt:02X}",
                        f"更新标识={(nt >> 7) & 1}"
                        f" 模块类型={ {0: '电表模块', 1: '采集器模块', 15: '未知'}.get(nt & 0xF, nt & 0xF)}")
                    add(f"从节点{i + 1}模块厂商代码",
                        data[pos + 7:pos + 9].decode("ascii", "replace"),
                        data[pos + 7:pos + 9].hex())
                    idlen, idfmt = data[pos + 9], data[pos + 10]
                    add(f"从节点{i + 1}模块ID号长度", f"{idlen}", f"{idlen:02X}")
                    add(f"从节点{i + 1}模块ID号格式",
                        _ID_FORMAT_NAMES.get(idfmt, f"保留({idfmt})"), f"{idfmt:02X}")
                    mid = data[pos + 11:pos + 11 + idlen]
                    add(f"从节点{i + 1}模块ID号", decode_id(mid, idfmt), mid.hex())
                    pos += 11 + idlen
        elif fn == 9 and len(data) >= 2:
            add("网络规模", f"{le16(data[0:2])}", data[0:2].hex(), "HPLC")
        elif fn == 21 and len(data) >= 3:
            if not up:
                add("节点起始序号", f"{le16(data[0:2])}", data[0:2].hex())
                add("节点数量", f"{data[2]}", f"{data[2]:02X}")
            else:
                add("节点总数量", f"{le16(data[0:2])}", data[0:2].hex())
                add("节点起始序号", f"{le16(data[2:4])}", data[2:4].hex())
                n = data[4]
                add("本次应答的节点数量", f"{n}", f"{n:02X}")
                pos = 5
                for i in range(n):
                    if pos + 11 > len(data):
                        break
                    add(f"节点{i + 1}地址", data[pos:pos + 6].hex(),
                        data[pos:pos + 6].hex(), "BIN 6B")
                    tei = le16(data[pos + 6:pos + 8])
                    proxy = le16(data[pos + 8:pos + 10])
                    info = data[pos + 10]
                    role = info >> 4
                    add(f"节点{i + 1}TEI", f"{tei}", f"{tei:04X}", "节点标识")
                    add(f"节点{i + 1}代理节点标识", f"{proxy}", f"{proxy:04X}")
                    add(f"节点{i + 1}网络拓扑",
                        f"层级={info & 0xF} 角色={_NODE_ROLE_NAMES.get(role, f'0x{role:X}')}",
                        f"{info:02X}")
                    pos += 11
        elif fn == 31 and len(data) >= 3:
            if not up:
                add("节点起始序号", f"{le16(data[0:2])}", data[0:2].hex())
                add("节点数量", f"{data[2]}", f"{data[2]:02X}")
            else:
                add("节点总数量", f"{le16(data[0:2])}", data[0:2].hex())
                add("节点起始序号", f"{le16(data[2:4])}", data[2:4].hex())
                n = data[4]
                add("本次应答的节点数量", f"{n}", f"{n:02X}")
                pos = 5
                for i in range(n):
                    if pos + 8 > len(data):
                        break
                    add(f"节点{i + 1}地址", data[pos:pos + 6].hex(),
                        data[pos:pos + 6].hex(), "BIN 6B")
                    w = le16(data[pos + 6:pos + 8])
                    phases = "+".join(str(p + 1) for p in range(3) if (w >> p) & 1) or "无"
                    add(f"节点{i + 1}相线信息", f"0x{w:04X}", data[pos + 6:pos + 8].hex(),
                        f"相位={phases} 电表类型={'三相表' if (w >> 4) & 1 else '单相表'}"
                        f" 线路异常={'有' if (w >> 5) & 1 else '无'}"
                        f" 相序类型={(w >> 5) & 0x7}")
                    pos += 8
        elif fn == 40 and len(data) >= 8:
            add("设备类型",
                f"{data[0]} ({_PIPELINE_DEV_NAMES.get(data[0], '保留')})", f"{data[0]:02X}")
            add("节点地址", data[1:7].hex(), data[1:7].hex(), "BIN 6B")
            add("ID类型", f"{data[7]} ({ {1: '芯片ID(长度24)', 2: '模块ID(长度11)'}.get(data[7], '保留') })",
                f"{data[7]:02X}")
            if up and len(data) >= 9:
                idlen = data[8]
                add("ID长度", f"{idlen}", f"{idlen:02X}")
                add("ID信息", data[9:9 + idlen].hex(), data[9:9 + idlen].hex())
        elif fn == 100 and len(data) >= 2:
            add("网络规模", f"{le16(data[0:2])}", data[0:2].hex(), "无线微功率")
        elif fn == 101 and len(data) >= 3:
            if not up:
                add("从节点起始序号", f"{le16(data[0:2])}", data[0:2].hex())
                add("从节点数量", f"{data[2]}", f"{data[2]:02X}")
            else:
                add("从节点总数量", f"{le16(data[0:2])}", data[0:2].hex())
                n = data[2]
                add("本次应答的从节点数量", f"{n}", f"{n:02X}")
                pos = 3
                for i in range(n):
                    if pos + 11 > len(data):
                        break
                    add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                        data[pos:pos + 6].hex())
                    w = le16(data[pos + 6:pos + 8])
                    add(f"从节点{i + 1}信息", f"0x{w:04X}", data[pos + 6:pos + 8].hex(),
                        f"信号品质={(w >> 4) & 0xF} 中继级别={w & 0xF}")
                    add(f"从节点{i + 1}软件版本", data[pos + 8:pos + 11].hex(),
                        data[pos + 8:pos + 11].hex())
                    pos += 11
        elif fn == 104 and data:
            add("应用数据", data.hex(), data.hex(),
                "查询升级后模块版本信息（蒸馏文档未给出格式，原始hex展示）")
        elif fn == 111 and len(data) >= 10:
            n = data[0]
            add("多网络节点总数量", f"{n}", f"{n:02X}")
            add("本节点网络标识号", f"{int.from_bytes(data[1:4], 'little')}",
                data[1:4].hex(), "NID 3B 小端，1~16777215")
            add("本节点主节点地址", data[4:10].hex(), data[4:10].hex(), "BIN 6B")
            pos = 10
            for i in range(max(n - 1, 0)):
                if pos + 3 > len(data):
                    break
                add(f"邻居节点{i + 1}网络标识号",
                    f"{int.from_bytes(data[pos:pos + 3], 'little')}",
                    data[pos:pos + 3].hex())
                pos += 3
        elif fn == 112 and len(data) >= 3:
            if not up:
                add("节点起始序号", f"{le16(data[0:2])}", data[0:2].hex())
                add("节点数量", f"{data[2]}", f"{data[2]:02X}")
            else:
                add("节点总数量", f"{le16(data[0:2])}", data[0:2].hex())
                add("节点起始序号", f"{le16(data[2:4])}", data[2:4].hex())
                n = data[4]
                add("本次应答的节点数量", f"{n}", f"{n:02X}")
                pos = 5
                for i in range(n):
                    if pos + 33 > len(data):
                        break
                    add(f"节点{i + 1}地址", data[pos:pos + 6].hex(),
                        data[pos:pos + 6].hex(), "BIN 6B")
                    add(f"节点{i + 1}设备类型",
                        f"0x{data[pos + 6]:02X}", f"{data[pos + 6]:02X}")
                    add(f"节点{i + 1}芯片ID信息", data[pos + 7:pos + 31].hex(),
                        data[pos + 7:pos + 31].hex(), "24B")
                    add(f"节点{i + 1}芯片软件版本", _bcd_to_str(data[pos + 31:pos + 33]),
                        data[pos + 31:pos + 33].hex(), "BCD 2B")
                    pos += 33
    # AFN=11H 路由设置（上行应答均为 00H 确认/否认）
    elif afn == 0x11:
        if fn == 1 and len(data) >= 1:
            n = data[0]
            add("从节点数量/操作标志", f"{n}", f"{n:02X}",
                "国网=从节点数量n；安徽扩展=1添加/0删除")
            pos, i = 1, 0
            while pos + 7 <= len(data):
                i += 1
                add(f"从节点{i}地址", _bcd_to_str(data[pos:pos + 6]),
                    data[pos:pos + 6].hex())
                add(f"从节点{i}通信协议类型", _proto_name(data[pos + 6]),
                    f"{data[pos + 6]:02X}")
                pos += 7
        elif fn == 2 and len(data) >= 1:
            n = data[0]
            add("从节点数量", f"{n}", f"{n:02X}")
            for i in range(n):
                pos = 1 + i * 6
                if pos + 6 > len(data):
                    break
                add(f"从节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                    data[pos:pos + 6].hex())
        elif fn == 3 and len(data) >= 7:
            add("从节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
            lv = data[6]
            add("中继级别", f"{lv}", f"{lv:02X}")
            for i in range(lv):
                pos = 7 + i * 6
                if pos + 6 > len(data):
                    break
                add(f"第{i + 1}级中继从节点地址", _bcd_to_str(data[pos:pos + 6]),
                    data[pos:pos + 6].hex())
        elif fn == 4 and len(data) >= 3:
            mode = data[0]
            add("工作模式", f"0x{mode:02X}", f"{mode:02X}",
                f"纠错编码={(mode >> 4) & 0xF} 注册允许={(mode >> 1) & 1}"
                f" 工作状态={'学习' if mode & 1 else '抄表'}")
            add("通信速率", rate_text(data[1:3]), data[1:3].hex(),
                "D15=速率单位标识")
        elif fn == 5 and len(data) >= 10:
            add("开始时间", f"20{_bcd_to_str(data[5:6])}-{_bcd_to_str(data[4:5])}"
                f"-{_bcd_to_str(data[3:4])} {_bcd_to_str(data[2:3])}:"
                f"{_bcd_to_str(data[1:2])}:{_bcd_to_str(data[0:1])}",
                data[0:6].hex(), "BCD 秒分时日月年")
            add("持续时间", f"{le16(data[6:8])}min", data[6:8].hex())
            add("从节点重发次数", f"{data[8]}", f"{data[8]:02X}")
            add("随机等待时间片个数", f"{data[9]}", f"{data[9]:02X}", "时间片=150ms")
        elif fn == 100 and len(data) >= 2:
            add("网络规模", f"{le16(data[0:2])}", data[0:2].hex())
        elif fn in (6, 101, 102):
            add("路由设置命令",
                {6: "终止从节点主动注册", 101: "启动网络维护进程",
                 102: "启动组网"}[fn], "", "无数据单元")
    # AFN=12H 路由控制：F1/F2/F3 均无数据单元
    elif afn == 0x12:
        add("路由控制命令", {1: "重启", 2: "暂停", 3: "恢复"}.get(fn, f"F{fn}"),
            "", "无数据单元")
    # AFN=13H 路由数据转发 F1 监控从节点
    elif afn == 0x13 and fn == 1:
        if not up and len(data) >= 3:
            add("通信协议类型", _proto_name(data[0]), f"{data[0]:02X}")
            add("通信延时相关性标志", "与延时相关" if data[1] else "与延时无关",
                f"{data[1]:02X}")
            n = data[2]
            add("从节点附属节点数量", f"{n}", f"{n:02X}")
            pos = 3
            for i in range(n):
                if pos + 6 > len(data):
                    break
                add(f"附属节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                    data[pos:pos + 6].hex())
                pos += 6
            if pos < len(data):
                plen = data[pos]
                pos += 1
                add("报文长度", f"{plen}B", f"{plen:02X}")
                add("报文内容", data[pos:pos + plen].hex(), data[pos:pos + plen].hex(),
                    "监控报文（内嵌645/698，递归解析）")
        elif up and len(data) >= 4:
            add("当前报文本地通信上行时长", f"{le16(data[0:2])}s", data[0:2].hex())
            add("通信协议类型", _proto_name(data[2]), f"{data[2]:02X}")
            plen = data[3]
            add("报文长度", f"{plen}B", f"{plen:02X}")
            add("报文内容", data[4:4 + plen].hex(), data[4:4 + plen].hex(),
                "应答报文（内嵌645/698，递归解析）")
    # AFN=14H 路由数据抄读
    elif afn == 0x14:
        if fn == 1:
            if up and len(data) >= 9:
                add("通信相位",
                    {0: "未知相", 1: "第1相", 2: "第2相", 3: "第3相"}.get(data[0], f"保留({data[0]})"),
                    f"{data[0]:02X}")
                add("从节点地址", _bcd_to_str(data[1:7]), data[1:7].hex())
                add("从节点序号", f"{le16(data[7:9])}", data[7:9].hex())
            elif not up and len(data) >= 3:
                flag = data[0]
                add("抄读标志",
                    {0: "抄读失败", 1: "抄读成功", 2: "可以抄读"}.get(flag, f"保留({flag})"),
                    f"{flag:02X}")
                add("通信延时相关性标志", "相关" if data[1] else "无关", f"{data[1]:02X}")
                ln = data[2]
                add("路由请求数据长度", f"{ln}B", f"{ln:02X}")
                add("路由请求数据内容", data[3:3 + ln].hex(), data[3:3 + ln].hex())
                pos = 3 + ln
                if pos < len(data):
                    n = data[pos]
                    add("从节点附属节点数量", f"{n}", f"{n:02X}")
                    pos += 1
                    for i in range(n):
                        if pos + 6 > len(data):
                            break
                        add(f"附属节点{i + 1}地址", _bcd_to_str(data[pos:pos + 6]),
                            data[pos:pos + 6].hex())
                        pos += 6
        elif fn == 2:
            if not up and len(data) >= 6:
                add("集中器时间", f"20{_bcd_to_str(data[5:6])}-{_bcd_to_str(data[4:5])}"
                    f"-{_bcd_to_str(data[3:4])} {_bcd_to_str(data[2:3])}:"
                    f"{_bcd_to_str(data[1:2])}:{_bcd_to_str(data[0:1])}",
                    data[0:6].hex(), "BCD 秒分时日月年")
            # 上行：无数据单元
        elif fn == 3:
            if up and len(data) >= 9:
                add("从节点地址", _bcd_to_str(data[0:6]), data[0:6].hex())
                add("预计延迟时间", f"{le16(data[6:8])}s", data[6:8].hex())
                ln = data[8]
                add("抄读信息长度", f"{ln}B", f"{ln:02X}")
                add("抄读数据内容", data[9:9 + ln].hex(), data[9:9 + ln].hex())
            elif not up and len(data) >= 1:
                ln = data[0]
                add("数据长度", f"{ln}B", f"{ln:02X}", "L=0放弃本次通信")
                add("修正通信数据内容", data[1:1 + ln].hex(), data[1:1 + ln].hex())
        elif fn == 4 and len(data) >= 5:
            add("数据项类型",
                {1: "DL/T645-2007", 2: "DL/T698.45"}.get(data[0], f"保留({data[0]})"),
                f"{data[0]:02X}")
            add("交采数据项标识", data[1:5].hex(), data[1:5].hex())
            if not up and len(data) > 5:
                add("交采数据项内容", data[5:].hex(), data[5:].hex())
    # AFN=15H 文件传输 F1
    elif afn == 0x15 and fn == 1:
        if not up and len(data) >= 11:
            fid, attr, cmd = data[0], data[1], data[2]
            add("文件标识", f"0x{fid:02X} ({_FILE_ID_NAMES.get(fid, '保留')})",
                f"{fid:02X}")
            add("文件属性", "结束帧" if attr else "起始帧/中间帧", f"{attr:02X}")
            add("文件指令", f"0x{cmd:02X}", f"{cmd:02X}", "00H=报文方式下装")
            add("总段数", f"{le16(data[3:5])}", data[3:5].hex())
            add("段标识", data[5:9].hex(), data[5:9].hex())
            lf = le16(data[9:11])
            add("段数据长度", f"{lf}B", f"{lf:04X}")
            add("文件数据", data[11:11 + lf].hex(), data[11:11 + lf].hex())
        elif up and len(data) >= 4:
            add("收到当前段标识", data[0:4].hex(), data[0:4].hex(),
                "0xFFFF=文件错误")
    # AFN=F0H 内部调试：厂家自定义，不拆字段
    # AFN=F1H 并发抄表 F1
    elif afn == 0xF1 and fn == 1:
        if not up and len(data) >= 4:
            add("规约类型", _proto_name(data[0]), f"{data[0]:02X}")
            add("保留", f"0x{data[1]:02X}", f"{data[1]:02X}")
            ln = le16(data[2:4])
            add("报文长度", f"{ln}B", f"{ln:04X}",
                "L=0抄表失败（链路层源地址A1=失败电表地址）")
            add("报文内容", data[4:4 + ln].hex(), data[4:4 + ln].hex(),
                "并发抄表报文（内嵌645/698，递归解析）")
        elif up and len(data) >= 3:
            add("规约类型", _proto_name(data[0]), f"{data[0]:02X}")
            ln = le16(data[1:3])
            add("报文长度", f"{ln}B", f"{ln:04X}",
                "L=0抄表失败（链路层源地址A1=失败电表地址）")
            add("报文内容", data[3:3 + ln].hex(), data[3:3 + ln].hex())
    return items


def _scan_payload_nested(payload: bytes):
    """对数据转发/上报的报文内容递归解内嵌 645/698。"""
    return _scan_nested(payload)

# ---------------------------------------------------------------------------
# 构建侧：应用数据单元编码（参数 → appdata 字节）
# 与解析侧 _app_items 对称，覆盖现有用例聚焦的 AFN/Fn（ADR-5）。
# 未覆盖的 (afn, fn) 抛 UnsupportedFn，不静默产出错帧。
# 帧字节序约定（据安徽已验证帧核查）：多字节 BIN 一律小端（低字节在前）。
# ---------------------------------------------------------------------------

class UnsupportedFn(ValueError):
    """未覆盖的 AFN/Fn 应用数据模板。"""


def _u8(v, name: str, lo: int = 0, hi: int = 0xFF) -> int:
    iv = int(v)
    if not (lo <= iv <= hi):
        raise ValueError(f"{name}={iv} 越界 [{lo},{hi}]")
    return iv


def _u16(v, name: str) -> int:
    iv = int(v)
    if not (0 <= iv <= 0xFFFF):
        raise ValueError(f"{name}={iv} 越界 [0,65535]")
    return iv


def _bcd_bytes(addr, name: str = "地址") -> bytes:
    s = str(addr).strip()
    if not s.isdigit():
        raise ValueError(f"{name} 必须为 BCD 数字串: {addr!r}")
    if len(s) != 12:
        raise ValueError(f"{name} 必须为 12 位 BCD: {s}")
    return _str_to_bcd(s, 6)


def _encode_11f231(params: dict) -> bytes:
    """11H-F231 采集任务配置（安徽分钟级采集扩展）。

    数据单元：任务号(1B) 启用/删除(1B) 协议类型(1B) 采集周期(1B)
              [单相表组][三相表组][其他表组]
    每组：固定值(1B 0x00/0x01/0x02) 数据项数量(1B)
          预计回复总长度(2B BIN 小端) 数据项标识(4B each) 回复长度(1B each)
    删除时数据项数量填 0，无后续字段；三组固定值始终写出。
    """
    action_raw = params.get("action", "enable")
    action = {"enable": 1, "delete": 0, 1: 1, 0: 0}.get(
        action_raw if not isinstance(action_raw, int) else action_raw)
    if action is None:
        raise ValueError(f"action 非法: {action_raw!r}（应为 enable/delete 或 0/1）")
    # 安徽方案：0xFF=全部任务，仅删除时允许（《安徽集中器交互报文》§10）
    if action == 0 and int(params.get("task_no", 0)) == 0xFF:
        task_no = 0xFF
    else:
        task_no = _u8(params.get("task_no", 0), "task_no", 1, 15)
    protocol = _u8(params.get("protocol", 2), "protocol", 2, 3)
    cycle = _u8(params.get("cycle_min", 0), "cycle_min", 0, 0xFF)

    items = params.get("items") or []
    # 按 meter_type 分组（0单相 → 1三相 → 2其他），保持组内原序
    groups = {0: [], 1: [], 2: []}
    for it in items:
        mt = _u8(it.get("meter_type", 0), "items[].meter_type", 0, 2)
        groups[mt].append(it)

    out = bytearray([task_no, action, protocol, cycle])
    for mt, fixed in ((0, 0x00), (1, 0x01), (2, 0x02)):
        g = groups[mt]
        out.append(fixed)
        out.append(_u8(len(g), f"meter_type={mt} 数据项数量", 0, 0xFF))
        if not g:
            continue
        total = sum(_u16(it.get("reply_len", 0), "items[].reply_len") for it in g)
        out += total.to_bytes(2, "little")
        for it in g:
            item_hex = str(it.get("item", "")).replace(" ", "")
            if len(item_hex) != 8:
                raise ValueError(f"数据项标识必须为 4B hex: {it.get('item')!r}")
            out += bytes.fromhex(item_hex)
            out.append(_u8(it.get("reply_len", 0), "items[].reply_len"))
    return bytes(out)


def _encode_11f232(params: dict) -> bytes:
    """11H-F232 采集任务关联档案配置。

    数据单元：任务号(1B) 档案个数(2B BIN 小端) 档案地址(6B BCD × N)。
    """
    task_no = _u8(params.get("task_no", 0), "task_no", 1, 15)
    meters = params.get("meters") or []
    out = bytearray([task_no])
    out += _u16(len(meters), "档案个数").to_bytes(2, "little")
    for m in meters:
        # 支持 {"addr": "..."} 或直接 BCD 字符串
        addr = m.get("addr") if isinstance(m, dict) else m
        out += _bcd_bytes(addr, "档案地址")
    return bytes(out)


# ---------------------------------------------------------------------------
# 编码辅助（全量模板共用）
# ---------------------------------------------------------------------------

def _req(params: dict, key: str, hint: str = ""):
    """取必填参数，缺失抛 ValueError。"""
    v = params.get(key)
    if v is None:
        raise ValueError(f"缺少参数 {key}" + (f"（{hint}）" if hint else ""))
    return v


def _hex_bytes(v, name: str) -> bytes:
    """hex 字符串（可含空格）→ bytes。"""
    s = str(v).replace(" ", "")
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise ValueError(f"{name} 必须为 hex 字符串: {v!r}")


def _int_to_bcd(v, name: str, hi: int = 99) -> int:
    """十进制两位数 → BCD（59 → 0x59）。"""
    v = int(v)
    if not (0 <= v <= hi):
        raise ValueError(f"{name}={v} 越界 [0,{hi}]")
    return ((v // 10) << 4) | (v % 10)


def _time6_bytes(params: dict) -> bytes:
    """BCD 时间 6B（秒分时日月年）。支持 {"sec",...,"year"} 或 {"time": "SSMMHHDDMMYY"}。"""
    if "time" in params:
        s = str(params["time"]).replace(" ", "").replace(":", "").replace("-", "")
        if len(s) != 12 or not s.isdigit():
            raise ValueError(f"time 须为 12 位数字（秒分时日月年各2位）: {params['time']!r}")
        return _str_to_bcd(s, 6)
    return bytes([
        _int_to_bcd(params.get("sec", 0), "sec", 59),
        _int_to_bcd(params.get("min", 0), "min", 59),
        _int_to_bcd(params.get("hour", 0), "hour", 23),
        _int_to_bcd(params.get("day", 0), "day", 31),
        _int_to_bcd(params.get("mon", 0), "mon", 12),
        _int_to_bcd(params.get("year", 0), "year", 99),
    ])


def _rate_word(unit: int, rate: int) -> int:
    """通信速率字：D15=速率单位标识(1=kbps)，D14~D0=速率。"""
    return ((int(unit) & 0x01) << 15) | (int(rate) & 0x7FFF)


def _nodes_list(params: dict, key: str = "nodes") -> list:
    v = params.get(key)
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"缺少参数 {key}（应为列表）")
    return list(v)


def _nid3(v, name: str) -> bytes:
    """3B 网络标识号 NID（int 0~16777215 或 3B hex 串）→ 小端 3B。"""
    if isinstance(v, str):
        b = _hex_bytes(v, name)
        if len(b) != 3:
            raise ValueError(f"{name} 须为 3B hex: {v!r}")
        return b
    iv = int(v)
    if not (0 <= iv <= 0xFFFFFF):
        raise ValueError(f"{name}={iv} 越界 [0,16777215]")
    return iv.to_bytes(3, "little")


# 下行无数据单元的查询/控制类 (afn, fn)（上行应答另按方向处理）
_NO_UNIT_DOWN = {
    (0x01, 1), (0x01, 2), (0x01, 3),                                  # 初始化
    (0x03, 1), (0x03, 2), (0x03, 4), (0x03, 5), (0x03, 7), (0x03, 8),  # 查询
    (0x03, 10), (0x03, 12), (0x03, 16), (0x03, 100),
    (0x04, 2),                                                        # 从节点点名
    (0x10, 1), (0x10, 4), (0x10, 9), (0x10, 100), (0x10, 104), (0x10, 111),
    (0x11, 6), (0x11, 101), (0x11, 102),                              # 路由设置
    (0x12, 1), (0x12, 2), (0x12, 3),                                  # 路由控制
}


def _encode_app_data(afn: int, fn: int, params: Optional[dict],
                     direction: str = "down") -> bytes:
    """按 AFN/Fn 把业务参数编码为应用数据单元字节（Q/GDW 10376.2 全量模板）。

    覆盖标准 §4/§5 定义的 73 个 Fn（含安徽分钟级采集扩展 F230/F231/F232）。
    双向格式不同的 Fn 按 direction 编码；下行查询类无数据单元返回 b""。
    上行应答类（04H/05H/11H/12H 的应答）标准规定回 AFN=00H 确认/否认帧，
    对其上行方向抛 UnsupportedFn 并提示改用 (0x00,1)。
    多字节 BIN 一律小端（标准备注1）。未覆盖的 (afn, fn) 抛 UnsupportedFn。
    """
    params = params or {}
    afn = int(afn) & 0xFF
    fn = int(fn)
    up = direction == "up"

    def u16le(v, name):
        return _u16(v, name).to_bytes(2, "little")

    # ---- 00H 确认/否认（上行应答；上下行同构） ----
    if (afn, fn) == (0x00, 1):
        status = params.get("status")
        if status in (None, 0, "0", "confirm", "ok"):
            if params.get("wait") is not None or params.get("channels") is not None:
                b0 = (0x80 if params.get("processed") else 0) \
                     | (_u8(params.get("channels", 0), "channels") & 0x7F)
                out = bytearray([b0, 0, 0, 0])
                out += u16le(params.get("wait", 0), "wait")
                return bytes(out)
            return b""
        if status in (1, "1", "deny", "ng"):
            return b"\x01"
        raise ValueError(f"00H-F1 status 非法: {status!r}")
    if (afn, fn) == (0x00, 2):
        return bytes([_u8(_req(params, "err"), "err")])

    # ---- 02H 数据转发 F1（上下行同构） ----
    if (afn, fn) == (0x02, 1):
        payload = _hex_bytes(_req(params, "payload", "转发报文 hex"), "payload")
        if len(payload) > 255:
            raise ValueError(f"payload 长度 {len(payload)} 超过 1B 长度域上限 255")
        return bytes([_u8(_req(params, "protocol"), "protocol", 0, 3),
                      len(payload)]) + payload

    if not up:
        # ================= 下行 =================
        if (afn, fn) in _NO_UNIT_DOWN:
            return b""
        if (afn, fn) == (0x03, 3):  # 查询从节点侦听信息
            return bytes([_u8(_req(params, "start"), "start"),
                          _u8(_req(params, "count"), "count", 0, 16)])
        if (afn, fn) == (0x03, 6):  # 设置主节点干扰持续时间
            return bytes([_u8(_req(params, "duration"), "duration")])
        if (afn, fn) == (0x03, 9):  # 通信延时广播时长查询（带报文）
            payload = _hex_bytes(_req(params, "payload"), "payload")
            return bytes([_u8(_req(params, "protocol"), "protocol", 0, 3),
                          len(payload)]) + payload
        if (afn, fn) == (0x03, 11):  # 查询 AFN 索引
            return bytes([_u8(_req(params, "afn"), "afn")])
        if (afn, fn) == (0x04, 1):  # 发送测试
            return bytes([_u8(_req(params, "duration"), "duration")])
        if (afn, fn) == (0x04, 3):  # 报文通信测试
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            return bytes([_u8(_req(params, "rate"), "rate"),
                          *_bcd_bytes(_req(params, "addr"), "目标地址"),
                          _u8(_req(params, "protocol"), "protocol", 0, 3),
                          len(payload)]) + payload
        if (afn, fn) == (0x05, 1):
            return _bcd_bytes(_req(params, "addr"), "主节点地址")
        if (afn, fn) == (0x05, 2):
            return bytes([_u8(_req(params, "enable"), "enable", 0, 1)])
        if (afn, fn) == (0x05, 3):
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            return bytes([_u8(_req(params, "ctrl"), "ctrl", 0, 3),
                          len(payload)]) + payload
        if (afn, fn) == (0x05, 4):
            return bytes([_u8(_req(params, "timeout"), "timeout")])
        if (afn, fn) == (0x05, 5):
            return bytes([_u8(_req(params, "channel"), "channel", 0, 255),
                          _u8(_req(params, "power"), "power")])
        if (afn, fn) == (0x05, 6):
            return bytes([_u8(_req(params, "enable"), "enable", 0, 1)])
        if (afn, fn) == (0x05, 16):
            return bytes([_u8(_req(params, "band"), "band", 0, 3)])
        if (afn, fn) == (0x05, 100):
            return bytes([_u8(_req(params, "threshold"), "threshold", 0, 255)])
        if (afn, fn) == (0x05, 101):
            return _time6_bytes(params)
        if (afn, fn) == (0x05, 200):
            return bytes([_u8(_req(params, "enable"), "enable", 0, 1)])
        if (afn, fn) == (0x10, 2):  # 查询从节点信息
            out = bytearray()
            out += u16le(params.get("start", 0), "start")
            out.append(_u8(params.get("count", 0), "count"))
            return bytes(out)
        if (afn, fn) == (0x10, 3):  # 指定从节点的上一级中继路由信息
            return _bcd_bytes(_req(params, "addr"), "从节点地址")
        if (afn, fn) in ((0x10, 5), (0x10, 6), (0x10, 7),
                         (0x10, 21), (0x10, 31), (0x10, 101), (0x10, 112)):
            # 起始序号 2B 小端 + 数量 1B
            out = bytearray()
            out += u16le(params.get("start", 0), "start")
            out.append(_u8(params.get("count", 0), "count"))
            return bytes(out)
        if (afn, fn) == (0x10, 40):  # 流水线查询 ID
            return bytes([_u8(_req(params, "dev_type"), "dev_type", 1, 7),
                          *_hex_bytes(_req(params, "addr"), "节点地址"),
                          _u8(_req(params, "id_type"), "id_type", 1, 2)])
        if (afn, fn) == (0x10, 230):  # 安徽扩展：采集任务数量查询
            return b""
        if (afn, fn) == (0x10, 231):  # 安徽扩展：查询任务配置
            return bytes([
                _u8(params.get("task_no", 0), "task_no", 1, 15),
                _u8(params.get("protocol", 2), "protocol", 2, 3),
            ])
        if (afn, fn) == (0x11, 1):  # 添加从节点
            if "nodes" in params:  # 国网格式：数量n + (地址+协议)×n
                nodes = _nodes_list(params)
                out = bytearray([_u8(len(nodes), "nodes")])
                for nd in nodes:
                    out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                    out.append(_u8(nd.get("protocol", 2), "protocol", 0, 3))
                return bytes(out)
            # 安徽扩展单节点 action 格式（与已验证帧兼容）
            action_raw = params.get("action", "add")
            action = {"add": 1, "delete": 0, 1: 1, 0: 0}.get(
                action_raw if not isinstance(action_raw, int) else action_raw)
            if action is None:
                raise ValueError(f"action 非法: {action_raw!r}")
            out = bytearray([action])
            addr = params.get("addr", params.get("sta"))
            out += _bcd_bytes(addr, "档案地址")
            out.append(_u8(params.get("protocol", 2), "protocol", 2, 3))
            return bytes(out)
        if (afn, fn) == (0x11, 2):  # 删除从节点
            meters = params.get("meters")
            if meters is None and "addr" in params:
                meters = [params["addr"]]
            if meters is None:
                raise ValueError("缺少参数 addr/meters")
            out = bytearray([_u8(len(meters), "从节点数量")])
            for m in meters:
                out += _bcd_bytes(m, "从节点地址")
            return bytes(out)
        if (afn, fn) == (0x11, 3):  # 设置固定中继路径
            relays = params.get("relays") or []
            out = bytearray(_bcd_bytes(_req(params, "addr"), "从节点地址"))
            out.append(_u8(len(relays), "中继级别", 0, 15))
            for r in relays:
                out += _bcd_bytes(r, "中继地址")
            return bytes(out)
        if (afn, fn) == (0x11, 4):  # 设置路由工作模式
            out = bytearray([_u8(_req(params, "mode"), "mode")])
            out += _rate_word(params.get("rate_unit", 0),
                              _u16(_req(params, "rate"), "rate")).to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x11, 5):  # 激活从节点主动注册
            out = bytearray(_time6_bytes(params))
            out += u16le(_req(params, "duration"), "duration")
            out.append(_u8(_req(params, "retry"), "retry"))
            out.append(_u8(_req(params, "slices"), "slices"))
            return bytes(out)
        if (afn, fn) == (0x11, 100):  # 设置网络规模
            return u16le(_req(params, "scale"), "scale")
        if (afn, fn) == (0x11, 231):
            return _encode_11f231(params)
        if (afn, fn) == (0x11, 232):
            return _encode_11f232(params)
        if (afn, fn) == (0x13, 1):  # 监控从节点（下行）
            subs = params.get("subs") or []
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            out = bytearray([_u8(_req(params, "protocol"), "protocol", 0, 3),
                             _u8(params.get("delay_flag", 0), "delay_flag", 0, 1),
                             _u8(len(subs), "subs")])
            for s in subs:
                out += _bcd_bytes(s, "附属节点地址")
            out.append(len(payload))
            out += payload
            return bytes(out)
        if (afn, fn) == (0x14, 1):  # 路由请求抄读内容（下行应答）
            payload = _hex_bytes(params.get("payload", ""), "payload") \
                if params.get("payload") else b""
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            subs = params.get("subs") or []
            out = bytearray([_u8(_req(params, "flag"), "flag", 0, 2),
                             _u8(params.get("delay_flag", 0), "delay_flag", 0, 1),
                             len(payload)])
            out += payload
            out.append(_u8(len(subs), "subs"))
            for s in subs:
                out += _bcd_bytes(s, "附属节点地址")
            return bytes(out)
        if (afn, fn) == (0x14, 2):  # 路由请求集中器时钟（下行应答）
            return _time6_bytes(params)
        if (afn, fn) == (0x14, 3):  # 依通信延时修正通信数据（下行应答）
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            return bytes([len(payload)]) + payload
        if (afn, fn) == (0x14, 4):  # 路由请求交采信息（下行应答）
            content = _hex_bytes(params.get("content", ""), "content") \
                if params.get("content") else b""
            return bytes([_u8(_req(params, "type"), "type", 1, 2),
                          *_hex_bytes(_req(params, "item"), "item")]) + content
        if (afn, fn) == (0x15, 1):  # 文件传输方式1（下行）
            fdata = _hex_bytes(_req(params, "data"), "data")
            seg_id = _hex_bytes(_req(params, "seg_id"), "seg_id")
            if len(seg_id) != 4:
                raise ValueError(f"seg_id 须为 4B hex: {params.get('seg_id')!r}")
            out = bytearray([_u8(_req(params, "file_id"), "file_id"),
                             _u8(params.get("attr", 0), "attr", 0, 1),
                             _u8(params.get("cmd", 0), "cmd")])
            out += u16le(_req(params, "total_segs"), "total_segs")
            out += seg_id
            out += u16le(params.get("seg_len", len(fdata)), "seg_len")
            out += fdata
            return bytes(out)
        if (afn, fn) == (0xF1, 1):  # 并发抄表（下行：规约+保留+L2+DATA）
            payload = _hex_bytes(_req(params, "payload"), "payload")
            out = bytearray([_u8(_req(params, "protocol"), "protocol", 0, 3), 0x00])
            out += u16le(len(payload), "payload")
            out += payload
            return bytes(out)
    else:
        # ================= 上行 =================
        if (afn, fn) in ((0x04, 1), (0x04, 2), (0x04, 3), (0x05, 1), (0x05, 2),
                         (0x05, 3), (0x05, 4), (0x05, 5), (0x05, 6), (0x05, 16),
                         (0x05, 100), (0x05, 101), (0x05, 200),
                         (0x11, 1), (0x11, 2), (0x11, 3), (0x11, 4), (0x11, 5),
                         (0x11, 100), (0x12, 1), (0x12, 2), (0x12, 3)):
            raise UnsupportedFn(
                f"0x{afn:02X}-F{fn} 上行应答为 AFN=00H 确认/否认帧，请构 (0x00,1)")
        if (afn, fn) == (0x10, 104):
            raise UnsupportedFn("10H-F104 上行格式蒸馏文档未定义，请用 data.raw 透传")
        if (afn, fn) == (0x02, 1):  # 同下行
            return _encode_app_data(0x02, 1, params, "down")
        if (afn, fn) == (0x03, 1):
            ver = _hex_bytes(_req(params, "version"), "version")
            if len(ver) != 2:
                raise ValueError("version 须为 2B hex")
            return bytes([
                *str(_req(params, "vendor"))[:2].ljust(2).encode("ascii"),
                *str(_req(params, "chip"))[:2].ljust(2).encode("ascii"),
                _int_to_bcd(_req(params, "day"), "day", 31),
                _int_to_bcd(_req(params, "month"), "month", 12),
                _int_to_bcd(_req(params, "year"), "year", 99),
                ver[0], ver[1],
            ])
        if (afn, fn) == (0x03, 2):
            return bytes([_u8(_req(params, "noise"), "noise", 0, 15)])
        if (afn, fn) == (0x03, 3):
            nodes = _nodes_list(params)
            out = bytearray([_u8(_req(params, "total"), "total"), len(nodes)])
            for nd in nodes:
                out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                out.append((_u8(nd.get("quality", 0), "quality", 0, 15) << 4)
                           | _u8(nd.get("relay", 0), "relay", 0, 15))
                out.append(_u8(nd.get("listen", 0), "listen"))
            return bytes(out)
        if (afn, fn) == (0x03, 4):
            return _bcd_bytes(_req(params, "addr"), "主节点地址")
        if (afn, fn) == (0x03, 5):
            rates = params.get("rates") or []
            out = bytearray([
                ((_u8(params.get("mode", 0), "mode", 0, 3)) << 6)
                | ((_u8(params.get("channel", 0), "channel", 0, 3)) << 4)
                | _u8(len(rates), "rates", 0, 15),
                _u8(params.get("channel_cnt", 0), "channel_cnt", 0, 15),
            ])
            for r in rates:
                out += _rate_word(r.get("unit", 0),
                                  _u16(r.get("rate", 0), "rate")).to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x03, 6):
            return bytes([_u8(_req(params, "status"), "status", 0, 1)])
        if (afn, fn) == (0x03, 7):
            return bytes([_u8(_req(params, "timeout"), "timeout")])
        if (afn, fn) == (0x03, 8):
            return bytes([_u8(_req(params, "channel"), "channel"),
                          _u8(_req(params, "power"), "power")])
        if (afn, fn) == (0x03, 9):
            payload = _hex_bytes(_req(params, "payload"), "payload")
            out = bytearray()
            out += u16le(_req(params, "delay"), "delay")
            out.append(_u8(_req(params, "protocol"), "protocol", 0, 3))
            out.append(len(payload))
            out += payload
            return bytes(out)
        if (afn, fn) == (0x03, 10):
            if not params:
                return b""
            out = bytearray(_hex_bytes(_req(params, "mode", "6B 模式字 hex"), "mode"))
            out.append(_u8(_req(params, "monitor_timeout"), "monitor_timeout"))
            out += u16le(_req(params, "broadcast_timeout"), "broadcast_timeout")
            out += u16le(_req(params, "max_frame"), "max_frame")
            out += u16le(_req(params, "max_file_pkt"), "max_file_pkt")
            out.append(_u8(_req(params, "upgrade_wait"), "upgrade_wait"))
            out += _bcd_bytes(_req(params, "addr"), "主节点地址")
            out += u16le(_req(params, "max_nodes"), "max_nodes")
            out += u16le(_req(params, "cur_nodes"), "cur_nodes")
            pub = _hex_bytes(_req(params, "pub_date"), "pub_date")
            rec = _hex_bytes(_req(params, "rec_date"), "rec_date")
            vv = _hex_bytes(_req(params, "vendor_ver"), "vendor_ver")
            if len(pub) != 3 or len(rec) != 3 or len(vv) != 9:
                raise ValueError("pub_date/rec_date 须为 3B，vendor_ver 须为 9B hex")
            out += pub + rec + vv
            for r in params.get("rates") or []:
                out += _rate_word(r.get("unit", 0),
                                  _u16(r.get("rate", 0), "rate")).to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x03, 11):
            bitmap = bytearray(32)
            for f in params.get("support") or []:
                f = int(f)
                if not (1 <= f <= 255):
                    raise ValueError(f"支持的数据单元 F{f} 越界 [1,255]")
                bitmap[(f - 1) >> 3] |= 1 << ((f - 1) & 7)
            return bytes([_u8(_req(params, "afn"), "afn")]) + bytes(bitmap)
        if (afn, fn) == (0x03, 12):
            mid = _hex_bytes(_req(params, "id"), "id")
            if len(mid) > 50:
                raise ValueError("模块ID号最长 50B")
            return bytes([
                *str(_req(params, "vendor"))[:2].ljust(2).encode("ascii"),
                len(mid),
                _u8(_req(params, "id_format"), "id_format", 0, 3),
            ]) + mid
        if (afn, fn) == (0x03, 16):
            return bytes([_u8(_req(params, "band"), "band", 0, 3)])
        if (afn, fn) == (0x03, 100):
            return bytes([_u8(_req(params, "threshold"), "threshold", 0, 255)])
        if (afn, fn) == (0x06, 1):
            nodes = _nodes_list(params)
            out = bytearray([_u8(len(nodes), "nodes")])
            for nd in nodes:
                out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                out.append(_u8(nd.get("protocol", 2), "protocol", 0, 3))
                out += _u16(nd.get("seq", 0), "seq").to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x06, 2):
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            out = bytearray()
            out += _u16(_req(params, "seq"), "seq").to_bytes(2, "little")
            out.append(_u8(_req(params, "protocol"), "protocol", 0, 3))
            out += _u16(params.get("up_len", 0), "up_len").to_bytes(2, "little")
            out.append(len(payload))
            out += payload
            return bytes(out)
        if (afn, fn) == (0x06, 3):
            return bytes([_u8(_req(params, "type"), "type", 1, 3)])
        if (afn, fn) == (0x06, 4):
            nodes = _nodes_list(params)
            out = bytearray([_u8(len(nodes), "nodes")])
            for nd in nodes:
                out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                out.append(_u8(nd.get("protocol", 2), "protocol", 0, 3))
                out += _u16(nd.get("seq", 0), "seq").to_bytes(2, "little")
                out.append(_u8(nd.get("dev_type", 1), "dev_type"))
                subs = nd.get("subs") or []
                out.append(_u8(len(subs), "subs"))
                out.append(_u8(nd.get("frame_subs", len(subs)), "frame_subs"))
                for s in subs:
                    out += _bcd_bytes(s.get("addr", s), "下接节点地址")
                    out.append(_u8(s.get("protocol", 2), "protocol", 0, 3))
            return bytes(out)
        if (afn, fn) == (0x06, 5):
            dt = _u8(_req(params, "dev_type"), "dev_type")
            proto = _u8(_req(params, "protocol"), "protocol", 0, 5)
            if "payload" in params:
                content = _hex_bytes(params["payload"], "payload")
            elif proto == 0x04 and dt == 0x00:  # 采集器停电：表地址(BIN 6B)+带电状态
                content = bytearray()
                for m in _nodes_list(params, "meters"):
                    content += _hex_bytes(_req(m, "addr"), "meters[].addr")
                    content.append(_u8(m.get("power", 0), "power", 0, 1))
            elif proto == 0x04:  # 停复电事件：事件类型 + 通信单元地址序列(BCD 6B)
                content = bytearray([_u8(_req(params, "event"), "event", 1, 2)])
                for a in params.get("addrs") or []:
                    content += _bcd_bytes(a, "通信单元地址")
            elif proto == 0x05:  # 台区改切拒绝节点
                rej = _nodes_list(params, "rejected")
                if len(rej) > 32:
                    raise ValueError("rejected 个数超过 32")
                content = bytearray([len(rej)])
                for r in rej:
                    content += _hex_bytes(_req(r, "addr"), "rejected[].addr")
                    content.append(_u8(r.get("dev_type", 1), "dev_type"))
            else:
                raise ValueError("06H-F5 需提供 payload 或 event/addrs、meters、rejected")
            if len(content) > 255:
                raise ValueError("报文内容超过 1B 长度域上限")
            return bytes([dt, proto, len(content)]) + content
        if (afn, fn) == (0x10, 1):
            return u16le(_req(params, "total"), "total") \
                + u16le(_req(params, "max"), "max")
        if (afn, fn) in ((0x10, 2), (0x10, 5), (0x10, 6)):
            nodes = _nodes_list(params)
            out = bytearray(u16le(_req(params, "total"), "total"))
            out.append(len(nodes))
            for nd in nodes:
                out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                info = nd.get("info")
                if isinstance(info, dict):
                    w = (_u8(info.get("quality", 0), "quality", 0, 15) << 4) \
                        | _u8(info.get("relay", 0), "relay", 0, 15)
                    w |= (_u8(info.get("proto", 0), "proto", 0, 7) << 12) \
                        | (_u8(info.get("phase", 0), "phase", 0, 15) << 8)
                else:
                    w = _u16(info or 0, "info")
                out += w.to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x10, 3):
            nodes = _nodes_list(params)
            out = bytearray([_u8(len(nodes), "nodes")])
            for nd in nodes:
                out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                out += _u16(nd.get("info", 0), "info").to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x10, 4):
            out = bytearray([_u8(_req(params, "status"), "status")])
            out += u16le(_req(params, "total"), "total")
            out += u16le(_req(params, "read"), "read")
            out += u16le(_req(params, "relay_read"), "relay_read")
            out.append(_u8(_req(params, "switch"), "switch"))
            out += _rate_word(0, _u16(_req(params, "rate"), "rate")).to_bytes(2, "little")
            levels = params.get("relay_level") or [0, 0, 0]
            steps = params.get("steps") or [0, 0, 0]
            for lv in levels:
                out.append(_u8(lv, "relay_level"))
            for st in steps:
                out.append(_u8(st, "steps"))
            return bytes(out)
        if (afn, fn) == (0x10, 7):
            nodes = _nodes_list(params)
            out = bytearray(u16le(_req(params, "total"), "total"))
            out.append(len(nodes))
            for nd in nodes:
                addr = _hex_bytes(_req(nd, "addr"), "nodes[].addr")
                if len(addr) != 6:
                    raise ValueError("nodes[].addr 须为 6B hex")
                out += addr
                out.append(_u8(nd.get("node_type", 0), "node_type"))
                out += str(nd.get("vendor", "  ")).encode("ascii", "replace")[:2].ljust(2)
                mid = _hex_bytes(nd.get("id", ""), "id")
                out.append(len(mid))
                out.append(_u8(nd.get("id_format", 2), "id_format", 0, 3))
                out += mid
            return bytes(out)
        if (afn, fn) in ((0x10, 9), (0x10, 100)):
            return u16le(_req(params, "scale"), "scale")
        if (afn, fn) == (0x10, 21):
            nodes = _nodes_list(params)
            out = bytearray(u16le(_req(params, "total"), "total"))
            out += u16le(params.get("start", 0), "start")
            out.append(len(nodes))
            for nd in nodes:
                addr = _hex_bytes(_req(nd, "addr"), "nodes[].addr")
                if len(addr) != 6:
                    raise ValueError("nodes[].addr 须为 6B hex")
                out += addr
                out += _u16(_req(nd, "tei"), "tei").to_bytes(2, "little")
                out += _u16(nd.get("proxy", 0), "proxy").to_bytes(2, "little")
                role = _u8(nd.get("role", 0), "role", 0, 15)
                level = _u8(nd.get("level", 0), "level", 0, 15)
                out.append((role << 4) | level)
            return bytes(out)
        if (afn, fn) == (0x10, 31):
            nodes = _nodes_list(params)
            out = bytearray(u16le(_req(params, "total"), "total"))
            out += u16le(params.get("start", 0), "start")
            out.append(len(nodes))
            for nd in nodes:
                addr = _hex_bytes(_req(nd, "addr"), "nodes[].addr")
                if len(addr) != 6:
                    raise ValueError("nodes[].addr 须为 6B hex")
                out += addr
                out += _u16(nd.get("info", 0), "info").to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x10, 40):
            mid = _hex_bytes(_req(params, "id"), "id")
            return bytes([_u8(_req(params, "dev_type"), "dev_type", 1, 7),
                          *_hex_bytes(_req(params, "addr"), "节点地址"),
                          _u8(_req(params, "id_type"), "id_type", 1, 2),
                          len(mid)]) + mid
        if (afn, fn) == (0x10, 101):
            nodes = _nodes_list(params)
            out = bytearray(u16le(_req(params, "total"), "total"))
            out.append(len(nodes))
            for nd in nodes:
                out += _bcd_bytes(nd.get("addr", nd.get("sta")), "从节点地址")
                out += _u16(nd.get("info", 0), "info").to_bytes(2, "little")
                ver = _hex_bytes(nd.get("ver", "000000"), "ver")
                if len(ver) != 3:
                    raise ValueError("ver 须为 3B hex")
                out += ver
            return bytes(out)
        if (afn, fn) == (0x10, 111):
            neighbors = params.get("neighbors") or []
            out = bytearray([_u8(len(neighbors) + 1, "nodes")])
            out += _nid3(_req(params, "self_nid"), "self_nid")
            out += _hex_bytes(_req(params, "self_master"), "self_master")
            for nb in neighbors:
                out += _nid3(nb, "neighbors[]")
            return bytes(out)
        if (afn, fn) == (0x10, 112):
            nodes = _nodes_list(params)
            out = bytearray(u16le(_req(params, "total"), "total"))
            out += u16le(params.get("start", 0), "start")
            out.append(len(nodes))
            for nd in nodes:
                addr = _hex_bytes(_req(nd, "addr"), "nodes[].addr")
                if len(addr) != 6:
                    raise ValueError("nodes[].addr 须为 6B hex")
                chip = _hex_bytes(_req(nd, "chip_id"), "chip_id")
                if len(chip) != 24:
                    raise ValueError("chip_id 须为 24B hex")
                ver = _hex_bytes(nd.get("ver", "0000"), "ver")
                if len(ver) != 2:
                    raise ValueError("ver 须为 2B hex")
                out += addr
                out.append(_u8(nd.get("dev_type", 1), "dev_type"))
                out += chip
                out += ver
            return bytes(out)
        if (afn, fn) == (0x13, 1):
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            out = bytearray()
            out += _u16(_req(params, "up_len"), "up_len").to_bytes(2, "little")
            out.append(_u8(_req(params, "protocol"), "protocol", 0, 3))
            out.append(len(payload))
            out += payload
            return bytes(out)
        if (afn, fn) == (0x14, 1):
            out = bytearray([_u8(_req(params, "phase"), "phase", 0, 3)])
            out += _bcd_bytes(_req(params, "addr"), "从节点地址")
            out += _u16(_req(params, "seq"), "seq").to_bytes(2, "little")
            return bytes(out)
        if (afn, fn) == (0x14, 2):  # 上行请求：无数据单元
            return b""
        if (afn, fn) == (0x14, 3):
            payload = _hex_bytes(_req(params, "payload"), "payload")
            if len(payload) > 255:
                raise ValueError("payload 长度超过 1B 长度域上限")
            out = bytearray(_bcd_bytes(_req(params, "addr"), "从节点地址"))
            out += _u16(_req(params, "delay"), "delay").to_bytes(2, "little")
            out.append(len(payload))
            out += payload
            return bytes(out)
        if (afn, fn) == (0x14, 4):
            return bytes([_u8(_req(params, "type"), "type", 1, 2),
                          *_hex_bytes(_req(params, "item"), "item")])
        if (afn, fn) == (0x15, 1):
            seg_id = _hex_bytes(_req(params, "seg_id"), "seg_id")
            if len(seg_id) != 4:
                raise ValueError(f"seg_id 须为 4B hex: {params.get('seg_id')!r}")
            return seg_id
        if (afn, fn) == (0xF1, 1):  # 并发抄表应答（上行：规约+L2+DATA）
            payload = _hex_bytes(_req(params, "payload"), "payload")
            out = bytearray([_u8(_req(params, "protocol"), "protocol", 0, 3)])
            out += u16le(len(payload), "payload")
            out += payload
            return bytes(out)

    raise UnsupportedFn(
        f"未覆盖 Fn 0x{afn:02X}-F{fn}（非标准数据单元标识，契约约定明确报错）")


def encode_app_data(afn: int, fn: int, params: Optional[dict] = None,
                    direction: str = "down") -> bytes:
    """对外导出：参数 → 应用数据字节（纯函数）。

    direction: "down"（集中器→模块，默认）/ "up"（模块→集中器应答）。
    """
    return _encode_app_data(afn, fn, params, direction)


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
            frame.items.extend(_app_items(afn, fn, appdata, direction))
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
        if "params" in data:
            appdata = encode_app_data(afn, fn, data["params"],
                                      direction=req.get("direction", "down"))
        elif "raw" in data:
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
