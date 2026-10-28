"""模拟厂测上位机的串口客户端。

默认端口是工作台串口桥暴露的 WSL PTY：``/run/hplc/serial/factory``
（对应 Windows COM7，9600 8N1）。也可以直接传 ``--port`` 指向其他 PTY/串口设备。
"""

from __future__ import annotations

import time
from typing import Iterable, List, Optional, Sequence

from .codec import Frame, FrameStream, build_frame
from .commands import (
    CTRL_READ,
    CTRL_WRITE,
    CTRL_WRITE_NAK,
    CTRL_WRITE_OK,
    DI_ADDRESS,
    DI_ENTER_FACTORY,
    DI_HPLC_BAND,
    DI_HPLC_CALL,
    DI_HPLC_CALL_RESULT,
    DI_HPLC_POWER,
    DI_RESET,
    DI_RF_CALL,
    DI_RF_CALL_RESULT,
    DI_RF_CHANNEL,
    DI_RF_OPTION,
    DI_RF_POWER,
    DI_VERSION,
    decode_address,
    decode_channel,
    decode_hplc_call_result,
    decode_option,
    decode_band,
    decode_power,
    decode_rf_call_result,
    decode_version,
    read_response_ctrl,
)


class FactoryTestError(RuntimeError):
    """厂测上位机业务错误。"""


class FactoryTestSerialError(FactoryTestError):
    """串口打开/读写失败。"""


class FactoryTestTimeout(FactoryTestError):
    """等待响应超时。"""


class FactoryTestNak(FactoryTestError):
    """设备返回 0x83 否认。"""

    def __init__(self, message: str, frame: Optional[Frame] = None):
        super().__init__(message)
        self.frame = frame


class FactoryTestClient:
    """一个最小可用的厂测 A5 上位机客户端。

    典型用法::

        with FactoryTestClient("/run/hplc/serial/factory") as host:
            host.enter()
            print(host.read_version())
            print(host.get_channel())
            ok, _ = host.rf_call(100)
            print(host.read_rf_call_result())
    """

    def __init__(
        self,
        port: str = "/run/hplc/serial/factory",
        baud: int = 9600,
        timeout: float = 1.0,
        read_timeout: float = 0.05,
        retries: int = 3,
        addr: int = 0xFE,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.read_timeout = read_timeout
        self.retries = retries
        self.addr = addr

        self._ser = None
        self._stream = FrameStream()
        self._pending: List[Frame] = []

    # ---- 生命周期 ----

    def open(self) -> None:
        if self._ser is not None:
            return
        try:
            import serial  # type: ignore
        except Exception as exc:  # pragma: no cover - 环境依赖
            raise FactoryTestSerialError(
                "pyserial is not installed; run: pip install pyserial"
            ) from exc

        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.read_timeout,
                write_timeout=self.timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except Exception as exc:  # pragma: no cover - 环境依赖
            raise FactoryTestSerialError(
                f"cannot open {self.port}: {exc}. "
                "If this is a Windows COM port, use the workbench serial bridge PTY "
                "(e.g. /run/hplc/serial/factory) instead."
            ) from exc

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None
                self._stream.reset()
                self._pending.clear()

    def __enter__(self) -> "FactoryTestClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---- 收发 ----

    def _require_open(self):
        if self._ser is None:
            raise FactoryTestSerialError("serial port is not open; call open() first")
        return self._ser

    def _drain_input(self) -> None:
        ser = self._require_open()
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        self._stream.reset()
        self._pending.clear()

    def _pump(self, timeout: float) -> Optional[Frame]:
        ser = self._require_open()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._pending:
                return self._pending.pop(0)
            try:
                waiting = ser.in_waiting
            except Exception:
                waiting = 0
            chunk = ser.read(waiting or 1)
            if chunk:
                self._pending.extend(self._stream.feed(chunk))
            else:
                time.sleep(0.005)
        if self._pending:
            return self._pending.pop(0)
        return None

    def send_bytes(self, data: bytes) -> None:
        ser = self._require_open()
        try:
            ser.write(data)
            ser.flush()
        except Exception as exc:  # pragma: no cover - 环境依赖
            raise FactoryTestSerialError(f"serial write failed: {exc}") from exc

    def send_text(self, text: str, append_newline: bool = True) -> None:
        payload = text.rstrip("\r\n")
        if append_newline:
            payload += "\r\n"
        self.send_bytes(payload.encode("ascii", errors="ignore"))

    def send_frame(self, ctrl: int, di: int, data: bytes = b"") -> bytes:
        frame = build_frame(ctrl, di, data, addr=self.addr)
        self.send_bytes(frame)
        return frame

    def read_frame(self, timeout: Optional[float] = None) -> Optional[Frame]:
        return self._pump(self.timeout if timeout is None else timeout)

    # ---- 事务 ----

    def transact_read(
        self,
        di: int,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> Frame:
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        expected = read_response_ctrl(CTRL_READ)

        last_error: Optional[Exception] = None
        for _ in range(max(1, retries)):
            self._drain_input()
            self.send_frame(CTRL_READ, di)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._pump(min(0.1, max(0.0, deadline - time.monotonic())))
                if frame is None:
                    continue
                if frame.ctrl == CTRL_WRITE_NAK:
                    raise FactoryTestNak(
                        f"device NAK for read 0x{di:04X}", frame
                    )
                if frame.di == di and frame.ctrl in (expected, 0x80, 0x90):
                    return frame
            last_error = FactoryTestTimeout(
                f"timeout waiting read response 0x{di:04X}"
            )
        raise last_error or FactoryTestTimeout(f"timeout waiting read response 0x{di:04X}")

    def transact_write(
        self,
        di: int,
        data: bytes = b"",
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        raise_on_nak: bool = True,
    ) -> Frame:
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries

        last_error: Optional[Exception] = None
        for _ in range(max(1, retries)):
            self._drain_input()
            self.send_frame(CTRL_WRITE, di, data)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._pump(min(0.1, max(0.0, deadline - time.monotonic())))
                if frame is None:
                    continue
                # 写响应 0x82/0x83 固定没有 DI 字段，按控制字节匹配。
                if frame.ctrl == CTRL_WRITE_OK:
                    return frame
                if frame.ctrl == CTRL_WRITE_NAK:
                    if raise_on_nak:
                        raise FactoryTestNak(
                            f"device NAK for write 0x{di:04X}", frame
                        )
                    return frame
                # 其他帧（例如上一条读响应）忽略，继续等。
            last_error = FactoryTestTimeout(f"timeout waiting write ACK 0x{di:04X}")
        raise last_error or FactoryTestTimeout(f"timeout waiting write ACK 0x{di:04X}")

    # ---- 厂测流程 ----

    def reset(self) -> Frame:
        """A5 0x0004：设备先回 0x82，再硬复位。"""

        return self.transact_write(DI_RESET, b"")

    def enter(
        self,
        at_prelude: bool = True,
        at_commands: Sequence[str] = ("AT+FACTMODE", "AT+RESET"),
        boot_delay: float = 0.5,
        timeout: float = 10.0,
    ) -> None:
        """进入厂测模式。

        AT+FACTMODE 通常是治具/适配器侧命令，STA 固件本身不解析；
        A5 0x0005 才是 STA 的厂测入口。进入窗口只有上电后的 500ms，
        因此这里在 boot_delay 后按 100ms 间隔重试 0x0005，直到设备 ACK。
        """

        if at_prelude:
            for command in at_commands:
                self.send_text(command)
            if boot_delay:
                time.sleep(boot_delay)

        deadline = time.monotonic() + timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                self.transact_write(
                    DI_ENTER_FACTORY, b"", timeout=0.3, retries=1
                )
                return
            except (FactoryTestTimeout, FactoryTestNak) as exc:
                last_error = exc
                time.sleep(0.1)
        raise FactoryTestTimeout(
            f"failed to enter factory mode within {timeout}s"
        ) from last_error

    # ---- 常用命令 ----

    def read_version(self) -> dict:
        return decode_version(self.transact_read(DI_VERSION).data)

    def read_address(self) -> dict:
        return decode_address(self.transact_read(DI_ADDRESS).data)

    def get_channel(self) -> int:
        return decode_channel(self.transact_read(DI_RF_CHANNEL).data)["channel"]

    def set_channel(self, channel: int) -> Frame:
        return self.transact_write(DI_RF_CHANNEL, bytes((channel & 0xFF,)))

    def get_option(self) -> int:
        return decode_option(self.transact_read(DI_RF_OPTION).data)["option"]

    def set_option(self, option: int) -> Frame:
        return self.transact_write(DI_RF_OPTION, bytes((option & 0xFF,)))

    def get_power(self) -> int:
        return decode_power(self.transact_read(DI_RF_POWER).data)["power"]

    def set_power(self, power: int) -> Frame:
        return self.transact_write(DI_RF_POWER, bytes((power & 0xFF,)))

    def get_band(self) -> int:
        return decode_band(self.transact_read(DI_HPLC_BAND).data)["band"]

    def set_band(self, band: int) -> Frame:
        return self.transact_write(DI_HPLC_BAND, bytes((band & 0xFF,)))

    def get_hplc_power(self) -> int:
        return decode_power(self.transact_read(DI_HPLC_POWER).data)["power"]

    def set_hplc_power(self, power: int) -> Frame:
        return self.transact_write(DI_HPLC_POWER, bytes((power & 0xFF,)))

    def rf_call(self, length: int = 100) -> bool:
        """0x0109 RF 点名。返回 True/False；失败时设备回 0x83，不抛异常。"""

        try:
            self.transact_write(DI_RF_CALL, bytes((length & 0xFF,)), timeout=2.0)
            return True
        except FactoryTestNak:
            return False

    def read_rf_call_result(self) -> dict:
        return decode_rf_call_result(self.transact_read(DI_RF_CALL_RESULT).data)

    def hplc_call(self, length: int = 100) -> bool:
        """0x0205 HPLC 点名。返回 True/False；失败时设备回 0x83，不抛异常。"""

        try:
            self.transact_write(DI_HPLC_CALL, bytes((length & 0xFF,)), timeout=2.0)
            return True
        except FactoryTestNak:
            return False

    def read_hplc_call_result(self) -> dict:
        return decode_hplc_call_result(self.transact_read(DI_HPLC_CALL_RESULT).data)
