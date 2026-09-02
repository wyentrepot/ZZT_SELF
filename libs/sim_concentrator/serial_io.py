"""可读写串口通道：模拟集中器与 CCO 模块通信的串口 IO。

区别于 listener 的只读监听（SerialCaptureService），本通道：
- 独占打开一个 COM；
- 读线程持续读字节，按 1376.2 帧结构切帧入队（复用 frame_codec.extract_frame）；
- 写队列 + 写锁，支持并发下发（send_frame / send_hex）。

用法：
    io = SerialIO(port="COM3", baudrate=115200)
    io.open()
    io.send_frame(raw_bytes)
    frame = io.recv_frame(timeout=5.0)   # 阻塞取一帧
    io.close()
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any, Optional

from shared.serial_mapping import SerialPortCatalog, SerialPortMapping
from shared.serial_resources import SerialResourceConflict, SerialResourceRegistry

from sim_concentrator.frame_codec import (
    scan_frame,
    frame_to_hex,
    hex_to_bytes,
)


def scan_any_frame(buf: bytes):
    """切一帧（单 68 统一：CCO 本地 = 单 68 标准帧）。"""
    return scan_frame(buf)


try:
    import serial
    from serial.tools import list_ports
    _SERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - 无 pyserial 环境
    serial = None
    list_ports = None
    _SERIAL_AVAILABLE = False


def available() -> bool:
    return _SERIAL_AVAILABLE


def _mapping_by_id(catalog: SerialPortCatalog, mapping_id: str) -> SerialPortMapping | None:
    wanted = str(mapping_id or "").strip()
    for mapping in catalog.mappings:
        if mapping.id == wanted:
            return mapping
    return None


def _natural_device_key(device: str) -> tuple:
    """COM10 排在 COM2 之后：按数字段数值排序。"""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", device)
    )


def _auto_select_port(catalog: SerialPortCatalog) -> str | None:
    """无显式端口时自动选择一个可用串口（默认值，可随时用 port 覆盖）。

    规则：排除已被启用映射认领的端口（侦听台/模块日志等专用口），
    蓝牙虚拟串口排到最后，余下按 COM 号自然序取第一个；无可用串口返回 None。
    """
    if not _SERIAL_AVAILABLE:
        return None
    claimed: set[str] = set()
    for mapping in catalog.mappings:
        if mapping.enabled:
            claimed |= mapping.aliases()
    candidates: list[tuple[int, tuple, str]] = []
    for info in list_ports.comports():
        device = str(getattr(info, "device", "") or "").strip()
        if not device or device.lower() in claimed:
            continue
        description = str(getattr(info, "description", "") or "")
        is_bluetooth = "bluetooth" in description.lower() or "蓝牙" in description
        candidates.append((1 if is_bluetooth else 0, _natural_device_key(device), device))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def list_serial_port_details(catalog: SerialPortCatalog | None = None) -> list[dict[str, Any]]:
    """枚举实际端口并合并可维护的映射端口（含离线配置项）。"""
    catalog = catalog or SerialPortCatalog.load()
    raw_ports = list_ports.comports() if _SERIAL_AVAILABLE else []
    return catalog.merge_system_ports(raw_ports)


def list_serial_ports(catalog: SerialPortCatalog | None = None) -> list[str]:
    """兼容旧调用：仅返回端口设备名，映射详情请使用 list_serial_port_details。"""
    return [str(item["device"]) for item in list_serial_port_details(catalog)]


def resolve_serial_config(
    port: str | None = None,
    *,
    mapping_id: str | None = None,
    baudrate: int | None = None,
    bytesize: int | None = None,
    parity: str | None = None,
    stopbits: int | None = None,
    catalog: SerialPortCatalog | None = None,
) -> dict[str, Any]:
    """将 mapping_id、COM 或 Linux 设备名收敛为当前平台可打开的串口参数。

    优先级：显式 mapping_id > 显式 port > 自动选择。模拟集中器不绑定固定
    映射：未提供端口时自动选择一个可用串口（排除侦听台/模块日志等已映射
    端口，蓝牙虚拟串口靠后），缺省串口参数 9600/E/8/1（1376.2 本地总线）。
    显式指定未映射端口时保持 115200/N/8/1 兼容默认值。
    """
    catalog = catalog or SerialPortCatalog.load()
    mapping: SerialPortMapping | None = None
    if mapping_id:
        mapping = _mapping_by_id(catalog, mapping_id)
        if mapping is None:
            raise ValueError(f"未找到串口映射：{mapping_id}")
        if not mapping.enabled:
            raise ValueError(f"串口映射已禁用：{mapping_id}")
    elif port:
        mapping = catalog.find(port)
    else:
        # 无显式端口：自动选择可用串口（默认值，不锁定到具体 COM 号）。
        auto_port = _auto_select_port(catalog)
        if not auto_port:
            raise ValueError(
                "未找到可用串口：请接入串口设备，或在任务/请求中显式指定 port")
        return {
            "port": auto_port,
            "mapping_id": "",
            "port_identity": {
                "mapping_id": "",
                "device": auto_port,
                "label": "",
                "usage": "",
                "module": "",
            },
            # 1376.2 本地总线缺省参数（沿用原 simcon 映射约定）
            "baudrate": 9600 if baudrate is None else int(baudrate),
            "bytesize": 8 if bytesize is None else int(bytesize),
            "parity": "E" if parity is None else str(parity).upper(),
            "stopbits": 1 if stopbits is None else int(stopbits),
        }

    if mapping is not None:
        resolved_port = mapping.device_for()
        identity = mapping.as_dict()
        identity["device"] = resolved_port
        return {
            "port": resolved_port,
            "mapping_id": mapping.id,
            "port_identity": identity,
            "baudrate": mapping.baudrate if baudrate is None else int(baudrate),
            "bytesize": mapping.bytesize if bytesize is None else int(bytesize),
            "parity": mapping.parity if parity is None else str(parity).upper(),
            "stopbits": mapping.stopbits if stopbits is None else int(stopbits),
        }

    resolved_port = str(port).strip()
    if not resolved_port:
        raise ValueError("必须提供串口或 mapping_id")
    return {
        "port": resolved_port,
        "mapping_id": "",
        "port_identity": {
            "mapping_id": "",
            "device": resolved_port,
            "label": "",
            "usage": "",
            "module": "",
        },
        "baudrate": 115200 if baudrate is None else int(baudrate),
        "bytesize": 8 if bytesize is None else int(bytesize),
        "parity": "N" if parity is None else str(parity).upper(),
        "stopbits": 1 if stopbits is None else int(stopbits),
    }


class SerialIO:
    """独占串口通道：读线程切帧入队 + 写锁发送。"""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.2,
                 bytesize: int = 8, parity: str = "N", stopbits: int = 1,
                 port_identity: dict[str, Any] | None = None,
                 resource_registry: SerialResourceRegistry | None = None,
                 journal=None,
                 auto_responder=None):
        """auto_responder：可选常驻应答器（Responder 实例）。

        协议约定：集中器任何时刻收到模块主动上报帧都应回确认（如 06H-F230 →
        00H-F1），与测试步骤/期待无关。该应答挂在读线程，收到完整帧即判定，
        不依赖 step/verify 执行窗口。
        """
        if not _SERIAL_AVAILABLE:
            raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.port_identity = dict(port_identity or {
            "mapping_id": "", "device": port, "label": "", "usage": "", "module": "",
        })
        # 会话帧日志（FrameJournal，可选）：tx 在 send_frame 记录，rx 在读线程记录
        self.journal = journal
        self._resource_registry = resource_registry or SerialResourceRegistry()
        self._resource_owner_id = "simcon:" + str(id(self))
        self._ser = None
        self._read_stop = threading.Event()
        self._read_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._rx_queue: "queue.Queue[bytes]" = queue.Queue()
        # 历史帧记录：读线程收到的所有完整帧（供 expect_history / 主动上报验证）
        self._rx_history: list = []
        self._open = False
        self._auto_responder = auto_responder

    # -- 生命周期 -------------------------------------------------------
    # 字符参数 → pyserial 常量映射
    _PARITY_MAP = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                   "O": serial.PARITY_ODD, "M": serial.PARITY_MARK,
                   "S": serial.PARITY_SPACE}
    _BYTESIZE_MAP = {5: serial.FIVEBITS, 6: serial.SIXBITS,
                     7: serial.SEVENBITS, 8: serial.EIGHTBITS}
    _STOPBITS_MAP = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}

    def _resource_aliases(self) -> tuple[str, ...]:
        return tuple(
            value for value in (
                self.port,
                self.port_identity.get("device"),
                self.port_identity.get("windows_com"),
                self.port_identity.get("linux_device"),
            ) if value
        )

    def _reserve_serial_resource(self) -> None:
        try:
            self._resource_registry.reserve(
                self._resource_owner_id,
                label="模拟集中器",
                resource_id=str(self.port_identity.get("mapping_id") or ""),
                aliases=self._resource_aliases(),
            )
        except SerialResourceConflict as exc:
            raise RuntimeError(
                f"串口 {self.port} 已被后端会话“{exc.owner_label}”占用（{exc.owner_id}）"
            ) from exc

    def _release_serial_resource(self) -> None:
        self._resource_registry.release(self._resource_owner_id)

    def open(self) -> bool:
        if self._open:
            return True
        reserved = False
        try:
            self._reserve_serial_resource()
            reserved = True
            self._ser = serial.Serial(
                port=self.port, baudrate=self.baudrate,
                bytesize=self._BYTESIZE_MAP.get(self.bytesize, serial.EIGHTBITS),
                parity=self._PARITY_MAP.get(self.parity.upper(), serial.PARITY_NONE),
                stopbits=self._STOPBITS_MAP.get(self.stopbits, serial.STOPBITS_ONE),
                timeout=self.timeout,
            )
            self._open = True
            self._read_stop.clear()
            self._read_thread = threading.Thread(
                target=self._run, name=f"simcon-rx-{self.port}", daemon=True)
            self._read_thread.start()
            return True
        except Exception:
            if reserved:
                self._release_serial_resource()
            self._ser = None
            self._open = False
            raise

    def close(self) -> None:
        self._read_stop.set()
        t, self._read_thread = self._read_thread, None
        if t is not None:
            t.join(timeout=2.0)
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._open = False
        self._release_serial_resource()

    def is_open(self) -> bool:
        return self._open

    # -- 读线程 ----------------------------------------------------------
    def _run(self) -> None:
        """读线程：累积字节，按 1376.2 帧切帧入队。"""
        buf = b""
        while not self._read_stop.is_set():
            try:
                data = self._ser.read(512)
            except Exception:
                break
            if not data:
                continue
            buf += data
            # 连续切出完整帧（跳过前导脏字节）
            while True:
                frame, consumed = scan_any_frame(buf)
                if frame is not None:
                    self._rx_history.append(frame)
                    if self.journal is not None:
                        try:
                            self.journal.append("rx", frame)
                        except Exception:
                            pass
                    # 历史只保留最近 1000 帧，防止长时间运行内存增长
                    if len(self._rx_history) > 1000:
                        del self._rx_history[: len(self._rx_history) - 1000]
                if frame is None:
                    break
                # 常驻自动应答：任何时刻收到完整帧，若命中规则（如 06H-F230 →
                # 00H-F1 确认）立即回包，与测试步骤/期待无关（协议默认行为）。
                if self._auto_responder is not None:
                    try:
                        reply = self._auto_responder.reply_for(frame)
                        if reply:
                            self.send_frame(reply)
                    except Exception:
                        # 自动应答失败不影响读线程主链路
                        pass
                self._rx_queue.put(frame)
                buf = buf[consumed:]
        # 退出前排空剩余（可选）
        if buf:
            # 保留以便将来 flush 语义；此处直接丢弃即可
            pass

    # -- 发送 ------------------------------------------------------------
    def send_frame(self, raw: bytes) -> int:
        """发送一帧字节，返回发送字节数。"""
        with self._write_lock:
            if not self._open or self._ser is None:
                raise RuntimeError("串口未打开")
            n = self._ser.write(raw)
        if self.journal is not None:
            try:
                self.journal.append("tx", raw)
            except Exception:
                pass
        return n

    def send_hex(self, hex_str: str) -> int:
        return self.send_frame(hex_to_bytes(hex_str))

    # -- 接收 ------------------------------------------------------------
    def recv_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """阻塞取一帧；超时返回 None。timeout=None 表示永不超时。"""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def pending_frames(self) -> int:
        return self._rx_queue.qsize()

    def rx_history(self) -> list:
        """返回收到的所有完整帧（bytes 列表，供主动上报验证/历史查询）。"""
        return list(self._rx_history)
