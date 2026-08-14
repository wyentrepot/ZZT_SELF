"""DL/T 645-2007 多功能电能表通信规约适配器。

帧格式：68H | 地址域A0..A5(6B,BCD) | 68H | 控制码C | 长度L | 数据域 | CS | 16H
- 起始符 0x68，帧结束 0x16；控制码 bit7=方向、bit6=异常、bit5=后续帧、bit0-4=功能码
- 数据域（读数据类）= DI0..DI3(4B,小端) + 数据；DI 查字典得语义/单位/倍率
- 转义：0x68/0x1B/0x16 线上传输时前加 0x1B（decode 内部反转义）
"""
import os
from parser_lib.core.adapter import ProtocolAdapter, ProtocolFrame, DataField, ExtractResult

_ESCAPE_SET = frozenset([0x68, 0x1B, 0x16])

_FUNC_NAMES = {
    0x01: "读数据", 0x02: "读后续数据", 0x03: "读通信地址",
    0x04: "写数据", 0x05: "写通信地址", 0x06: "冻结命令",
    0x07: "电表清零", 0x08: "广播校时", 0x09: "更改通信速率",
    0x0A: "修改最大需量周期", 0x0B: "清零最大需量", 0x0C: "清电能表",
}

# 不进行 +33H 加密的功能码（广播命令、读通信地址）
_NO_ENCRYPT_FUNCS = frozenset([0x03, 0x08])


def _unescape(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b == 0x1B and i + 1 < n:
            out.append(data[i + 1])
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def _bcd_decode(data: bytes) -> int:
    """BCD 解码（645 数据域低字节在前，需反转后读取）。"""
    s = ""
    for b in reversed(data):
        s += f"{(b >> 4) & 0xF}{b & 0xF}"
    return int(s) if s else 0


def build_frame(addr_bytes: bytes, control: int, data_domain: bytes) -> bytes:
    """构造一个 645 帧（未转义）。供测试与 fixtures 生成使用。"""
    body = bytes([0x68]) + addr_bytes + bytes([0x68, control, len(data_domain)]) + data_domain
    cs = sum(body) % 256
    return body + bytes([cs, 0x16])


def build_frame_escaped(addr_bytes: bytes, control: int, data_domain: bytes) -> bytes:
    """构造一个 645 帧（传输层转义：数据域与 CS 中的 68/1B/16 前加 0x1B）。

    CS 仍按逻辑（未转义）字节计算，转义是最后传输层操作，decode 内部会反转义还原。
    """
    logical = build_frame(addr_bytes, control, data_domain)  # 含逻辑 CS
    out = bytearray()
    out.append(logical[0])     # 起始符 0x68 不转义
    out += logical[1:7]        # 地址域不转义
    out.append(logical[7])     # 第二起始符 0x68 不转义
    for b in logical[8:-1]:    # 控制码起至 CS（不含结尾 0x16）转义
        if b in _ESCAPE_SET:
            out += bytes([0x1B, b])
        else:
            out.append(b)
    out.append(logical[-1])    # 结束符 0x16 不转义
    return bytes(out)


class DLT645Adapter(ProtocolAdapter):
    protocol = "645"

    def __init__(self, metadata_store=None):
        self.metadata_store = metadata_store

    # ---------- 切帧（含结构校验；支持转义与半包） ----------
    def try_extract(self, buf: bytes):
        res = self._try_extract_escaped(buf)
        if res is not None:
            return res
        # 未转义抓包流回退：CS/数据中的 0x1B 不能被当作转义标记。
        # 转义帧的原始字节数大于 12+L，plain 解析会因结束符位置不符而失败，
        # 因此该回退不影响转义帧的正常识别。
        return self._try_extract_plain(buf)

    def _try_extract_plain(self, buf: bytes):
        n = len(buf)
        if n == 0 or buf[0] != 0x68:
            return None
        try:
            if buf[0] != 0x68:
                return None
            if buf[7] != 0x68:
                return None
            L = buf[9]
            if not (0 <= L <= 200):
                return None
            if buf[11 + L] != 0x16:      # 结束符直接定位（无转义）
                return None
            consumed = 12 + L
            return ExtractResult(raw=buf[:consumed], consumed=consumed)
        except IndexError:
            return None

    def _try_extract_escaped(self, buf: bytes):
        n = len(buf)
        if n == 0 or buf[0] != 0x68:
            return None
        start = -1
        i = 0
        while i < n:                       # 找未转义的起始符 0x68
            b = buf[i]
            if b == 0x1B:
                i += 2
                continue
            if b == 0x68:
                start = i
                break
            i += 1
        if start < 0:
            return None
        pos = start

        def rb():
            nonlocal pos
            if pos >= n:
                raise IndexError
            b = buf[pos]
            if b == 0x1B:                  # 转义对：取真实字节，跳 2 原始字节
                if pos + 1 >= n:
                    raise IndexError
                v = buf[pos + 1]
                pos += 2
                return v
            pos += 1
            return b

        try:
            if rb() != 0x68:
                return None
            addr = bytes(rb() for _ in range(6))
            if rb() != 0x68:               # 第二个起始符
                return None
            C = rb()
            L = rb()
            if not (0 <= L <= 200):
                return None
            data = bytes(rb() for _ in range(L))
            cs = rb()                      # 校验和 CS（切帧阶段不强制校验）
            if rb() != 0x16:               # 结束符
                return None
            consumed = pos - start
            return ExtractResult(raw=bytes(buf[start:pos]), consumed=consumed)
        except IndexError:
            return None                    # 字节不足 → 半包，等待续接

    # ---------- 嗅探打分 ----------
    @staticmethod
    def _logical_bytes(raw: bytes) -> bytes:
        """转义解析成立时返回反转义字节，否则按未转义原始字节处理。"""
        lb = _unescape(raw)
        if len(lb) >= 12 and lb[0] == 0x68 and lb[7] == 0x68 and lb[-1] == 0x16:
            L = lb[9]
            if 0 <= L <= 200 and len(lb) == 12 + L:
                return lb
        return raw

    def confidence(self, raw: bytes) -> float:
        # 645 帧结构（FT1.2）：68 | A0..A5(6B) | 68 | C | L | 数据域 | CS | 16
        # 识别特征（索引 95 帧类别 · 645 族）：起始 68H、地址域后第二 68H(pos7)、
        # 结束 16H、控制码低 4 位为合法功能码(1..B)、CS 校验。
        lb = self._logical_bytes(raw)
        if len(lb) < 12:
            return 0.0
        if lb[0] != 0x68 or lb[7] != 0x68 or lb[-1] != 0x16:
            return 0.0
        # 隔离 1376.2：其第二 68H 在 pos3，而 645 的 pos3 是地址字节 A3，绝不可能是 0x68
        if lb[3] == 0x68:
            return 0.0
        # 长度一致性：帧长 = 12 + L（L=数据域长度，pos9）
        L = lb[9]
        if L > 200 or len(lb) != 12 + L:
            return 0.0
        # 控制码低 4 位为合法功能码 0001..1100（方向/异常/后续位任意）
        if not (0x01 <= (lb[8] & 0x0F) <= 0x0C):
            return 0.3
        # CS 校验通过 → 最高置信；否则结构成立但校验失败
        if sum(lb[:-2]) % 256 == lb[-2]:
            return 1.0
        return 0.4

    # ---------- 解码（协议原生结构） ----------
    def decode(self, raw: bytes) -> ProtocolFrame:
        lb = self._logical_bytes(raw)
        frame = ProtocolFrame(structure="645", raw_hex=raw.hex())
        warnings = []

        addr_bytes = lb[1:7]
        addr_str = "".join(f"{b:02X}" for b in addr_bytes)
        C = lb[8]
        L = lb[9]
        data = lb[10:10 + L]
        cs_idx = 10 + L
        CS = lb[cs_idx] if cs_idx < len(lb) else None

        direction = "上行(从站→主站)" if (C & 0x80) else "下行(主站→从站)"
        is_err = bool(C & 0x40)
        has_more = bool(C & 0x20)
        func = C & 0x0F
        func_name = _FUNC_NAMES.get(func, f"未知功能码({func})")

        frame.fields.append(DataField(name="地址域", value=addr_str, hex=addr_bytes.hex(), raw=addr_bytes.hex()))
        frame.fields.append(DataField(
            name="控制码", value=f"0x{C:02X}", hex=f"{C:02X}", raw=C,
            desc=f"{direction};{'异常' if is_err else '正常'};{'有后续帧' if has_more else '无后续帧'};功能:{func_name}"))
        frame.fields.append(DataField(name="长度域", value=L, hex=f"{L:02X}", raw=L))

        if CS is not None:
            calc = sum(lb[:-2]) % 256
            ok = (calc == CS)
            frame.fields.append(DataField(
                name="校验和CS", value=f"0x{CS:02X}", hex=f"{CS:02X}", raw=CS,
                desc="校验" + ("通过" if ok else f"失败(计算0x{calc:02X})")))
            if not ok:
                warnings.append(f"CS校验失败: 帧内0x{CS:02X} vs 计算0x{calc:02X}")

        # 数据域 +33H 解密（广播校时/读通信地址不加33H）
        if L > 0 and func not in _NO_ENCRYPT_FUNCS:
            data = bytes((b - 0x33) & 0xFF for b in data)

        # 特殊功能码处理（非DI结构的数据域）
        if func == 0x03 and L > 0:
            # 读通信地址应答：数据域为6字节BCD通信地址
            addr_val = "".join(f"{b:02X}" for b in data)
            frame.items.append(DataField(name="通信地址", value=addr_val,
                hex=data.hex(), raw=data.hex(), desc="读通信地址应答"))
        elif func == 0x08 and L > 0:
            # 广播校时：数据域为6字节时间（秒分时日月年，BCD）
            if L >= 6:
                sec, minute, hour, day, month, year = data[:6]
                time_str = f"20{year:02X}年{month:02X}月{day:02X}日 {hour:02X}:{minute:02X}:{sec:02X}"
                frame.items.append(DataField(name="校时时间", value=time_str,
                    hex=data.hex(), raw=data.hex(), desc="广播校时(秒分时日月年)"))
            else:
                frame.items.append(DataField(name="校时数据", value=data.hex(),
                    hex=data.hex(), raw=data.hex()))
        elif L >= 4:
            di0, di1, di2, di3 = data[0], data[1], data[2], data[3]
            di_key = f"{di3:02X}{di2:02X}{di1:02X}{di0:02X}"   # 显示顺序 DI3..DI0
            payload = data[4:]
            meta = self.metadata_store.lookup("645", di_key) if self.metadata_store else None
            if meta:
                dt = meta.get("data_type", "bcd_compact")
                scale = meta.get("scale", 0)
                if dt == "bcd_compact" and payload:
                    value = _bcd_decode(payload) / (10 ** scale) if scale else _bcd_decode(payload)
                    value_hex = payload.hex()
                else:
                    value, value_hex = payload.hex(), payload.hex()
                frame.items.append(DataField(
                    name=meta.get("name", di_key), value=value, unit=meta.get("unit"),
                    hex=value_hex, raw=payload.hex(), desc=meta.get("desc", "")))
            else:
                frame.items.append(DataField(
                    name=f"未知DI({di_key})", value=payload.hex(), hex=payload.hex(),
                    raw=payload.hex(), desc="未在字典中登记，保留原始值"))
                warnings.append(f"未知数据标识 DI={di_key}")
        else:
            frame.items.append(DataField(name="数据域(原始)", value=data.hex(), hex=data.hex(), raw=data.hex()))

        frame.warnings = warnings
        return frame
