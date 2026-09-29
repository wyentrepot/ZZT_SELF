"""模拟电表核心脑：645 帧应答引擎 + 表内数据集 + 重庆即装即采状态。

只做协议与状态，不碰串口：handle_frame(原始帧) → 应答帧 + 动作。串口
热切换波特率等物理动作由 serial_channel / api 层执行。

控制码口径（DL/T 645-2007 国标）：
    08H 广播校时(不应答)  11H 读数据  12H 读后续  13H 读通信地址
    14H 写数据  15H 写通信地址  17H 更改通信速率
645-1997 模式：
    01H 读数据(2B DI)  04H 写数据  05H 广播校时(不应答)
    06H 写通信地址  07H 更改通信速率
知识库 01_DLT645_帧格式.md 卡把"更改通信速率"记在 09H（与国标 17H 有
偏差），为兼容既有生态两种都实现为更改速率。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from parser_lib.adapters.adapter_645 import build_frame, build_frame_escaped

_BROADCAST_READ_ADDR = b"\xFE" * 6      # 13H/15H 广播方式使用的地址域（知识库口径）
_BROADCAST_CTRL = b"\x99" * 6           # 广播校时地址域
_BROADCAST_AA = b"\xAA" * 6             # AA×6 广播（STA 绑表状态机 remap645_addr_buff_AA）
# 实测 STA 绑表轮换用 99×6 / AA×6 广播发 13H 读地址（台区实测 2026-09-28，
# H_STA/02/sta aps_addrmap_state.c），三种广播都认
_BROADCAST_ADDRS = (_BROADCAST_READ_ADDR, _BROADCAST_CTRL, _BROADCAST_AA)

_NO_ENCRYPT_CTRL = frozenset([0x08])    # 广播校时数据域不加 33H（2007）

# 速率特征字：单 bit 有效（b0=600 … b7=38400；7200/38400 非标准速率，按扩展接受）
RATE_BITS: Dict[int, int] = {
    0x01: 600, 0x02: 1200, 0x04: 2400, 0x08: 4800,
    0x10: 7200, 0x20: 9600, 0x40: 19200, 0x80: 38400,
}

_ERR_OTHER = 0x01        # 其他错误
_ERR_NO_DATA = 0x02      # 无请求数据

# ---------------------------------------------------------------------------
# 重庆即装即采设备类型（4036 取值）与默认设备描述符
# 来源：知识库 协议层/102_重庆即装即采数据对象与设备类型_帧格式.md §7/§8
# ---------------------------------------------------------------------------
JZJC_TYPE_PRESETS: Dict[int, Dict[str, Any]] = {
    0x10: {"name": "13/20版单相电能表", "descriptor": "DDZY666-Z"},
    0x11: {"name": "698协议物联表", "descriptor": "DDZY666-Z"},
    0x12: {"name": "导轨式电能表", "descriptor": "DTSU666-D"},
    0x13: {"name": "直流表", "descriptor": "DJS666"},
    0x26: {"name": "充电桩计费控制单元", "descriptor": "EVBC-666"},
    0x27: {"name": "充电桩充电控制模块", "descriptor": "EVCC-666"},
    0x28: {"name": "智能量测开关", "descriptor": "LKT666Z-D"},
    0x29: {"name": "分布式电源接入单元", "descriptor": "DFDL33-666"},
}

# 默认数据集（DI 显示序 DI3..DI0 → (数值字符串, 小数位, 字节数)）。
# 费率电能之和 = 总电能，便于对账测试。
_DEFAULT_REGISTERS: Dict[str, Tuple[str, int, int]] = {
    "00010000": ("001234.56", 2, 4),   # 当前正向有功总电能 kWh
    "00010001": ("000123.45", 2, 4),   # 费率1（尖）
    "00010002": ("000345.67", 2, 4),   # 费率2（峰）
    "00010003": ("000456.78", 2, 4),   # 费率3（平）
    "00010004": ("000308.66", 2, 4),   # 费率4（谷）
    "02010100": ("220.1", 1, 2),       # A相电压 V
    "02010200": ("221.0", 1, 2),       # B相电压 V
    "02010300": ("219.8", 1, 2),       # C相电压 V
    "02020100": ("001.234", 3, 3),     # A相电流 A
    "02020200": ("001.223", 3, 3),     # B相电流 A
    "02020300": ("001.245", 3, 3),     # C相电流 A
    "0203FF00": ("00.5432", 4, 3),     # 瞬时总有功功率 kW
}

# 兼容别名：重庆分钟级采集卡记载的口径（04000100H 有功功率 / 00000001H 正向有功电能）
_ALIAS_REGISTERS = {
    "04000100": "0203FF00",
    "00000001": "00010000",
}


def _unescape(data: bytes) -> bytes:
    """645 传输层反转义（1BH 转义对还原），与 adapter_645 口径一致。"""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        if data[i] == 0x1B and i + 1 < n:
            out.append(data[i + 1])
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def _bcd_encode(value: str, scale: int, length: int) -> bytes:
    """数值字符串 → 定长 BCD 字节（低字节在前）。"""
    digits = str(value).strip()
    if scale > 0:
        if "." in digits:
            head, _, frac = digits.partition(".")
            if len(frac) != scale:
                raise ValueError(f"小数位应为 {scale} 位：{value}")
            digits = head + frac
        else:
            digits = digits + "0" * scale
    if not digits.isdigit():
        raise ValueError(f"非 BCD 数值：{value!r}")
    need = length * 2
    if len(digits) > need:
        raise ValueError(f"数值超出 {length} 字节 BCD 范围：{value!r}")
    digits = digits.zfill(need)
    pairs = [digits[i:i + 2] for i in range(0, need, 2)]
    pairs.reverse()
    return bytes(int(p, 16) for p in pairs)


def _parse_addr(addr: str) -> bytes:
    """12 位十六进制地址串 → 地址域字节（A0..A5，低字节在前）。"""
    s = str(addr).strip().upper().replace(" ", "")
    if len(s) != 12 or any(c not in "0123456789ABCDEF" for c in s):
        raise ValueError(f"地址须为 12 位十六进制：{addr!r}")
    return bytes.fromhex(s)


def _bcd_byte(b: int) -> int:
    """单字节 BCD → 整数（0x30 → 30）。"""
    return ((b >> 4) & 0xF) * 10 + (b & 0xF)


class JzjcConfig:
    """重庆即装即采设备信息状态。"""

    def __init__(self):
        self.enabled = True
        self.device_type = 0x10            # 4036 取值（半字节拼接口径：大类×0x10+小类）
        self.descriptor = "DDZY666-Z"      # 设备描述符（ASCII，前 3~4 字符决定类型）
        self.protocol_type = 0x02          # 00H 透明 / 02H 645-2007 / 03H 698.45
        self.seq = 1                       # 06H-F7 上行序号
        self.sub_devices: List[Dict[str, Any]] = []   # 下挂设备：addr/descriptor/protocol_type

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "device_type": f"0x{self.device_type:02X}",
            "device_type_name": JZJC_TYPE_PRESETS.get(self.device_type, {}).get("name", "自定义/备用"),
            "descriptor": self.descriptor,
            "protocol_type": self.protocol_type,
            "seq": self.seq,
            "sub_devices": [dict(sd) for sd in self.sub_devices],
        }


class HandleResult:
    """handle_frame 结果：应答帧（未转义+线上转义两份）+ 动作 + 说明。"""

    def __init__(self, reply: Optional[bytes], note: dict,
                 action: Optional[Tuple[str, Any]] = None):
        self.reply = reply                                # 逻辑帧（未转义）
        self.note = note
        self.action = action                              # 如 ("baud", 9600)

    @property
    def reply_wire(self) -> Optional[bytes]:
        """线上传输帧（数据域+33H 已含、转义后）。"""
        if self.reply is None:
            return None
        return build_frame_escaped(self.reply[1:7], self.reply[8],
                                   self.reply[10:-2])


class MeterBrain:
    """模拟电表：645 从站应答引擎 + 表内状态。"""

    def __init__(self, addr: str = "000000000001", version: str = "2007",
                 baudrate: int = 2400, parity: str = "E", bytesize: int = 8,
                 stopbits: int = 1):
        self._lock = threading.RLock()
        self._addr = _parse_addr(addr)
        self.version = "2007"                 # "2007" | "1997"
        self.set_version(version)
        self.baudrate = int(baudrate)         # 645-2007 RS-485 缺省 2400/E/8/1
        self.parity = str(parity).upper()
        self.bytesize = int(bytesize)
        self.stopbits = int(stopbits)
        self._clock_offset_ms = 0             # 表内时钟 = 系统时间 + 偏移
        self._registers: Dict[str, Dict[str, Any]] = {}
        for di, (val, scale, length) in _DEFAULT_REGISTERS.items():
            self._registers[di] = {"value": val, "scale": scale, "length": length}
        self.jzjc = JzjcConfig()
        self.tx_count = 0
        self.rx_count = 0

    # ------------------------------------------------------------------
    # 状态配置（接口层直调）
    # ------------------------------------------------------------------
    @property
    def addr_bytes(self) -> bytes:
        return self._addr

    @property
    def addr_hex(self) -> str:
        return self._addr.hex().upper()

    def set_address(self, addr: str) -> str:
        with self._lock:
            self._addr = _parse_addr(addr)
            return self.addr_hex

    def set_version(self, version: str) -> str:
        v = str(version).strip()
        if v in ("07", "2007"):
            v = "2007"
        elif v in ("97", "1997"):
            v = "1997"
        else:
            raise ValueError(f"不支持的 645 版本：{version!r}（07/97）")
        with self._lock:
            self.version = v
        return v

    def set_serial_params(self, baudrate: Optional[int] = None,
                          parity: Optional[str] = None,
                          bytesize: Optional[int] = None,
                          stopbits: Optional[int] = None) -> None:
        with self._lock:
            if baudrate is not None:
                self.baudrate = int(baudrate)
            if parity is not None:
                self.parity = str(parity).upper()
            if bytesize is not None:
                self.bytesize = int(bytesize)
            if stopbits is not None:
                self.stopbits = int(stopbits)

    # -- 时钟 -----------------------------------------------------------
    def now(self) -> datetime:
        from datetime import timedelta
        return datetime.now() + timedelta(milliseconds=self._clock_offset_ms)

    def set_clock(self, dt: datetime) -> datetime:
        """设置表内时钟（记录与系统时间的偏移，表时间随之走动）。"""
        from datetime import timedelta
        with self._lock:
            self._clock_offset_ms = int(
                (dt - datetime.now()).total_seconds() * 1000)
            return self.now()

    # -- 数据集 ---------------------------------------------------------
    def get_registers(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {di: dict(item) for di, item in self._registers.items()}

    def set_register(self, di: str, value: str, scale: Optional[int] = None,
                     length: Optional[int] = None) -> Dict[str, Any]:
        """设置/新增数据标识（DI 显示序 DI3..DI0，如 00010000）。"""
        di = str(di).strip().upper().replace(" ", "")
        if len(di) != 8 or any(c not in "0123456789ABCDEF" for c in di):
            raise ValueError(f"DI 须为 8 位十六进制（DI3DI2DI1DI0）：{di!r}")
        with self._lock:
            item = self._registers.get(di, {})
            item.update({
                "value": str(value),
                "scale": int(scale if scale is not None else item.get("scale", 0)),
                "length": int(length if length is not None else item.get("length", 4)),
            })
            _bcd_encode(item["value"], item["scale"], item["length"])  # 先验证再入库
            self._registers[di] = item
            return dict(item)

    # -- 即装即采 -------------------------------------------------------
    def get_jzjc(self) -> dict:
        with self._lock:
            return self.jzjc.to_dict()

    def set_jzjc(self, *, enabled: Optional[bool] = None,
                 device_type: Optional[str] = None,
                 descriptor: Optional[str] = None,
                 protocol_type: Optional[int] = None,
                 seq: Optional[int] = None,
                 sub_devices: Optional[List[dict]] = None) -> dict:
        """更新即装即采配置。device_type 形如 "0x10"；置类型时描述符缺省
        取该类型预设（可随后用 descriptor 覆盖）。"""
        with self._lock:
            if device_type is not None:
                dt = int(str(device_type), 16) if isinstance(device_type, str) else int(device_type)
                if not 0 <= dt <= 0xFFFF:
                    raise ValueError(f"设备类型越界：{device_type!r}")
                self.jzjc.device_type = dt
                preset = JZJC_TYPE_PRESETS.get(dt)
                if preset and descriptor is None:
                    self.jzjc.descriptor = preset["descriptor"]
            if descriptor is not None:
                desc = str(descriptor).strip()
                desc.encode("ascii")     # 描述符须为 ASCII（BIN 传输）
                self.jzjc.descriptor = desc
            if protocol_type is not None:
                pt = int(protocol_type)
                if pt not in (0x00, 0x02, 0x03):
                    raise ValueError(f"协议类型仅 00H/02H/03H：{protocol_type!r}")
                self.jzjc.protocol_type = pt
            if enabled is not None:
                self.jzjc.enabled = bool(enabled)
            if seq is not None:
                self.jzjc.seq = int(seq) & 0xFFFF
            if sub_devices is not None:
                cleaned = []
                for sd in sub_devices:
                    addr = _parse_addr(sd.get("addr", ""))
                    desc = str(sd.get("descriptor", "")).strip()
                    desc.encode("ascii")
                    pt = int(sd.get("protocol_type", self.jzjc.protocol_type))
                    if pt not in (0x02, 0x03):
                        raise ValueError(f"下挂设备协议类型仅 02H/03H：{pt!r}")
                    cleaned.append({"addr": addr.hex().upper(),
                                    "descriptor": desc, "protocol_type": pt})
                if len(cleaned) > 32:
                    raise ValueError("下挂设备数量不应超过 32")
                self.jzjc.sub_devices = cleaned
            return self.jzjc.to_dict()

    def build_jzjc_report(self, style: str = "sta", seq: Optional[int] = None) -> bytes:
        """生成即装即采设备信息上送报文（应用层数据单元，未加链路封装）。

        style="sta"：Q/GDW 11612.43 STA→CCO 事件主动上报数据子域
                     （事件类型 51 + 本体设备 + M 个下挂设备）。
        style="afn06f7"：Q/GDW 1376.2 AFN=06H F7 上行数据单元
                     （从节点数量 n + 公共 11B + 描述符 + M + m + 记录）。
        """
        with self._lock:
            jc = self.jzjc
            desc = jc.descriptor.encode("ascii")
            seq_v = int(seq if seq is not None else jc.seq) & 0xFFFF
            records = bytearray()
            for sd in jc.sub_devices:
                a = bytes.fromhex(sd["addr"])
                d = sd["descriptor"].encode("ascii")
                records += bytes([len(a)]) + a + bytes([len(d)]) + d + bytes([sd["protocol_type"]])
            if style == "sta":
                out = bytearray([51])                      # STA 上报事件类型=51 设备信息
                out += self._addr                          # 设备地址（STA 模块通信地址）6B
                out += bytes([jc.protocol_type])
                out += bytes([len(desc)]) + desc
                out += bytes([len(jc.sub_devices)])
                out += records
            elif style == "afn06f7":
                out = bytearray([1])                       # 上报从节点数量 n=1
                out += self._addr + bytes([jc.protocol_type])
                out += seq_v.to_bytes(2, "little")
                out += bytes([len(desc)]) + desc
                out += bytes([len(jc.sub_devices)])        # 下挂设备数量 M
                out += bytes([len(jc.sub_devices)])        # 本报文传输的节点数量 m
                out += records
            else:
                raise ValueError(f"未知报文风格：{style!r}（sta/afn06f7）")
            return bytes(out)

    # ------------------------------------------------------------------
    # 协议入口
    # ------------------------------------------------------------------
    def handle_hex(self, hex_str: str) -> Optional[HandleResult]:
        """十六进制串（可含空白/FE前导）直接喂给应答引擎。"""
        s = str(hex_str).strip().replace(" ", "").replace("-", "")
        return self.handle_frame(bytes.fromhex(s))

    def handle_frame(self, raw: bytes) -> Optional[HandleResult]:
        """处理一帧主站请求，返回应答（None=不应答）。"""
        with self._lock:
            self.rx_count += 1
        buf = _unescape(bytes(raw or b""))
        i = 0
        while i < len(buf) and buf[i] == 0xFE:     # 去主站唤醒前导 4×FE
            i += 1
        buf = buf[i:]
        note: Dict[str, Any] = {"rx_hex": bytes(raw).hex().upper()}
        if len(buf) < 12 or buf[0] != 0x68 or buf[7] != 0x68 or buf[-1] != 0x16:
            note["error"] = "帧结构不合法"
            return HandleResult(None, note)
        L = buf[9]
        if len(buf) != 12 + L or L > 200:
            note["error"] = "长度域与帧长不符"
            return HandleResult(None, note)
        if sum(buf[:-2]) % 256 != buf[-2]:
            note["error"] = "CS 校验失败，从站丢弃"
            return HandleResult(None, note)
        addr, ctrl, data = buf[1:7], buf[8], buf[10:10 + L]
        note["ctrl"] = f"{ctrl:02X}"
        note["addr"] = addr.hex().upper()
        try:
            if self.version == "2007":
                result = self._handle_2007(addr, ctrl, data, note)
            else:
                result = self._handle_1997(addr, ctrl, data, note)
            if result is not None and result.reply is not None:
                with self._lock:
                    self.tx_count += 1
            return result
        except Exception as exc:                    # 协议入口不向通道层抛异常
            note["error"] = f"帧处理异常: {exc!r}"
            return HandleResult(None, note)

    # -- 应答帧构造 ------------------------------------------------------
    def _reply(self, ctrl: int, data: bytes = b"") -> bytes:
        """正常应答：数据域按 645 规则 +33H 传输（13H 读通信地址应答的
        地址域为原始字节，与知识库示例一致）。"""
        if ctrl != 0x13 and data:
            data = bytes((b + 0x33) & 0xFF for b in data)
        return build_frame(self._addr, ctrl | 0x80, data)

    def _err_reply(self, ctrl: int, err: int) -> bytes:
        return build_frame(self._addr, ctrl | 0xC0,
                           bytes([(err + 0x33) & 0xFF]))

    # -- 645-2007 --------------------------------------------------------
    def _handle_2007(self, addr: bytes, ctrl: int, data: bytes,
                     note: dict) -> Optional[HandleResult]:
        if ctrl == 0x08:                            # 广播校时：99×6，秒分时日月年
            if len(data) >= 6 and addr == _BROADCAST_CTRL:
                self._apply_broadcast_time(data[:6])
                note["func"] = "广播校时"
                note["applied"] = True
                return HandleResult(None, note)
            note["func"] = "广播校时(地址域非广播，忽略)"
            return HandleResult(None, note)

        if addr == self._addr:
            pass                                    # 本表
        elif addr in _BROADCAST_ADDRS and ctrl in (0x13, 0x15):
            pass                                    # 广播读/写地址（FE×6 或 99×6）
        else:
            note["func"] = "非本表地址，不应答"
            note["mine"] = False
            return HandleResult(None, note)

        if ctrl == 0x13:                            # 读通信地址（应答地址）
            note["func"] = "读通信地址"
            return HandleResult(self._reply(0x13, self._addr), note)

        if ctrl == 0x11:                            # 读数据
            return self._read_data(data, note)

        if ctrl == 0x12:                            # 读后续数据：模拟表不支持分帧
            note["func"] = "读后续数据(不支持)"
            return HandleResult(self._err_reply(0x12, _ERR_NO_DATA), note)

        if ctrl == 0x14:                            # 写数据（DI+密码+操作者+数据）
            return self._write_data(data, note)

        if ctrl == 0x15:                            # 写通信地址
            return self._write_address(data, 0x15, note)

        if ctrl in (0x17, 0x09):                    # 更改通信速率（国标17H；09H为知识库兼容口径）
            note["func"] = "更改通信速率" + ("(09H兼容口径)" if ctrl == 0x09 else "")
            return self._change_baudrate(ctrl, data, note)

        note["func"] = f"未知控制码 0x{ctrl:02X}"
        return HandleResult(self._err_reply(ctrl, _ERR_OTHER), note)

    # -- 645-1997 --------------------------------------------------------
    def _handle_1997(self, addr: bytes, ctrl: int, data: bytes,
                     note: dict) -> Optional[HandleResult]:
        if ctrl == 0x05:                            # 97 广播校时
            if len(data) >= 6 and addr == _BROADCAST_CTRL:
                self._apply_broadcast_time(data[:6])
                note["func"] = "广播校时(97)"
                return HandleResult(None, note)
            note["func"] = "广播校时(97, 地址域非广播)"
            return HandleResult(None, note)

        if addr != self._addr:
            di_probe = self._di1997_key(data)
            # 广播读表地址（97 固件口径 01H+C032H）放行
            if not (addr in _BROADCAST_ADDRS and di_probe is not None
                    and di_probe[1] == "C032" and ctrl == 0x01):
                note["func"] = "非本表地址，不应答"
                note["mine"] = False
                return HandleResult(None, note)

        if ctrl == 0x01:                            # 读数据（2B DI；9010 有功总 / C032 表地址）
            di = self._di1997_key(data)
            if di is None:
                return HandleResult(self._err_reply(0x01, _ERR_NO_DATA), note)
            di_wire, key = di
            note["func"] = f"读数据(97 DI={key})"
            if key == "9010":
                value = self._register_value_bcd("00010000")
                if value is None:
                    return HandleResult(self._err_reply(0x01, _ERR_NO_DATA), note)
                return HandleResult(self._reply(0x01, di_wire + value), note)
            if key == "C032":
                return HandleResult(self._reply(0x01, di_wire + self._addr), note)
            return HandleResult(self._err_reply(0x01, _ERR_NO_DATA), note)

        if ctrl == 0x04:                            # 写数据（97 暂不开放写项）
            note["func"] = "写数据(97, 无可写项)"
            return HandleResult(self._err_reply(0x04, _ERR_NO_DATA), note)

        if ctrl == 0x06:                            # 写通信地址
            return self._write_address(data, 0x06, note)

        if ctrl == 0x07:                            # 更改通信速率（97）
            note["func"] = "更改通信速率(97)"
            return self._change_baudrate(ctrl, data, note)

        note["func"] = f"未知控制码 0x{ctrl:02X}(97)"
        return HandleResult(self._err_reply(ctrl, _ERR_OTHER), note)

    # -- 分支实现 --------------------------------------------------------
    def _di1997_key(self, data: bytes) -> Optional[Tuple[bytes, str]]:
        """97 2B 数据标识双解释：先按 +33H 口径（CCO 固件常量 43C3/65F3），
        失败再按原始字节，返回 (线序DI字节, 显示键)。"""
        for raw in (bytes((b - 0x33) & 0xFF for b in data[:2]), data[:2]):
            if len(raw) == 2:
                key = raw[::-1].hex().upper()
                if key in ("9010", "C032"):
                    return raw, key
        return None

    def _read_data(self, data: bytes, note: dict) -> HandleResult:
        if len(data) < 4:
            return HandleResult(self._err_reply(0x11, _ERR_OTHER), note)
        di_wire = bytes((b - 0x33) & 0xFF for b in data[:4])
        key = di_wire[::-1].hex().upper()           # 显示序 DI3DI2DI1DI0
        note["di"] = key

        if key == "04000401":                       # 通信地址（CCO 固件读表地址口径）
            note["func"] = "读通信地址(DI 04000401)"
            return HandleResult(self._reply(0x11, di_wire + self._addr), note)
        if key == "04000101":                       # 日期及周次 YYMMDDWW
            n = self.now()
            value = _bcd_encode(f"{n.year % 100:02d}{n.month:02d}{n.day:02d}{n.isoweekday():02d}", 0, 4)
            note["func"] = "读日期及周次"
            note["value"] = n.strftime("%Y-%m-%d") + f" 周{'一二三四五六日'[n.isoweekday() - 1]}"
            return HandleResult(self._reply(0x11, di_wire + value), note)
        if key == "04000102":                       # 时间 hhmmss
            n = self.now()
            value = _bcd_encode(f"{n.hour:02d}{n.minute:02d}{n.second:02d}", 0, 3)
            note["func"] = "读时间"
            note["value"] = n.strftime("%H:%M:%S")
            return HandleResult(self._reply(0x11, di_wire + value), note)

        di = _ALIAS_REGISTERS.get(key, key)
        value = self._register_value_bcd(di)
        if value is None:
            note["func"] = "读数据(无此DI)"
            return HandleResult(self._err_reply(0x11, _ERR_NO_DATA), note)
        note["func"] = "读数据"
        note["value"] = self._registers[di]["value"]
        return HandleResult(self._reply(0x11, di_wire + value), note)

    def _write_data(self, data: bytes, note: dict) -> HandleResult:
        """14H：DI(4) + PA(1) + 密码(3) + 操作者(4) + 数据（BCD）。

        最短写时间帧 = 4+8+3 = 15 字节。
        """
        if len(data) < 15:
            return HandleResult(self._err_reply(0x14, _ERR_OTHER), note)
        di_wire = bytes((b - 0x33) & 0xFF for b in data[:4])
        key = di_wire[::-1].hex().upper()
        payload = bytes((b - 0x33) & 0xFF for b in data[12:])
        note["di"] = key
        try:
            if key == "04000101" and len(payload) >= 4:     # 日期：YYMMDDWW（低字节在前）
                yy, mo, dd = (_bcd_byte(payload[3]), _bcd_byte(payload[2]),
                              _bcd_byte(payload[1]))
                n = self.now()
                self.set_clock(n.replace(year=2000 + yy, month=mo, day=dd))
                note["func"] = "写日期"
                return HandleResult(self._reply(0x14), note)
            if key == "04000102" and len(payload) >= 3:     # 时间：hhmmss（低字节在前）
                hh, mi, ss = (_bcd_byte(payload[2]), _bcd_byte(payload[1]),
                              _bcd_byte(payload[0]))
                n = self.now()
                self.set_clock(n.replace(hour=hh, minute=mi, second=ss))
                note["func"] = "写时间"
                return HandleResult(self._reply(0x14), note)
        except ValueError:
            return HandleResult(self._err_reply(0x14, _ERR_OTHER), note)
        note["func"] = "写数据(无此DI)"
        return HandleResult(self._err_reply(0x14, _ERR_NO_DATA), note)

    def _write_address(self, data: bytes, ctrl: int, note: dict) -> HandleResult:
        """写通信地址：数据域 6B 新地址。+33H 口径优先，非法 BCD 时按原始解释。"""
        if len(data) < 6:
            return HandleResult(self._err_reply(ctrl, _ERR_OTHER), note)
        new_addr = bytes((b - 0x33) & 0xFF for b in data[:6])
        if any((b >> 4) > 9 or (b & 0xF) > 9 for b in new_addr):
            new_addr = data[:6]                          # 主站未加 33H 的兼容
        note["func"] = "写通信地址"
        note["new_addr"] = new_addr.hex().upper()
        self._addr = new_addr
        return HandleResult(self._reply(ctrl), note)

    def _change_baudrate(self, ctrl: int, data: bytes, note: dict) -> HandleResult:
        """速率特征字单 bit 有效；应答回显解码后的特征字（_reply 统一 +33H，
        线上与主站请求字节一致）。应答后由通道层执行串口重开（action）。"""
        if len(data) < 1:
            return HandleResult(self._err_reply(ctrl, _ERR_OTHER), note)
        word = data[0]
        decoded = (word - 0x33) & 0xFF
        rate = RATE_BITS.get(decoded) if bin(decoded).count("1") == 1 else None
        if rate is None and bin(word).count("1") == 1:   # 主站未加 33H 的兼容
            rate = RATE_BITS.get(word)
            decoded = word
        if rate is None:
            note["rate_word"] = f"0x{word:02X}"
            return HandleResult(self._err_reply(ctrl, _ERR_OTHER), note)
        self.baudrate = rate
        note["baudrate"] = rate
        return HandleResult(self._reply(ctrl, bytes([decoded])),
                            note, action=("baud", rate))

    def _apply_broadcast_time(self, bcd6: bytes) -> None:
        """秒 分 时 日 月 年（BCD，低字节在前）→ 校准表内时钟。

        BCD 位越界（>9）按字节值兜底解释，避免恶意/畸形帧把时钟改飞。
        """
        ss, mi, hh, dd, mo, yy = (
            _bcd_byte(b) if ((b >> 4) & 0xF) <= 9 and (b & 0xF) <= 9 else b
            for b in bcd6[:6])
        target = datetime(2000 + yy, mo, dd, hh, mi, ss)
        self.set_clock(target)

    def _register_value_bcd(self, di: str) -> Optional[bytes]:
        item = self._registers.get(di)
        if item is None:
            return None
        try:
            return _bcd_encode(item["value"], item["scale"], item["length"])
        except ValueError:
            return None
