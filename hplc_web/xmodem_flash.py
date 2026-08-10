"""XMODEM 模块烧录传输核心（从部门 skill xmodem-module-flash 的
flash_xmodem_module.ps1 移植，纯 Python，无 pyserial 之外的依赖）。

设计要点（需求 0001 serial-flash-session，REQS.md 变更 2）：
- 与串口 handle 解耦：flash(ser, ...) 只认一个 pyserial 对象
  （有 read/write/timeout 属性的对象即可），谁持有 handle 由调用方决定：
    · 项目运行中：ModuleSerialService 用其常驻 handle 调用
    · 项目不在：flash_module.py 自己 open COM 后调用
- 烧录 = 在同一 handle 上传输文件 + 动态切波特率，全程不关串口；
  RX 监控线程可同时读回显（pyserial read/write 线程安全）。
- 波特率方案 baud_plan：bootloader 导航 → 传输 → 恢复，如 [9600, 115200, 9600]。
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

# XMODEM 协议常量
SOH = 0x01
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18
CRCCHR = 0x43  # 'C'：请求 CRC 模式
PAD = 0x1A

# 默认超时（毫秒），与 ps1 一致
DEFAULT_PROMPT_TIMEOUT_MS = 3000
DEFAULT_BOOT_TIMEOUT_MS = 12000
DEFAULT_XMODEM_TIMEOUT_MS = 30000
DEFAULT_RESPONSE_TIMEOUT_MS = 10000


def crc16_xmodem(data: bytes, offset: int = 0, count: Optional[int] = None) -> int:
    """XMODEM CRC-16（多项式 0x1021，初值 0，无反射），与 ps1 Get-XmodemCrc16 一致。

    自检向量：crc16_xmodem(b"123456789") == 0x31C3
    """
    if count is None:
        count = len(data) - offset
    crc = 0
    for i in range(offset, offset + count):
        crc ^= (data[i] & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def build_xmodem_packet(block: bytes, packet_no: int, use_crc: bool = True) -> bytes:
    """构造一个 128 字节块的 XMODEM 包（SOH + 序号 + ~序号 + 数据 + CRC/校验和）。"""
    if len(block) != 128:
        raise ValueError("XMODEM block must be 128 bytes.")
    seq = packet_no & 0xFF
    packet = bytearray([SOH, seq, (0xFF - seq) & 0xFF])
    packet.extend(block)
    if use_crc:
        crc = crc16_xmodem(block)
        packet.append((crc >> 8) & 0xFF)
        packet.append(crc & 0xFF)
    else:
        checksum = sum(block) & 0xFF
        packet.append(checksum)
    return bytes(packet)


class SerialReadTimeout(Exception):
    """读取串口字节超时。"""


def _read_byte(ser, timeout_ms: int) -> int:
    """带超时读取单字节；-1 表示超时（与 ps1 Read-ByteWithTimeout 语义一致）。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if ser.in_waiting > 0:
            b = ser.read(1)
            if b:
                return b[0]
        time.sleep(0.01)
    return -1


def _read_text_until_quiet(ser, timeout_ms: int, quiet_ms: int,
                           log: Optional[Callable[[str], None]] = None) -> str:
    """读取文本直到静默期（quiescence）或超时；近似 ps1 Read-TextUntilQuiet。

    串口字节视为 ASCII 文本拼接；连续 quiet_ms 毫秒无新数据且已有内容即返回。
    """
    buf = bytearray()
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        if ser.in_waiting > 0:
            chunk = ser.read(ser.in_waiting)
            if chunk:
                buf.extend(chunk)
                last_data = time.monotonic()
        elif buf and (time.monotonic() - last_data) * 1000.0 >= quiet_ms:
            break
        else:
            time.sleep(0.025)
    text = buf.decode("ascii", errors="replace")
    if text and log:
        log(f"RX text: {text!r}")
    return text


def _send_line(ser, line: str, log: Optional[Callable[[str], None]] = None) -> None:
    """发送一行 ASCII 文本（追加 CRLF），与 ps1 Send-Line 一致。"""
    if log:
        log(f"TX line: {line}")
    ser.write((line + "\r\n").encode("ascii"))


def _test_bootloader_text(text: str) -> bool:
    return "[image /]#" in text


def _invoke_bootloader_navigation(ser, text: str,
                                  log: Optional[Callable[[str], None]] = None) -> str:
    """处理 bootloader 导航提示（Press 'd' key → 'd'；[root /]# → 'image'）。"""
    extra = ""
    if "Press 'd' key" in text:
        if log:
            log("TX boot key: d")
        ser.write(b"d")
        extra += _read_text_until_quiet(ser, 2000, 200, log)
    if "[root /]#" in (text + extra):
        _send_line(ser, "image", log)
        extra += _read_text_until_quiet(ser, 2000, 200, log)
    return text + extra


def wait_bootloader_prompt(ser, enter_bootloader_command: str = "reboot",
                           prompt_timeout_ms: int = DEFAULT_PROMPT_TIMEOUT_MS,
                           boot_timeout_ms: int = DEFAULT_BOOT_TIMEOUT_MS,
                           log: Optional[Callable[[str], None]] = None) -> bool:
    """等待并进入 bootloader [image /]# 提示符；成功返回 True。

    流程（与 ps1 Wait-BootloaderPrompt 一致）：
    1) 先发空行探活；2) 若出现导航提示则处理；3) 否则发 EnterBootloaderCommand
    并在 BootTimeoutMs 内发 Ctrl-C 中断 + 持续读取，直到出现 [image /]#。
    """
    _send_line(ser, "", log)
    text = _read_text_until_quiet(ser, prompt_timeout_ms, 200, log)
    text = _invoke_bootloader_navigation(ser, text, log)
    if _test_bootloader_text(text):
        return True

    if enter_bootloader_command:
        _send_line(ser, enter_bootloader_command, log)

    deadline = time.monotonic() + boot_timeout_ms / 1000.0
    all_text = ""
    while time.monotonic() < deadline:
        if ser.in_waiting == 0:
            ser.write(bytes([0x03]))  # Ctrl-C 中断
            time.sleep(0.15)
        chunk = _read_text_until_quiet(ser, 500, 120, log)
        if chunk:
            all_text += _invoke_bootloader_navigation(ser, chunk, log)
            if _test_bootloader_text(all_text):
                return True
    return False


def wait_xmodem_request(ser, timeout_ms: int = DEFAULT_XMODEM_TIMEOUT_MS,
                        log: Optional[Callable[[str], None]] = None) -> bool:
    """等待接收方发出 XMODEM 请求；返回 True=CRC 模式，False=校验和模式。"""
    if log:
        log("Waiting for XMODEM receiver request.")
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        b = _read_byte(ser, 500)
        if b == CRCCHR:
            if log:
                log("XMODEM mode: crc")
            return True
        if b == NAK:
            if log:
                log("XMODEM mode: checksum")
            return False
        if b == CAN:
            raise RuntimeError("Receiver cancelled XMODEM before transfer.")
    raise RuntimeError("Timed out waiting for XMODEM request.")


def send_xmodem(ser, image: bytes, use_crc: bool, log: Optional[Callable[[str], None]] = None,
                progress: Optional[Callable[[int, int], None]] = None,
                response_timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
                max_retries: int = 10) -> None:
    """以 XMODEM 发送整个镜像（与 ps1 Send-Xmodem 一致）。

    每 128 字节一块；发送后等 ACK（成功）或 NAK（重发）；发完发 EOT 等 ACK。
    progress(packet_no, total_packets) 供前端进度条。
    """
    packet_no = 1
    offset = 0
    total_packets = (len(image) + 127) // 128
    last_progress = -1

    while offset < len(image):
        block = bytearray([PAD] * 128)
        count = min(128, len(image) - offset)
        block[:count] = image[offset : offset + count]
        packet = build_xmodem_packet(bytes(block), packet_no, use_crc)

        sent = False
        for retry in range(max_retries):
            ser.write(packet)
            resp = _read_byte(ser, response_timeout_ms)
            if resp == ACK:
                sent = True
                offset += count
                pct = int(offset * 100.0 / len(image))
                if pct >= last_progress + 5 or offset == len(image):
                    current = min(total_packets, (offset + 127) // 128)
                    if log:
                        log(f"XMODEM progress packet={current}/{total_packets} "
                            f"bytes={offset}/{len(image)} pct={pct}")
                    last_progress = pct
                    if progress:
                        progress(current, total_packets)
                packet_no = (packet_no + 1) & 0xFF
            elif resp == NAK:
                if log:
                    log(f"XMODEM retry packet={packet_no} attempt={retry + 1}")
            elif resp == CAN:
                raise RuntimeError("Receiver cancelled XMODEM transfer.")
            elif resp < 0:
                if log:
                    log(f"XMODEM timeout packet={packet_no} attempt={retry + 1}")
            else:
                if log:
                    log(f"Unexpected XMODEM response 0x{resp:02X} packet={packet_no}")
            if sent:
                break
        if not sent:
            raise RuntimeError(f"XMODEM packet {packet_no} failed after retries.")

    for retry in range(max_retries):
        ser.write(bytes([EOT]))
        resp = _read_byte(ser, response_timeout_ms)
        if resp == ACK:
            if log:
                log("XMODEM EOT ACK")
            return
        if resp == CAN:
            raise RuntimeError("Receiver cancelled XMODEM at EOT.")
        if log:
            log(f"Retry EOT, response={resp}")
    raise RuntimeError("XMODEM EOT was not acknowledged.")


def flash(ser, bin_path: str, slot: int = 0,
          baud_plan: Optional[List[int]] = None,
          no_reboot_after: bool = False,
          enter_bootloader_command: str = "reboot",
          log: Optional[Callable[[str], None]] = None,
          progress: Optional[Callable[[int, int], None]] = None,
          on_baud_change: Optional[Callable[[int], None]] = None) -> dict:
    """模块烧录主流程（与 ps1 主逻辑一致，但 handle 由调用方提供）。

    波特率：与烧录技能（flash_xmodem_module.ps1）一致，使用**单一波特率**
    （默认 115200），不在导航/传输之间切换。调用方可传 baud_plan=[rate]
    指定；传多元素列表时也只取首元素，保持与技能一致的单一速率语义。

    返回 {"status": "success", "log_events": n}。
    """
    import pathlib

    if baud_plan is None or not baud_plan:
        baud_plan = [115200]

    fw = pathlib.Path(bin_path)
    if not fw.is_file():
        raise FileNotFoundError(f"Firmware image not found: {bin_path}")
    image = fw.read_bytes()

    def _log(msg: str) -> None:
        if log:
            log(msg)

    def _set_baud(rate: int) -> None:
        _log(f"波特率变更 → {rate}")
        ser.baudrate = rate
        if on_baud_change:
            on_baud_change(rate)
        time.sleep(0.05)  # 给接收方一点时间重锁波特率

    _log(f"Firmware: {bin_path} size={len(image)}")
    _log(f"Baud: {baud_plan[0]}（单一速率，与烧录技能一致）")

    # 与烧录技能一致：单一波特率，只设一次，不切换
    _set_baud(baud_plan[0])

    if not wait_bootloader_prompt(ser, enter_bootloader_command, log=log):
        raise RuntimeError("Bootloader prompt was not detected.")

    _send_line(ser, f"download {slot}", log)
    download_text = _read_text_until_quiet(ser, 5000, 300, log)
    if not any(k in download_text for k in ("overwrite", "Press <Y>", "continue", "download")):
        _log("Download prompt was not explicit; continuing cautiously.")

    _send_line(ser, "Y", log)
    _read_text_until_quiet(ser, 1500, 300, log)

    use_crc = wait_xmodem_request(ser, log=log)
    send_xmodem(ser, image, use_crc, log=log, progress=progress)

    result_text = _read_text_until_quiet(ser, 12000, 500, log)
    if not any(k in result_text for k in ("Image download OK", "download", "success")):
        raise RuntimeError(
            "XMODEM ended but bootloader success text was not observed."
        )
    _log("Image download confirmed.")
    if not no_reboot_after:
        _send_line(ser, "reboot", log)
        _read_text_until_quiet(ser, 3000, 300, log)
    _log("BURN SUCCESS")
    return {"status": "success", "log_events": 1}


def selftest() -> dict:
    """无硬件自检：CRC 向量 + 包构造（对照 ps1 Invoke-SelfTest 0x31C3）。"""
    data = b"123456789"
    crc = crc16_xmodem(data)
    if crc != 0x31C3:
        raise RuntimeError(f"CRC self-test failed: 0x{crc:04X}")

    block = bytearray([PAD] * 128)
    block[: len(data)] = data
    packet = build_xmodem_packet(bytes(block), 1, True)
    if len(packet) != 133:
        raise RuntimeError("Packet length self-test failed.")
    if packet[0] != SOH or packet[1] != 1 or packet[2] != 254:
        raise RuntimeError("Packet header self-test failed.")
    return {"status": "pass", "crc": "0x31C3", "packet_len": len(packet)}
