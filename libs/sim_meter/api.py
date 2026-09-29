"""模拟电表 REST API：FastAPI 子应用（create_simeter_app）。

独立运行（python -m sim_meter api --port 8782）或挂载到侦听台
（app.mount("/api/simeter", create_simeter_app(prefix=""))）。

接口：
- GET  /api/simeter/status          串口/电表状态（地址、版本、波特率、会话）
- GET  /api/simeter/ports           列出可用串口
- POST /api/simeter/open            打开串口（port/baudrate/parity…）
- POST /api/simeter/close           关闭串口
- POST /api/simeter/baud            波特率切换（打开时热切换，未打开仅改参数）
- GET  /api/simeter/config          电表配置（地址/版本/串口参数/数据集摘要）
- POST /api/simeter/config          修改地址、645 版本（07/97）、串口参数
- GET  /api/simeter/data            当前数据集（抄表数据标识与数值）
- POST /api/simeter/data            设置数据标识（如把 00010000 电能改值）
- POST /api/simeter/clock           设置表内时钟
- GET  /api/simeter/jzjc            重庆即装即采配置（设备类型/描述符/下挂设备）
- POST /api/simeter/jzjc            更新即装即采配置（类型可改，描述符缺省取预设）
- POST /api/simeter/jzjc/report     生成即装即采设备信息上送报文（sta / afn06f7）
- POST /api/simeter/handle_hex      无串口调测：把主站请求帧喂给电表脑，返回应答帧
- POST /api/simeter/send_hex        串口打开时手动注入原始帧
- GET  /api/simeter/frames          会话帧日志查询（dir/after_seq/limit）

帧日志：每次 open 生成一个 MeterJournal 会话（session_id = sm-*），
tx/rx/event 逐条记录并入 data/logs/simeter/sm-*.jsonl。
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sim_meter.journal import MeterJournal, default_log_dir
from sim_meter.meter import JZJC_TYPE_PRESETS, MeterBrain
from sim_meter.serial_channel import MeterSerialChannel, list_serial_ports

if TYPE_CHECKING:  # 工作台挂载时注入；独立运行不依赖
    from shared.serial_resources import SerialResourceRegistry


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------
class OpenSpec(BaseModel):
    port: Optional[str] = None
    mapping_id: Optional[str] = None       # 串口目录映射 ID（如 listener），工作台模式可用
    baudrate: Optional[int] = None
    bytesize: Optional[int] = None
    parity: Optional[str] = None
    stopbits: Optional[int] = None


class BaudSpec(BaseModel):
    baudrate: int


class ConfigSpec(BaseModel):
    addr: Optional[str] = None
    version: Optional[str] = None          # "07"/"2007" 或 "97"/"1997"，默认 07
    baudrate: Optional[int] = None
    parity: Optional[str] = None
    bytesize: Optional[int] = None
    stopbits: Optional[int] = None


class DataSpec(BaseModel):
    di: str                                # 显示序 DI3DI2DI1DI0，如 00010000
    value: str
    scale: Optional[int] = None
    length: Optional[int] = None


class ClockSpec(BaseModel):
    time: str                              # "YYYY-MM-DD HH:MM:SS"


class JzjcSubDevice(BaseModel):
    addr: str                              # 12 位十六进制（6B BCD）
    descriptor: str = ""
    protocol_type: int = 0x02


class JzjcSpec(BaseModel):
    enabled: Optional[bool] = None
    device_type: Optional[str] = None      # "0x10" / "0x12" / "0x28"…
    descriptor: Optional[str] = None
    protocol_type: Optional[int] = None
    seq: Optional[int] = None
    sub_devices: Optional[List[JzjcSubDevice]] = None


class JzjcReportSpec(BaseModel):
    style: str = "sta"                     # sta | afn06f7
    seq: Optional[int] = None
    send: bool = False                     # 串口打开时把报文 hex 从本口发出


class HandleHexSpec(BaseModel):
    hex: str                               # 主站请求帧（可含 FE 前导/空白）


class SendHexSpec(BaseModel):
    hex: str


class FramesQuery(BaseModel):
    dir: Optional[str] = None              # rx | tx | event
    after_seq: int = 0
    limit: int = 200


# ---------------------------------------------------------------------------
# 服务装配：电表脑 + 串口通道 + 会话日志（同进程单例）
# ---------------------------------------------------------------------------
class SimMeterService:
    def __init__(self, brain: Optional[MeterBrain] = None,
                 log_dir=None,
                 resource_registry: Optional["SerialResourceRegistry"] = None,
                 port_resolver=None):
        """port_resolver：mapping_id/端口 → 实际打开参数（dict，含 port/baudrate…）。

        工作台挂载时注入（shared.serial_resources 注册表 + 串口目录解析），
        实现底座口经工作台统一接管、防双开；独立运行两者缺省为 None，行为不变。
        """
        self.brain = brain or MeterBrain()
        self.channel: Optional[MeterSerialChannel] = None
        self.journal: Optional[MeterJournal] = None
        self.log_dir = log_dir if log_dir is not None else default_log_dir()
        self.resource_registry = resource_registry
        self.port_resolver = port_resolver
        self._owner_id = "simeter"
        self._lock = threading.RLock()

    def _resolve_open_params(self, spec: OpenSpec) -> dict:
        """收敛 mapping_id/端口为实际打开参数；返回 dict 至少含 port。"""
        if self.port_resolver is not None:
            return dict(self.port_resolver(
                mapping_id=spec.mapping_id, port=spec.port,
                baudrate=spec.baudrate, bytesize=spec.bytesize,
                parity=spec.parity, stopbits=spec.stopbits))
        if spec.mapping_id:
            raise HTTPException(400, "独立运行未接串口目录，不支持 mapping_id（请用 port）")
        return {"port": spec.port}

    def open(self, spec: OpenSpec) -> dict:
        with self._lock:
            if self.channel is not None and self.channel.is_open():
                raise HTTPException(409, f"串口已打开：{self.channel.port}，请先关闭")
            resolved = self._resolve_open_params(spec)
            port = str(resolved.get("port") or "").strip()
            if not port:
                raise HTTPException(400, "必须提供 port（COMx / /dev/ttyUSB* / loop://）或 mapping_id")
            if self.resource_registry is not None:
                try:
                    self.resource_registry.reserve(
                        self._owner_id, label="模拟电表",
                        resource_id=str(resolved.get("mapping_id") or ""),
                        aliases=[port])
                except Exception as exc:
                    raise HTTPException(409, f"串口资源占用失败：{exc}")
            if self.journal is not None:
                self.journal.close_file()
            self.journal = MeterJournal(port=port, log_dir=self.log_dir)
            try:
                self.channel = MeterSerialChannel(
                    self.brain, port=port,
                    baudrate=resolved.get("baudrate"), bytesize=resolved.get("bytesize"),
                    parity=resolved.get("parity"), stopbits=resolved.get("stopbits"),
                    journal=self.journal)
                self.channel.open()
            except Exception as exc:
                self.channel = None
                if self.resource_registry is not None:
                    self.resource_registry.release(self._owner_id)
                raise HTTPException(500, f"打开串口失败: {exc}") from exc
            return self.status()

    def close(self) -> dict:
        with self._lock:
            if self.channel is not None:
                self.channel.close()
                self.channel = None
            if self.resource_registry is not None:
                self.resource_registry.release(self._owner_id)
            return self.status()

    def set_baudrate(self, baudrate: int) -> dict:
        with self._lock:
            self.brain.set_serial_params(baudrate=baudrate)
            if self.channel is not None and self.channel.is_open():
                self.channel.set_baudrate(baudrate)
            return self.status()

    def status(self) -> dict:
        return {
            "brain": {
                "addr": self.brain.addr_hex,
                "version": self.brain.version,
                "baudrate": self.brain.baudrate,
                "parity": self.brain.parity,
                "bytesize": self.brain.bytesize,
                "stopbits": self.brain.stopbits,
                "rx_count": self.brain.rx_count,
                "tx_count": self.brain.tx_count,
            },
            "serial": self.channel.status() if self.channel is not None else None,
            "session": self.journal.info() if self.journal is not None else None,
        }


def create_simeter_app(prefix: str = "/api/simeter",
                       service: Optional[SimMeterService] = None,
                       resource_registry: Optional["SerialResourceRegistry"] = None,
                       port_resolver=None) -> FastAPI:
    """创建模拟电表 API 子应用。挂载时传 prefix="" 避免双前缀。

    resource_registry / port_resolver：工作台挂载时注入（串口资源防双开 +
    串口目录 mapping_id 解析）；service 未提供时据此装配默认服务。
    """
    svc = service or SimMeterService(
        resource_registry=resource_registry, port_resolver=port_resolver)
    app = FastAPI(title="模拟电表（sim_meter）", version="0.1.0")
    app.state.simeter_service = svc      # 工作台 AI 桥经 state 取服务（不经 HTTP 回调）
    routes_prefix = prefix.rstrip("/")

    def _bad_request(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    @app.get(routes_prefix + "/_meta")
    def meta() -> dict:
        """AI 使用小抄：端点清单 + 典型调用序列 + 常见错误语义（自描述，省 token）。"""
        return {
            "service": "sim_meter 模拟电表（645-2007/1997 从站仿真）",
            "role": "被测从站：挂底座口 485，应答 STA/主站的 645 请求",
            "open_sequence": [
                "POST /api/simeter/open {\"mapping_id\":\"listener\"}",
                "GET  /api/simeter/status",
                "GET  /api/simeter/frames?limit=50",
            ],
            "endpoints": {
                "GET /status": "电表脑+串口+会话状态",
                "GET /ports": "本机串口枚举",
                "POST /open": "打开串口（mapping_id 或 port；工作台模式支持 mapping_id=listener）",
                "POST /close": "关闭串口并释放资源注册",
                "POST /baud": "波特率热切换 {baudrate}",
                "GET/POST /config": "读/改 地址 addr、645 版本 version(07/97)、串口参数",
                "GET/POST /data": "读/设 数据标识（di 显示序如 00010000，value，scale 小数位）",
                "POST /clock": "设置表内时钟 {time:\"YYYY-MM-DD HH:MM:SS\"}",
                "GET/POST /jzjc": "重庆即装即采配置",
                "POST /jzjc/report": "生成即装即采上送报文（style=sta|afn06f7）",
                "POST /handle_hex": "无串口调测：喂 645 请求帧 hex，返回应答帧 hex",
                "POST /send_hex": "串口打开时注入原始帧 hex",
                "GET /frames": "会话帧日志（dir=rx|tx|event，after_seq，limit）",
            },
            "errors": {
                "409": "串口已打开 / 资源被其他后端占用（先 close 或释放占用方）",
                "400": "参数不合法（地址/DI/版本等）",
                "500": "打开串口失败（设备不存在/被系统占用）",
            },
            "notes": [
                "出厂地址 000000000001；对档联调先 POST /config 设为档案地址（如 999999999999），否则读数据不响应",
                "13H 读地址支持 FE×6 / 99×6 / AA×6 三种广播（对齐 STA 绑表扫掠）",
                "WSL pts 不接受 PARENB：经虚拟串口/桥接环境开口一律 parity=N（线参由对端决定）",
            ],
        }

    @app.get(routes_prefix + "/status")
    def status() -> dict:
        return svc.status()

    @app.get(routes_prefix + "/ports")
    def ports() -> dict:
        return {"ports": list_serial_ports()}

    @app.post(routes_prefix + "/open")
    def open_port(spec: OpenSpec) -> dict:
        return svc.open(spec)

    @app.post(routes_prefix + "/close")
    def close_port() -> dict:
        return svc.close()

    @app.post(routes_prefix + "/baud")
    def set_baud(spec: BaudSpec) -> dict:
        try:
            return svc.set_baudrate(spec.baudrate)
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.get(routes_prefix + "/config")
    def get_config() -> dict:
        cfg = svc.status()["brain"]
        cfg["jzjc"] = svc.brain.get_jzjc()
        cfg["registers"] = {k: v["value"] for k, v in svc.brain.get_registers().items()}
        cfg["jzjc_types"] = {
            f"0x{k:02X}": v["name"] for k, v in JZJC_TYPE_PRESETS.items()}
        return cfg

    @app.post(routes_prefix + "/config")
    def set_config(spec: ConfigSpec) -> dict:
        try:
            if spec.addr is not None:
                svc.brain.set_address(spec.addr)
            if spec.version is not None:
                svc.brain.set_version(spec.version)
            svc.brain.set_serial_params(baudrate=spec.baudrate, parity=spec.parity,
                                        bytesize=spec.bytesize, stopbits=spec.stopbits)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if (spec.baudrate is not None and svc.channel is not None
                and svc.channel.is_open()):
            svc.channel.set_baudrate(spec.baudrate)
        return svc.status()

    @app.get(routes_prefix + "/data")
    def get_data() -> dict:
        return {"addr": svc.brain.addr_hex, "registers": svc.brain.get_registers()}

    @app.post(routes_prefix + "/data")
    def set_data(spec: DataSpec) -> dict:
        try:
            item = svc.brain.set_register(spec.di, spec.value,
                                          scale=spec.scale, length=spec.length)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"di": spec.di.upper(), "item": item}

    @app.post(routes_prefix + "/clock")
    def set_clock(spec: ClockSpec) -> dict:
        from datetime import datetime
        try:
            dt = datetime.strptime(spec.time.strip(), "%Y-%m-%d %H:%M:%S")
            meter_now = svc.brain.set_clock(dt)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"meter_time": meter_now.strftime("%Y-%m-%d %H:%M:%S")}

    @app.get(routes_prefix + "/jzjc")
    def get_jzjc() -> dict:
        return svc.brain.get_jzjc()

    @app.post(routes_prefix + "/jzjc")
    def set_jzjc(spec: JzjcSpec) -> dict:
        try:
            return svc.brain.set_jzjc(
                enabled=spec.enabled, device_type=spec.device_type,
                descriptor=spec.descriptor, protocol_type=spec.protocol_type,
                seq=spec.seq,
                sub_devices=[sd.model_dump() for sd in spec.sub_devices]
                if spec.sub_devices is not None else None)
        except (ValueError, UnicodeEncodeError) as exc:
            raise _bad_request(exc) from exc

    @app.post(routes_prefix + "/jzjc/report")
    def jzjc_report(spec: JzjcReportSpec) -> dict:
        try:
            payload = svc.brain.build_jzjc_report(style=spec.style, seq=spec.seq)
        except (ValueError, UnicodeEncodeError) as exc:
            raise _bad_request(exc) from exc
        sent = False
        if spec.send:
            if svc.channel is None or not svc.channel.is_open():
                raise HTTPException(409, "串口未打开，无法发送（可 send=false 仅生成报文）")
            svc.channel.send_frame(payload)
            sent = True
        return {"style": spec.style, "payload_hex": payload.hex().upper(),
                "length": len(payload), "sent": sent}

    @app.post(routes_prefix + "/handle_hex")
    def handle_hex(spec: HandleHexSpec) -> dict:
        try:
            result = svc.brain.handle_hex(spec.hex)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        if result is None:
            return {"reply_hex": None, "note": {"error": "处理异常"}}
        return {
            "reply_hex": result.reply.hex().upper() if result.reply is not None else None,
            "reply_wire_hex": result.reply_wire.hex().upper() if result.reply is not None else None,
            "note": result.note,
            "action": list(result.action) if result.action else None,
        }

    @app.post(routes_prefix + "/send_hex")
    def send_hex(spec: SendHexSpec) -> dict:
        if svc.channel is None or not svc.channel.is_open():
            raise HTTPException(409, "串口未打开")
        try:
            n = svc.channel.send_hex(spec.hex)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"sent_bytes": n}

    @app.get(routes_prefix + "/frames")
    def frames(dir: Optional[str] = None, after_seq: int = 0,
               limit: int = 200, session_id: Optional[str] = None) -> dict:
        if svc.journal is None:
            return {"session": None, "entries": []}
        info = svc.journal.info()
        if session_id and session_id != info.get("session_id"):
            raise HTTPException(404, f"会话不存在或已关闭：{session_id}")
        entries = svc.journal.entries(direction=dir, after_seq=after_seq,
                                      limit=limit)
        return {"session": info, "entries": entries}

    return app


def main(host: str = "127.0.0.1", port: int = 8782) -> None:
    import uvicorn
    uvicorn.run(create_simeter_app(), host=host, port=port)
