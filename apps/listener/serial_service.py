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
                 stopbits=SERIAL_STOPBITS, log_dir=None):
        if serial is None:
            raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial")
        self.log_service = log_service
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
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
            "baudrate": self.baudrate,
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

    def start(self, port=None, baudrate=None, bytesize=None, parity=None,
              stopbits=None) -> dict:
        if port is not None:
            self.port = port
        if baudrate is not None:
            self.baudrate = baudrate
        if bytesize is not None:
            self.bytesize = bytesize
        if parity is not None:
            self.parity = parity
        if stopbits is not None:
            self.stopbits = stopbits
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("串口采集已在运行")
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
            # 串口模式：清空现有索引，保证从干净库开始（与日志模式互斥，各自保留）
            try:
                self.log_service.reset_index()
            except Exception:
                pass
        self._thread = threading.Thread(
            target=self._run, name="hplc-serial-capture", daemon=True
        )
        self._thread.start()
        return self.status()

    def _open_log_file(self):
        """在 LOG 目录新建会话日志文件，命名 {COM}_{时间}_自动保存.txt。

        返回 (file_object, path)；失败返回 (None, None)，采集仍继续但落盘跳过。
        """
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            port_safe = str(self.port).replace("\\", "").replace("/", "")
            path = self.log_dir / f"{port_safe}_{stamp}_自动保存.txt"
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
            self.log_service.append_frames(records, self._minute_state)
        except Exception:
            self._replace_status(
                error_count=self._status.get("error_count", 0) + len(records)
            )
            return
        self._replace_status(
            frame_count=self._sequence,
            byte_count=self._status.get("byte_count", 0) + byte_total,
        )

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
        if list_ports is None:
            return []
        try:
            ports = [
                {"device": p.device, "description": p.description}
                for p in list_ports.comports()
            ]
        except Exception:
            return []
        # 仅非 Windows（WSL 等）附加 COM 标注：设备名是 /dev/ttyUSB* 等，
        # 前端据此显示 'COM4 (/dev/ttyUSB0)'。Windows 侧设备名本就是 COMx，
        # 保持原样不加标注，避免影响 Windows 端使用。
        if os.name != "nt":
            com_map = self._load_com_map()
            for port in ports:
                port["com"] = com_map.get(port["device"], "")
        return ports

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
