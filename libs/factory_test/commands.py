"""厂测命令 ID、载荷编解码与可读描述。

命令表来自 STA 固件 misc/src/factory_test.c 的 fact_cmd_read_handler / fact_cmd_write_handler。
所有多字节整数均为小端。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .codec import Frame, build_frame

# 控制字节
CTRL_READ = 0x10
CTRL_WRITE = 0x11
CTRL_WRITE_OK = 0x82
CTRL_WRITE_NAK = 0x83


def read_response_ctrl(request_ctrl: int = CTRL_READ) -> int:
    """读响应控制字节 = 请求控制 | 0x80。兼容 0x00/0x80 与 0x10/0x90。"""

    return (request_ctrl & 0xFF) | 0x80


# 基本参数
DI_VERSION = 0x0000
DI_ADDRESS = 0x0001
DI_SN = 0x0003
DI_RESET = 0x0004
DI_ENTER_FACTORY = 0x0005
DI_CHIP_ID = 0x0008
DI_MODULE_ID = 0x0009
DI_DEVICE_TYPE = 0x000B
DI_MODULE_MAC = 0x000C
DI_FIRM_CODE = 0x000D
DI_INTERNAL_VERSION = 0x000E

# RF
DI_RF_CHANNEL = 0x0100
DI_RF_TX_MODE = 0x0101
DI_RF_POWER = 0x0103
DI_RF_RX = 0x0104
DI_RF_CALL = 0x0109
DI_RF_CALL_RESULT = 0x010A
DI_RF_OPTION = 0x010B
DI_RF_TX = 0x010C
DI_RF_TX_SIN_START = 0x010D
DI_RF_TX_SIN_STOP = 0x010E

# HPLC
DI_HPLC_PHASE = 0x0200
DI_HPLC_TX = 0x0201
DI_HPLC_RX = 0x0202
DI_HPLC_CALL_RESULT = 0x0204
DI_HPLC_CALL = 0x0205
DI_HPLC_BAND = 0x0206
DI_HPLC_POWER = 0x020B

# 其他
DI_TEMPERATURE = 0x0401

COMMAND_NAMES: Dict[int, str] = {
    DI_VERSION: "版本号",
    DI_ADDRESS: "地址/MAC",
    DI_SN: "SN",
    DI_RESET: "重启模块",
    DI_ENTER_FACTORY: "进入测试模式",
    DI_CHIP_ID: "芯片ID",
    DI_MODULE_ID: "模块ID",
    DI_DEVICE_TYPE: "设备类型",
    DI_MODULE_MAC: "模块MAC",
    DI_FIRM_CODE: "厂商代码",
    DI_INTERNAL_VERSION: "内部版本号",
    DI_RF_CHANNEL: "RF测试信道",
    DI_RF_TX_MODE: "RF发送模式",
    DI_RF_POWER: "RF功率",
    DI_RF_RX: "RF接收模式",
    DI_RF_CALL: "RF点名",
    DI_RF_CALL_RESULT: "RF点名结果",
    DI_RF_OPTION: "RF选项字",
    DI_RF_TX: "RF发包",
    DI_RF_TX_SIN_START: "启动长发模式",
    DI_RF_TX_SIN_STOP: "停止长发模式",
    DI_HPLC_PHASE: "HPLC相位",
    DI_HPLC_TX: "HPLC发送",
    DI_HPLC_RX: "HPLC接收模式",
    DI_HPLC_CALL_RESULT: "HPLC点名结果",
    DI_HPLC_CALL: "HPLC点名",
    DI_HPLC_BAND: "HPLC频段",
    DI_HPLC_POWER: "HPLC功率",
    DI_TEMPERATURE: "温度",
}


def _le(data: bytes, start: int, size: int, signed: bool = False) -> int:
    chunk = data[start : start + size]
    if len(chunk) != size:
        raise ValueError(f"need {size} bytes at offset {start}, got {len(chunk)}")
    return int.from_bytes(chunk, "little", signed=signed)


def _bcd_to_int(value: int) -> int:
    return (value >> 4) * 10 + (value & 0x0F)


def decode_single_byte(data: bytes, signed: bool = False) -> int:
    if not data:
        raise ValueError("empty data")
    return int.from_bytes(data[:1], "little", signed=signed)


def decode_version(data: bytes) -> Dict[str, Any]:
    """0x0000：HVERSION(le16) + SVERSION(le24)，固件实际 res_len=7。"""

    sv = _le(data, 2, 3)
    return {
        "hversion": _le(data, 0, 2),
        "sversion": sv,
        "sversion_hex": f"0x{sv:06X}",
        "tail_hex": data[5:7].hex(" ").upper(),
    }


def decode_internal_version(data: bytes) -> Dict[str, Any]:
    """0x000E：HVERSION(le16) + FC_SVERSION(le24) + 日/月/年（BCD）。"""

    fc = _le(data, 2, 3)
    return {
        "hversion": _le(data, 0, 2),
        "fc_sversion": fc,
        "fc_sversion_hex": f"0x{fc:06X}",
        "day": _bcd_to_int(data[5]),
        "month": _bcd_to_int(data[6]),
        "year": _bcd_to_int(data[7]),
    }


def decode_address(data: bytes) -> Dict[str, Any]:
    """0x0001：MAC(6) + 保留(2)。"""

    mac = ":".join(f"{byte:02X}" for byte in data[:6])
    return {"mac": mac, "reserved_hex": data[6:8].hex(" ").upper()}


def decode_rf_call_result(data: bytes) -> Dict[str, Any]:
    """0x010A：RX RSSI、频偏、TX RSSI、RX SNR、TX SNR。"""

    return {
        "rx_rssi": _le(data, 0, 1),
        "freq_dev": _le(data, 1, 1),
        "tx_rssi": _le(data, 2, 1),
        "rx_snr": _le(data, 3, 1),
        "tx_snr": _le(data, 4, 1),
    }


def decode_hplc_call_result(data: bytes) -> Dict[str, Any]:
    """0x0204：RX RSSI、RX SNR、TX RSSI、TX SNR（RSSI 已经过 rgain→rssi 换算）。"""

    return {
        "rx_rssi": _le(data, 0, 1),
        "rx_snr": _le(data, 1, 1),
        "tx_rssi": _le(data, 2, 1),
        "tx_snr": _le(data, 3, 1),
    }


def decode_phase(data: bytes) -> Dict[str, Any]:
    value = decode_single_byte(data)
    names = {0: "A", 1: "B", 2: "C"}
    return {"phase": value, "phase_name": names.get(value, "UNKNOWN")}


def decode_power(data: bytes, signed: bool = True) -> Dict[str, Any]:
    value = decode_single_byte(data, signed=signed)
    return {"power": value}


def decode_channel(data: bytes) -> Dict[str, Any]:
    return {"channel": decode_single_byte(data)}


def decode_option(data: bytes) -> Dict[str, Any]:
    return {"option": decode_single_byte(data)}


def decode_band(data: bytes) -> Dict[str, Any]:
    return {"band": decode_single_byte(data)}


def describe_frame(frame: Frame) -> Dict[str, Any]:
    """把一帧转成可读的 dict；未知 DI 保留原始数据。"""

    result: Dict[str, Any] = {
        "ctrl": f"0x{frame.ctrl:02X}",
        "di": None if frame.di is None else f"0x{frame.di:04X}",
        "name": None,
        "direction": "response" if frame.is_response else "request",
        "data_hex": frame.data.hex(" ").upper(),
        "raw_hex": frame.raw.hex(" ").upper(),
        "crc_ok": True,
    }

    if frame.di is None:
        if frame.ctrl == CTRL_WRITE_OK:
            result["name"] = "写成功"
            result["ok"] = True
        elif frame.ctrl == CTRL_WRITE_NAK:
            result["name"] = "写否认"
            result["ok"] = False
        else:
            result["name"] = "无DI响应"
        return result

    result["name"] = COMMAND_NAMES.get(frame.di, "未知命令")
    result["ok"] = not frame.is_write_nak

    # 请求帧只展示原始 DATA；结果解码只对响应帧有意义。
    if not frame.is_response:
        return result

    try:
        if frame.di == DI_VERSION:
            result.update(decode_version(frame.data))
        elif frame.di == DI_INTERNAL_VERSION:
            result.update(decode_internal_version(frame.data))
        elif frame.di == DI_ADDRESS or frame.di == DI_MODULE_MAC:
            result.update(decode_address(frame.data))
        elif frame.di == DI_RF_CALL_RESULT:
            result.update(decode_rf_call_result(frame.data))
        elif frame.di == DI_HPLC_CALL_RESULT:
            result.update(decode_hplc_call_result(frame.data))
        elif frame.di == DI_RF_CHANNEL:
            result.update(decode_channel(frame.data))
        elif frame.di == DI_RF_OPTION:
            result.update(decode_option(frame.data))
        elif frame.di == DI_RF_POWER:
            result.update(decode_power(frame.data, signed=True))
        elif frame.di == DI_HPLC_BAND:
            result.update(decode_band(frame.data))
        elif frame.di == DI_HPLC_POWER:
            result.update(decode_power(frame.data, signed=True))
        elif frame.di == DI_HPLC_PHASE:
            result.update(decode_phase(frame.data))
    except (ValueError, IndexError) as exc:
        result["decode_error"] = str(exc)

    return result


# ---- 构帧快捷函数 ----


def build_read(di: int, ctrl: int = CTRL_READ, addr: int = 0xFE) -> bytes:
    return build_frame(ctrl, di, b"", addr=addr)


def build_write(di: int, data: bytes = b"", ctrl: int = CTRL_WRITE, addr: int = 0xFE) -> bytes:
    return build_frame(ctrl, di, data, addr=addr)


def build_enter(addr: int = 0xFE) -> bytes:
    return build_write(DI_ENTER_FACTORY, b"", addr=addr)


def build_reset(addr: int = 0xFE) -> bytes:
    return build_write(DI_RESET, b"", addr=addr)


def build_set_channel(channel: int, addr: int = 0xFE) -> bytes:
    return build_write(DI_RF_CHANNEL, bytes((channel & 0xFF,)), addr=addr)


def build_set_option(option: int, addr: int = 0xFE) -> bytes:
    return build_write(DI_RF_OPTION, bytes((option & 0xFF,)), addr=addr)


def build_set_power(power: int, addr: int = 0xFE) -> bytes:
    return build_write(DI_RF_POWER, bytes((power & 0xFF,)), addr=addr)


def build_set_band(band: int, addr: int = 0xFE) -> bytes:
    return build_write(DI_HPLC_BAND, bytes((band & 0xFF,)), addr=addr)


def build_hplc_power(power: int, addr: int = 0xFE) -> bytes:
    return build_write(DI_HPLC_POWER, bytes((power & 0xFF,)), addr=addr)


def build_rf_call(length: int = 100, addr: int = 0xFE) -> bytes:
    return build_write(DI_RF_CALL, bytes((length & 0xFF,)), addr=addr)


def build_hplc_call(length: int = 100, addr: int = 0xFE) -> bytes:
    return build_write(DI_HPLC_CALL, bytes((length & 0xFF,)), addr=addr)


def describe_di(di: Optional[int]) -> str:
    if di is None:
        return "无DI"
    return COMMAND_NAMES.get(di, f"未知命令(0x{di:04X})")
