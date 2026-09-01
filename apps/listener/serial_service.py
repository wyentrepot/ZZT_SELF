"""串口实时采集服务。

从指定 COM 口（默认 COM19, 115200, N, 8, 1）读取 HPLC 侦听裸 7E 帧流，
按 7E 定界切出完整帧，为每帧追加实时时间戳（HH:MM:SS.mmm），解析后
追加写入现有 sqlite 索引（复用 LogFileService.append_frame），供前端
轮询实时查看。

裸流切帧规则（依据真实抓包 SAVE*.DAT 分析）：
- 帧以 0x7E 开头和结尾定界；
- 相邻帧之间出现 "7E 7E"：前一 7E 是上帧尾、后一 7E 是下帧头；
- 帧内 0x7E 通过 HDLC 转义 7D 5E 表示，切帧时按 7D 5E 识别为帧内字节。
"""
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from shared.serial_mapping import SerialPortCatalog
from shared.serial_resources import SerialResourceConflict, SerialResourceRegistry

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - 依赖缺失时给出明确错误
    serial = None
    list_ports = None

SERIAL_BAUD = 115200
SERIAL_BYTESIZE = 8
SERIAL_PARITY = "N"
SERIAL_STOPBITS = 1

# 转义处理
_ESC = 0x7D
_FLAG = 0x7E


def format_timestamp(now: Optional[float] = None) -> str:
    """生成实时时间戳 HH:MM:SS.mmm（与日志文件行首格式一致）。"""
    now = time.time() if now is None else now
    local = time.localtime(now)
    millis = int((now - int(now)) * 1000)
    return (
        f"{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d}.{millis:03d}"
    )


def split_7e_frames(data: bytes):
    """从裸字节流中切出以 0x7E 定界的完整帧（含首尾 7E）。

    内部 7E（HDLC 转义 7D 5E）不被当作定界。逐字节扫描，识别帧尾 7E 后
    判定是否构成一帧：帧头 7E 与帧尾 7E 之间的净载荷长度 ≥ 2（HPLC 帧
    最小长度远大于此，用于过滤残留/噪声）。

    返回 (frames, tail)：frames 为完整帧 bytes 列表；tail 为未闭合的
    残留在缓冲区开头（供下次拼接）。
    """
    frames = []
    start = -1  # 当前候选帧头 7E 的位置
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == _ESC and i + 1 < n and data[i + 1] in (_FLAG, _ESC):
            # 合法 HDLC 转义序列 7D 5E / 7D 7D：被转义字节是帧内数据，跳过
            i += 2
            continue
        if b == _FLAG:
            if start == -1:
                # 帧头
                start = i
            else:
                # 帧尾：data[start..i] 是一帧（前后都是 7E）
                payload_len = i - start - 1
                if payload_len >= 2:
                    frames.append(data[start : i + 1])
                    start = -1
                else:
                    # 过短，视为残留：以当前 7E 为新候选帧头
                    start = i
        i += 1

    # 未闭合的尾部（可能包含一个帧头 7E 或完整帧残留）
    if start != -1:
        tail = data[start:]
    else:
        # 未发现未闭合帧头，丢弃已处理内容（保留最后一个 7E 以防它是上帧尾）
        tail = b"\x7e" if data and data[-1] == _FLAG and not frames else b""
    return frames, tail


class SerialCaptureService:
    def __init__(self, log_service, port="COM19", baudrate=SERIAL_BAUD,
                 bytesize=SERIAL_BYTESIZE, parity=SERIAL_PARITY,
                 stopbits=SERIAL_STOPBITS, log_dir=None, port_catalog=None,
                 resource_registry=None):
        if serial is None:
            raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial")
        self.log_service = log_service
        # 帧入库钩子（需求 0009 live 追踪）：批量入库成功后以
        # on_frames_appended(last_frame_id) 回调；由 app 装配时注入。
        self.on_frames_appended = None
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        # 统一映射仅增强展示与身份识别；缺失/错误时仍可枚举并打开真实设备。
        self._port_catalog = port_catalog or SerialPortCatalog.load()
        self._resource_registry = resource_registry or SerialResourceRegistry()
        self._resource_owner_id = "listener:" + str(id(self))
        self._port_identity = self._port_identity_for(self.port)[1]
        # 串口数据落盘目录：默认项目根 data/logs/侦听台/（侦听台日志独立子目录）
        default_log_dir = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "侦听台"
        self.log_dir = Path(log_dir) if log_dir else default_log_dir
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._sequence = 0
        self._minute_state = {}
        self._log_file = None  # 当前会话打开的日志文件对象
        self._log_path = None  # 当前 LOG 文件路径
        self._log_day = None   # 当前 LOG 文件对应的日期（YYYYMMDD），用于跨天轮转
        self._status = self._empty_status()

    def _empty_status(self) -> dict:
        return {
            "state": "idle",  # idle | running | stopped | error
            "port": self.port,
            "port_identity": dict(self._port_identity),
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "frame_count": 0,
            "byte_count": 0,
            "error_count": 0,
            "message": "串口未启动",
            "log_dir": str(self.log_dir),
            "log_file": None,
            "started_at": None,
        }

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _replace_status(self, **values) -> dict:
        with self._lock:
            self._status.update(values)
            return dict(self._status)

    def set_resource_registry(self, resource_registry: SerialResourceRegistry) -> None:
        """Attach the workbench-wide registry while this capture is idle."""
        if resource_registry is None:
            raise ValueError("resource_registry 不能为空")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("串口采集运行中，不能切换串口资源注册表")
            self._resource_registry = resource_registry

    @staticmethod
    def _resource_aliases(open_port: str, identity: dict) -> tuple[str, ...]:
        return tuple(
            value for value in (
                open_port,
                identity.get("device"),
                identity.get("windows_com"),
                identity.get("linux_device"),
            ) if value
        )

    def _reserve_serial_resource(self, open_port: str, identity: dict) -> None:
        try:
            self._resource_registry.reserve(
                self._resource_owner_id,
                label="侦听台",
                resource_id=str(identity.get("mapping_id") or ""),
                aliases=self._resource_aliases(open_port, identity),
            )
        except SerialResourceConflict as exc:
            raise RuntimeError(
                f"串口 {open_port} 已被后端会话“{exc.owner_label}”占用（{exc.owner_id}）"
            ) from exc

    def _release_serial_resource(self) -> None:
        self._resource_registry.release(self._resource_owner_id)

    def _mapping_is_online(self, mapping_id: str) -> bool:
        return any(
            detail.get("mapping_id") == mapping_id and detail.get("online")
            for detail in self.list_available_ports()
        )

    def _port_identity_for(self, port: str) -> tuple[str, dict]:
        """将 Windows COM 与 WSL 设备别名收敛到同一可展示身份。"""
        catalog = getattr(self, "_port_catalog", None)
        mapping = catalog.find(port) if catalog is not None else None
        if mapping is None:
            return str(port), {
                "mapping_id": "",
                "label": "",
                "device": str(port),
                "linux_device": "",
                "windows_com": "",
                "usage": "",
                "module": "",
            }
        if not mapping.enabled:
            raise RuntimeError(f"串口映射 {mapping.id} 已禁用")
        device = mapping.device_for()
        return device, {
            "mapping_id": mapping.id,
            "label": mapping.label,
            "device": device,
            "linux_device": mapping.linux_device,
            "windows_com": mapping.windows_com,
            "usage": mapping.usage,
            "module": mapping.module,
        }

    def start(self, port=None, baudrate=None, bytesize=None, parity=None,
              stopbits=None) -> dict:
        requested_port = self.port if port is None else str(port)
        open_port, identity = self._port_identity_for(requested_port)
        mapping_id = str(identity.get("mapping_id") or "")
        if mapping_id and not self._mapping_is_online(mapping_id):
            raise RuntimeError(
                f"映射串口 {identity.get('label') or mapping_id} 当前离线，请刷新端口列表"
            )
        next_baudrate = self.baudrate if baudrate is None else baudrate
        next_bytesize = self.bytesize if bytesize is None else bytesize
        next_parity = self.parity if parity is None else parity
        next_stopbits = self.stopbits if stopbits is None else stopbits
        reserved = False
        try:
            with self._lock:
                if self._thread and self._thread.is_alive():
                    raise RuntimeError("串口采集已在运行")
                self._reserve_serial_resource(open_port, identity)
                reserved = True
                self.port = open_port
                self._port_identity = identity
                self.baudrate = next_baudrate
                self.bytesize = next_bytesize
                self.parity = next_parity
                self.stopbits = next_stopbits
                self._buffer = bytearray()
                self._sequence = 0
                self._minute_state = {}
                self._stop_event.clear()
                self._status = self._empty_status()
                self._status["state"] = "starting"
                self._status["message"] = f"正在打开 {self.port} ..."
                self._log_file, self._log_path = self._open_log_file()
                if self._log_path is not None:
                    self._status["log_file"] = str(self._log_path)
                try:
                    self.log_service.reset_index()
                except Exception:
                    pass
            self._thread = threading.Thread(
                target=self._run, name="hplc-serial-capture", daemon=True
            )
            self._thread.start()
            return self.status()
        except Exception:
            if reserved:
                self._release_serial_resource()
            raise

    def _open_log_file(self):
        """在 LOG 目录新建会话日志文件，命名 {COM}_{时间}_自动保存.txt。

        返回 (file_object, path)；失败返回 (None, None)，采集仍继续但落盘跳过。
        """
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            port_safe = str(self.port).replace("\\", "").replace("/", "")
            mapping_id = str(self._port_identity.get("mapping_id") or "")
            identity_tag = mapping_id or port_safe
            path = self.log_dir / f"{identity_tag}_{port_safe}_{stamp}_自动保存.txt"
            handle = open(path, "a", encoding="utf-8", buffering=1)
            self._log_day = time.strftime("%Y%m%d", time.localtime())
            return handle, path
        except OSError:
            return None, None

    def stop(self) -> dict:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._close_log_file()
        self._release_serial_resource()
        return self.status()

    def _close_log_file(self):
        try:
            if self._log_file is not None:
                self._log_file.close()
        except OSError:
            pass
        finally:
            self._log_file = None

    def _run(self) -> None:
        try:
            self._capture_loop()
        except Exception as exc:
            self._close_log_file()
            self._release_serial_resource()
            self._replace_status(state="error", message=f"串口采集错误：{exc}")

    def _capture_loop(self) -> None:
        self._replace_status(
            state="running",
            message=f"正在监听 {self.port} ({self.baudrate}, {self.parity}, {self.bytesize}, {self.stopbits})",
            started_at=time.time(),
        )
        with serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=0.1,
        ) as ser:
            while not self._stop_event.is_set():
                chunk = ser.read(4096)
                if not chunk:
                    continue
                self._on_chunk(chunk)
            # 停止时把缓冲区残留一并入库（可选）
            self._replace_status(
                state="stopped", message="串口采集已停止"
            )

    def _on_chunk(self, chunk: bytes) -> None:
        """处理串口到达的一块数据：并入缓冲、切出完整帧批量入库。

        缓冲跨块累积（bytearray），保证半帧在下一块到达后能合并成完整帧。
        完整帧按块批量入库（单事务），避免逐帧提交的写放大。
        """
        self._buffer.extend(chunk)
        frames, tail = split_7e_frames(bytes(self._buffer))
        self._buffer = bytearray(tail)  # 保留未闭合尾部，保持 bytearray 类型
        if frames:
            self._ingest_batch(frames)

    def _ingest_batch(self, frames) -> None:
        """批量切出帧：加时间戳、落盘 LOG 文件、批量追加入库、更新计数。"""
        records = []
        byte_total = 0
        for frame in frames:
            self._sequence += 1
            seq = f"{self._sequence:06d}"
            ts = format_timestamp()
            hex_frame = " ".join(f"{b:02X}" for b in frame)
            # 1) 间接入库：先落盘 LOG 文件（[序号][时间]7E...7E，与日志索引格式一致）
            self._write_log_line(f"[{seq}][{ts}]{hex_frame}")
            # 2) 实时入库：批量写入 sqlite 供前端实时查看
            records.append((seq, ts, hex_frame))
            byte_total += len(frame)
        try:
            results = self.log_service.append_frames(records, self._minute_state)
        except Exception:
            self._replace_status(
                error_count=self._status.get("error_count", 0) + len(records)
            )
            return
        self._replace_status(
            frame_count=self._sequence,
            byte_count=self._status.get("byte_count", 0) + byte_total,
        )
        if self.on_frames_appended is not None and results:
            try:
                self.on_frames_appended(results[-1][0])
            except Exception:
                pass  # 追踪钩子异常不影响采集主链路

    def _ingest(self, frame: bytes) -> None:
        """单帧入库（兼容旧接口/测试），委托批量实现。"""
        self._ingest_batch([frame])

    def _write_log_line(self, line: str) -> None:
        """追加一行到当前会话 LOG 文件；失败仅计数不中断采集。

        跨天（日期变化）时自动轮转到新文件，避免单个 LOG 文件随长时间
        采集无限增长。轮转仅换文件句柄，不改变会话语义。
        """
        if self._log_file is None:
            return
        try:
            today = time.strftime("%Y%m%d", time.localtime())
            if self._log_day is not None and today != self._log_day:
                self._close_log_file()
                self._log_file, self._log_path = self._open_log_file()
                if self._log_file is None:
                    return
            self._log_file.write(line + "\n")
        except OSError:
            self._replace_status(error_count=self._status.get("error_count", 0) + 1)

    def list_available_ports(self) -> list:
        """枚举可见串口，并合并统一映射的 COM、角色和默认串口参数。"""
        if list_ports is None:
            return []
        try:
            system_ports = [
                {"device": p.device, "description": p.description}
                for p in list_ports.comports()
            ]
        except Exception:
            return []
        catalog = getattr(self, "_port_catalog", None)
        if catalog is not None:
            merged = catalog.merge_system_ports(system_ports, platform_name=os.name)
            try:
                from shared.serial_tags import SerialTagStore

                return SerialTagStore().merge_port_details(merged)
            except Exception:  # noqa: BLE001 - 标签层故障不影响串口枚举
                return merged

        # 兼容旧的直接 __new__ 测试和独立调用：未装配 catalog 时保留原展示行为。
        if os.name != "nt":
            com_map = self._load_com_map()
            for port in system_ports:
                port["com"] = com_map.get(port["device"], "")
        return system_ports

    def mapping_error(self) -> str:
        catalog = getattr(self, "_port_catalog", None)
        return getattr(catalog, "mapping_error", "") if catalog is not None else ""

    @staticmethod
    def _load_com_map() -> dict:
        """读取 设备名 -> Windows COM 号 映射表（serial_com_map.json）。

        文件缺失/损坏时返回空表（仅不显示 COM 标注，不影响功能）。
        """
        path = Path(__file__).resolve().parent / "serial_com_map.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return dict(data.get("map", {}))
        except Exception:
            return {}


def create_serial_service(log_service, port="COM19", log_dir=None) -> SerialCaptureService:
    return SerialCaptureService(log_service, port=port, log_dir=log_dir)
