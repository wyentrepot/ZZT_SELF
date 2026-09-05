"""AI 验证 REST API：模拟集中器验证任务的 HTTP 入口。

独立 FastAPI 子应用（create_simcon_app），可独立运行（python -m
sim_concentrator.api --port 8781）或挂载到侦听台 create_app。

接口：
- GET  /api/simcon/status           串口/模块状态（含帧日志会话摘要）
- GET  /api/simcon/responders       列出当前生效应答规则（内置+覆盖）
- GET  /api/simcon/ports            列出可用串口
- POST /api/simcon/verify           执行一个验证任务，返回结论 JSON
- POST /api/simcon/step             单步语义执行：下发指定 afn/fn 或等待一帧（感知主动上报）
- GET  /api/simcon/frames           会话帧日志查询（本次下发过什么帧 / CCO 主动上报过什么帧 / 有无某类 afn 上行帧）
- GET  /api/simcon/session          当前会话与最近会话信息
- POST /api/simcon/open             打开串口
- POST /api/simcon/close            关闭串口

会话帧日志：每次 open（或 verify 自建临时串口）生成一个 FrameJournal 会话
（session_id = sc-*），tx/rx 帧逐条记录并入 data/logs/simcon/sc-*.jsonl 持久化。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sim_concentrator.journal import SessionManager
from sim_concentrator.runner import execute_task, run_single_step
from sim_concentrator.responder import Responder
from sim_concentrator.scenario_codec import build_send, load_profile
from shared.serial_mapping import SerialPortCatalog
from shared.serial_resources import SerialResourceRegistry
from sim_concentrator.serial_io import (
    SerialIO,
    list_serial_port_details,
    resolve_serial_config,
)


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------
class OpenSpec(BaseModel):
    # 所有字段均可省略；省略时采用 config/serial_ports.json 的 simcon 映射默认值。
    port: Optional[str] = None
    mapping_id: Optional[str] = None
    baudrate: Optional[int] = None
    bytesize: Optional[int] = None
    parity: Optional[str] = None
    stopbits: Optional[int] = None


class SendSpec(BaseModel):
    afn: int = 0
    seq: int = 0
    rtsa: Optional[str] = None
    msaa: int = 0x01
    pw: int = 0
    userdata: Any = b""


class ExpectSpec(BaseModel):
    afn: Optional[int] = None
    dir: Optional[str] = None
    nested: Optional[bool] = None
    fields: Optional[Dict[str, Any]] = None
    nested_fields: Optional[List[Dict[str, Any]]] = None
    any: Optional[bool] = None


class StepSpec(BaseModel):
    name: Optional[str] = None
    send: Optional[Dict[str, Any]] = None
    expect: Optional[Dict[str, Any]] = None
    expect_timeout: float = 5.0
    expect_no_reply: bool = False
    responders: Optional[List[Dict[str, Any]]] = None


class VerifyTask(BaseModel):
    id: Optional[str] = "verify.task"
    port: Optional[str] = None
    mapping_id: Optional[str] = None
    baudrate: Optional[int] = None
    bytesize: Optional[int] = None
    parity: Optional[str] = None
    stopbits: Optional[int] = None
    enable_responder: bool = True
    fail_fast: bool = True
    responders: Optional[List[Dict[str, Any]]] = None
    steps: List[Dict[str, Any]] = []


class StepRequest(BaseModel):
    """单步语义执行请求（ADR-5：send 只写 afn/fn+params，raw 报错）。

    REQS-0027：expect_timeout=None 时按 expect_rules per-Fn 档位自动取值
    （默认 5s / 单抄 59s / 并抄 99s）；auto_expect=True 且未显式给 expect 时
    按规则库自动生成默认 expect（显式 expect 可覆盖）。
    """
    send: Optional[Dict[str, Any]] = None
    profile: Optional[str] = None
    expect: Optional[Dict[str, Any]] = None
    expect_timeout: Optional[float] = None
    auto_expect: bool = True
    expect_no_reply: bool = False
    recv_only: bool = False
    enable_responder: bool = True
    name: Optional[str] = None


class BuildRequest(BaseModel):
    """语义化构帧预览请求：只经 scenario_codec 计算帧字节，不触串口。"""
    afn: Any
    fn: Any
    params: Dict[str, Any] = {}
    direction: str = "down"
    profile: Optional[str] = None
    seq: int = 1


class BatchReadRequest(BaseModel):
    """并发抄表任务创建请求（REQS-0027 G5）。"""
    meters: List[str] = []
    max_concurrent: int = 5
    mode: str = "single"            # single=02H-F1 单抄 / batch=F1H-F1 并发抄表
    protocol_type: int = 2          # 00 透明 / 02=645-2007 / 03=698.45
    timeout: Optional[float] = None  # 缺省按 expect_rules 档位（单抄59s/并抄99s）
    profile: Optional[str] = None
    port: Optional[str] = None
    mapping_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_simcon_app(prefix: str = "/api/simcon", resource_registry: SerialResourceRegistry | None = None,
                      journal_dir=None) -> FastAPI:
    """创建模拟集中器子应用。

    prefix 控制路由前缀：
    - 默认 "/api/simcon"：独立运行（端口 8781）或直接作为根挂载时使用，路由为 /api/simcon/*。
    - 传入 "" 时路由为相对路径（/status、/ports...），供 module_log 挂载到 /api/simcon 使用
      （app.mount("/api/simcon", create_simcon_app(prefix=""))，避免双前缀）。
    journal_dir：会话帧日志目录（缺省 data/logs/simcon；测试注入 tmp_path）。
    """
    app = FastAPI(title="模拟集中器验证工具", version="0.2.0")
    catalog = SerialPortCatalog.load()
    app.state.serial_port_catalog = catalog
    app.state.serial_resource_registry = resource_registry or SerialResourceRegistry()
    _holder = {"io": None, "lock": threading.Lock()}
    # REQS-0013：1376.2 收发库（单文件 sqlite），会话帧同步落库；缺失时降级为纯帧日志。
    _store = None
    try:
        from sim_concentrator.store import ListenerStore
        _store = ListenerStore()
    except Exception:
        _store = None
    app.state.simcon_store = _store
    # 会话帧日志：open/verify 产生的会话都登记在此，供 /frames /session 查询
    _sessions = SessionManager(log_dir=journal_dir, store=_store)
    app.state.simcon_sessions = _sessions
    app.state.simcon_step_state = {"profile": None, "seq": 0, "lock": threading.Lock()}
    # REQS-0027 G5：并发抄表任务注册表（job_id → BatchReadJob）
    app.state.simcon_batch_jobs = {}

    def _resolve(
        port: Optional[str] = None,
        mapping_id: Optional[str] = None,
        baudrate: Optional[int] = None,
        bytesize: Optional[int] = None,
        parity: Optional[str] = None,
        stopbits: Optional[int] = None,
    ) -> dict[str, Any]:
        return resolve_serial_config(
            port,
            mapping_id=mapping_id,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            catalog=catalog,
        )

    def _port_details() -> list[dict[str, Any]]:
        # simcon 无固定映射：展示所有未映射实际端口（含离线映射项供 UI 标记）。
        details = [
            detail for detail in list_serial_port_details(catalog)
            if detail.get("usage") in ("", "simcon")
        ]
        details = sorted(details, key=lambda detail: (
            0 if detail.get("mapping_id") == "simcon" else 1,
            str(detail.get("device", "")),
        ))
        try:
            from shared.serial_tags import SerialTagStore

            return SerialTagStore().merge_port_details(details)
        except Exception:  # noqa: BLE001 - 标签层故障不影响串口枚举
            return details

    def _io() -> Optional[SerialIO]:
        with _holder["lock"]:
            return _holder["io"]

    def _auto_ack_responder() -> Responder:
        """常驻自动应答器：只对「模块主动上报」回确认（06H-F230 采集上报、
        06H-F3 工况变动、03H-F10 运行模式 → 00H-F1），并压制查询类帧。

        不开内置查询 echo 规则（10H/03H echo 会与 CCO 形成应答回环）；
        查询/配置类帧的显式应答仍由 step 内的完整 responder 处理。
        """
        return Responder(override_rules=[
            {"id": "autoack.06f230", "match": {"afn": 0x06, "fn": 230},
             "reply": {"afn": 0x00, "fn": 1, "format": "local"}},
            {"id": "autoack.06f3", "match": {"afn": 0x06, "fn": 3},
             "reply": {"afn": 0x00, "fn": 1, "format": "local"}},
            {"id": "autoack.03f10", "match": {"afn": 0x03, "fn": 10},
             "reply": {"afn": 0x00, "fn": 1, "format": "local"}},
        ], builtin=False)

    def _open_io(resolved: dict[str, Any]) -> SerialIO:
        with _holder["lock"]:
            io = _holder["io"]
            if io is None or not io.is_open():
                journal = _sessions.open_session(str(resolved["port"]))
                io = SerialIO(
                    port=resolved["port"],
                    baudrate=resolved["baudrate"],
                    bytesize=resolved["bytesize"],
                    parity=resolved["parity"],
                    stopbits=resolved["stopbits"],
                    port_identity=resolved["port_identity"],
                    resource_registry=app.state.serial_resource_registry,
                    journal=journal,
                    auto_responder=_auto_ack_responder(),
                )
                io.open()
                _holder["io"] = io
            return io

    def _close_io() -> None:
        with _holder["lock"]:
            io, _holder["io"] = _holder["io"], None
        if io is not None:
            journal = getattr(io, "journal", None)
            io.close()
            if journal is not None:
                _sessions.finalize(journal)

    # P4：把 open/close 服务函数暴露到 state，供统一工作台 SerialProfileApplier
    # 以适配器方式注入（不经 HTTP 回调，避免服务层自调网络）。
    def simcon_open_io(resolved: dict[str, Any]) -> None:
        _open_io(resolved)

    def simcon_close_io() -> None:
        _close_io()

    app.state.simcon_open_io = simcon_open_io
    app.state.simcon_close_io = simcon_close_io
    app.state.simcon_catalog = catalog

    # -- 任务/单步执行核心：HTTP 路由与 AI 控制面（进程内注入）共用 -------------
    def _run_verify_task(task_payload: dict) -> dict:
        """执行一个验证任务；会话帧日志随任务写入并登记（临时串口用后保留日志）。"""
        resolved = _resolve(
            task_payload.get("port"), task_payload.get("mapping_id"),
            task_payload.get("baudrate"), task_payload.get("bytesize"),
            task_payload.get("parity"), task_payload.get("stopbits"),
        )
        if not task_payload.get("steps"):
            # 空任务/无步骤：不碰串口，但仍返回可审计的映射解析结果。
            return {
                "task_id": task_payload.get("id", "verify.task"),
                "port": resolved["port"],
                "baudrate": resolved["baudrate"],
                "mapping_id": resolved["mapping_id"],
                "port_identity": resolved["port_identity"],
                "steps": [],
                "summary": {"total": 0, "pass": 0, "fail": 0, "verdict": "fail"},
            }
        task_payload = dict(task_payload)
        task_payload.update(resolved)
        io = _io()
        if io is None or not io.is_open():
            # 任务自带串口参数：自建并独占，执行后关闭（帧日志会话保留可查）。
            journal = _sessions.open_session(str(resolved["port"]))
            io = SerialIO(
                port=resolved["port"],
                baudrate=resolved["baudrate"],
                bytesize=resolved["bytesize"],
                parity=resolved["parity"],
                stopbits=resolved["stopbits"],
                port_identity=resolved["port_identity"],
                resource_registry=app.state.serial_resource_registry,
                journal=journal,
            )
            io.open()
            try:
                return execute_task(task_payload, io=io, journal=journal)
            finally:
                io.close()
                _sessions.finalize(journal)
        return execute_task(task_payload, io=io)

    def _run_step_task(payload: dict) -> dict:
        """单步语义执行；串口未开时按 simcon 映射自动打开。"""
        io = _io()
        if io is None or not io.is_open():
            resolved = _resolve(
                payload.get("port"), payload.get("mapping_id"),
                payload.get("baudrate"), payload.get("bytesize"),
                payload.get("parity"), payload.get("stopbits"),
            )
            io = _open_io(resolved)
        step_state = app.state.simcon_step_state
        with step_state["lock"]:
            profile_id = payload.get("profile") or step_state["profile"] or "anhui"
            step_state["profile"] = str(profile_id)
            step_state["seq"] += 1
            seq = int(step_state["seq"])
        profile = load_profile(str(profile_id))
        return run_single_step(
            io,
            send=payload.get("send"),
            profile=profile,
            expect=payload.get("expect"),
            expect_timeout=(None if payload.get("expect_timeout") is None
                            else float(payload.get("expect_timeout"))),
            auto_expect=bool(payload.get("auto_expect", True)),
            expect_no_reply=bool(payload.get("expect_no_reply")),
            recv_only=bool(payload.get("recv_only")),
            enable_responder=bool(payload.get("enable_responder", True)),
            name=str(payload.get("name") or "单步"),
            seq=seq,
        )

    def simcon_frames(*, session_id: str | None = None, direction: str | None = None,
                      updown: str | None = None, afn: str | None = None,
                      fn: str | None = None, kind: str | None = None,
                      run_id: str | None = None, after_seq: int = 0,
                      limit: int = 100) -> dict:
        journal = _sessions.resolve(session_id)
        if journal is None:
            raise LookupError("当前没有帧日志会话（先 open 串口或执行 verify/step）")
        return journal.query(
            direction=direction, updown=updown, afn=afn, fn=fn,
            kind=kind, run_id=run_id, after_seq=after_seq, limit=limit,
        )

    def simcon_session() -> dict:
        current = _sessions.current_or_latest()
        return {
            "current": current.info() if current is not None else None,
            "sessions": _sessions.list_info(),
        }

    def simcon_open(spec: dict | None = None) -> dict:
        spec = dict(spec or {})
        resolved = _resolve(
            spec.get("port"), spec.get("mapping_id"),
            spec.get("baudrate"), spec.get("bytesize"),
            spec.get("parity"), spec.get("stopbits"),
        )
        io = _open_io(resolved)
        journal = getattr(io, "journal", None)
        return {
            "open": True,
            "port": io.port,
            "mapping_id": resolved["mapping_id"],
            "port_identity": io.port_identity,
            "session_id": journal.session_id if journal is not None else None,
        }

    # REQS-0018：1376.2 收发库只读查询访问器（供 AI 控制面经进程内注入，不经 HTTP 回调）。
    def _simcon_store_or_raise():
        if _store is None:
            raise LookupError("1376.2 收发库未启用（初始化失败或依赖缺失）")
        return _store

    def simcon_store_snapshots(*, afn: str | None = None, fn: str | None = None,
                               limit: int = 20) -> dict:
        return {"items": _simcon_store_or_raise().list_snapshots(afn=afn, fn=fn, limit=limit)}

    def simcon_store_snapshot_items(snapshot_id: int) -> dict:
        return {"items": _simcon_store_or_raise().snapshot_items(int(snapshot_id))}

    def simcon_store_events(*, limit: int = 50) -> dict:
        return {"items": _simcon_store_or_raise().list_report_events(limit=limit)}

    # P4+：把执行核心提升到 state，供统一工作台/AI 控制面进程内注入（不经 HTTP 回调）。
    app.state.simcon_run_verify = _run_verify_task
    app.state.simcon_run_step = _run_step_task
    app.state.simcon_frames = simcon_frames
    app.state.simcon_session = simcon_session
    app.state.simcon_open = simcon_open
    app.state.simcon_store_snapshots = simcon_store_snapshots
    app.state.simcon_store_snapshot_items = simcon_store_snapshot_items
    app.state.simcon_store_events = simcon_store_events

    @app.get(f"{prefix}/status")
    async def status():
        io = _io()
        journal = getattr(io, "journal", None) if io is not None else None
        return {
            "open": io is not None and io.is_open(),
            "port": io.port if io is not None else None,
            "port_identity": io.port_identity if io is not None else None,
            "mapping_error": catalog.mapping_error,
            "pending_frames": io.pending_frames() if io is not None else 0,
            "session": journal.info() if journal is not None else None,
        }

    @app.get(f"{prefix}/ports")
    async def ports():
        port_details = _port_details()
        return {
            "ports": [str(detail["device"]) for detail in port_details],
            "port_details": port_details,
            "mapping_error": catalog.mapping_error,
        }

    @app.get(f"{prefix}/responders")
    async def responders():
        r = Responder()
        return {"rules": r.list_rules()}

    @app.get(f"{prefix}/expect_rules")
    async def expect_rules_endpoint():
        """应答预期规则库（REQS-0027 G2/G3）：映射表 + 否认码语义 + 超时档位。"""
        from sim_concentrator import expect_rules as er
        return er.load()

    @app.post(f"{prefix}/open")
    async def open_serial(request: OpenSpec):
        try:
            resolved = _resolve(
                request.port, request.mapping_id, request.baudrate,
                request.bytesize, request.parity, request.stopbits,
            )
            io = _open_io(resolved)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"串口打开失败：{exc}") from exc
        journal = getattr(io, "journal", None)
        return {
            "open": True,
            "port": io.port,
            "mapping_id": resolved["mapping_id"],
            "port_identity": io.port_identity,
            "session_id": journal.session_id if journal is not None else None,
            "baudrate": getattr(io, "baudrate", resolved["baudrate"]),
            "bytesize": getattr(io, "bytesize", resolved["bytesize"]),
            "parity": getattr(io, "parity", resolved["parity"]),
            "stopbits": getattr(io, "stopbits", resolved["stopbits"]),
        }

    @app.post(f"{prefix}/close")
    async def close_serial():
        _close_io()
        return {"open": False}

    @app.post(f"{prefix}/verify")
    async def verify(task: VerifyTask):
        """执行验证任务并返回逐步判定 + 汇总结论 JSON。"""
        try:
            return _run_verify_task(task.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"验证任务执行失败：{exc}") from exc

    @app.post(f"{prefix}/step")
    async def step(request: StepRequest):
        """单步语义执行：下发指定 afn/fn 或等待一帧（感知 CCO 主动上报）。"""
        try:
            return _run_step_task(request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"单步执行失败：{exc}") from exc

    @app.post(f"{prefix}/build")
    async def build_frame(request: BuildRequest):
        """语义化构帧预览（reqs/0010 P4）：scenario_codec 只算不发，供 UI 帧预览。"""
        try:
            profile = load_profile(request.profile or "anhui")
            data = build_send(
                {"afn": request.afn, "fn": request.fn, "direction": request.direction,
                 "params": request.params},
                profile, seq=request.seq or 1,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"构帧失败：{exc}") from exc
        return {"hex": data.hex(" ").upper(), "length": len(data)}

    @app.get(f"{prefix}/frames")
    async def frames(
        session_id: str = "",
        direction: str = "",
        updown: str = "",
        afn: str = "",
        fn: str = "",
        kind: str = "",
        run_id: str = "",
        after_seq: int = 0,
        limit: int = 100,
    ):
        """会话帧日志查询：默认当前会话；direction=tx|rx、updown=up 即 CCO 主动上报。"""
        journal = _sessions.resolve(session_id or None)
        if journal is None:
            raise HTTPException(
                status_code=404,
                detail="当前没有帧日志会话（先 open 串口或执行 verify/step）")
        return journal.query(
            direction=direction or None, updown=updown or None,
            afn=afn or None, fn=fn or None, kind=kind or None,
            run_id=run_id or None, after_seq=after_seq, limit=limit,
        )

    @app.get(f"{prefix}/session")
    async def session():
        current = _sessions.current_or_latest()
        return {
            "current": current.info() if current is not None else None,
            "sessions": _sessions.list_info(),
        }

    # ---- REQS-0027：并发抄表滑窗任务 + 统一抄读汇总 + 上报分桶 --------------
    def _batch_create(payload: dict) -> dict:
        meters = [str(m).strip() for m in (payload.get("meters") or []) if str(m).strip()]
        if not meters:
            raise ValueError("meters 不能为空（12 位表地址列表）")
        io = _io()
        if io is None or not io.is_open():
            io = _open_io(_resolve(
                payload.get("port"), payload.get("mapping_id"),
                payload.get("baudrate"), payload.get("bytesize"),
                payload.get("parity"), payload.get("stopbits"),
            ))
        profile = load_profile(str(payload.get("profile") or "anhui"))
        with app.state.simcon_step_state["lock"]:
            app.state.simcon_step_state["seq"] += len(meters)
            seq_start = int(app.state.simcon_step_state["seq"])
        from sim_concentrator.batch import BatchReadJob
        job = BatchReadJob(
            io, meters,
            max_concurrent=int(payload.get("max_concurrent") or 5),
            mode=str(payload.get("mode") or "single"),
            protocol_type=int(payload.get("protocol_type") or 2),
            timeout=(None if payload.get("timeout") is None else float(payload["timeout"])),
            profile=profile, seq_start=seq_start,
        )
        app.state.simcon_batch_jobs[job.id] = job
        job.start()
        return job.snapshot()

    def _batch_get(job_id: str) -> dict:
        job = app.state.simcon_batch_jobs.get(job_id)
        if job is None:
            raise LookupError(f"任务不存在: {job_id}")
        return job.snapshot()

    def _batch_list() -> dict:
        jobs = {jid: job.snapshot() for jid, job in app.state.simcon_batch_jobs.items()}
        return {"jobs": jobs}

    def _batch_stop(job_id: str) -> dict:
        job = app.state.simcon_batch_jobs.get(job_id)
        if job is None:
            raise LookupError(f"任务不存在: {job_id}")
        job.stop()
        return job.snapshot()

    def _readings(**kw) -> dict:
        from sim_concentrator.aggregate import collect_readings
        return collect_readings(_store, app.state.simcon_batch_jobs, **kw)

    def _report_buckets(**kw) -> dict:
        from sim_concentrator.aggregate import report_buckets
        return report_buckets(_store, **kw)

    app.state.simcon_batch_create = _batch_create
    app.state.simcon_batch_get = _batch_get
    app.state.simcon_batch_list = _batch_list
    app.state.simcon_batch_stop = _batch_stop
    app.state.simcon_readings = _readings
    app.state.simcon_report_buckets = _report_buckets

    @app.post(f"{prefix}/batch_read")
    async def batch_read(request: BatchReadRequest):
        """创建并发抄表任务（G5）：滑窗调度，回一帧补一发保持在途=最大并发。"""
        try:
            return _batch_create(request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"并发任务创建失败：{exc}") from exc

    @app.get(f"{prefix}/batch_read")
    async def batch_read_list():
        return _batch_list()

    @app.get(f"{prefix}/batch_read/{{job_id}}")
    async def batch_read_job(job_id: str):
        try:
            return _batch_get(job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(f"{prefix}/batch_read/{{job_id}}/stop")
    async def batch_read_stop(job_id: str):
        try:
            return _batch_stop(job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(f"{prefix}/readings")
    async def readings(
        source: str = "", result: str = "", since: str = "", limit: int = 1000,
    ):
        """统一抄读数据表格（G4）：并发任务/查询快照/上报抄读合并 + 成功率统计。"""
        return _readings(source=source or None, result=result or None,
                         since=since or None, limit=limit)

    @app.get(f"{prefix}/report_buckets")
    async def report_buckets(limit: int = 500):
        """主动上报分类型计数（G6）：F1-F5 各桶 + 停复电子类单列。"""
        return _report_buckets(limit=limit)

    # ---- REQS-0013：1376.2 收发库查询（快照 + 上报事件） -------------------
    @app.get(f"{prefix}/store/snapshots")
    async def store_snapshots(afn: str = "", fn: str = "", limit: int = 20):
        """查询结果快照列表（临时层）。"""
        store = getattr(app.state, "simcon_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="收发库未启用")
        items = store.list_snapshots(afn=afn or None, fn=fn or None, limit=limit)
        return {"items": items}

    @app.get(prefix + "/store/snapshots/{snapshot_id}")
    async def store_snapshot_items(snapshot_id: int):
        """某快照的明细行。"""
        store = getattr(app.state, "simcon_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="收发库未启用")
        return {"items": store.snapshot_items(int(snapshot_id))}

    @app.get(f"{prefix}/store/events")
    async def store_events(limit: int = 50):
        """06H 主动上报事件（持久层）。"""
        store = getattr(app.state, "simcon_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="收发库未启用")
        return {"items": store.list_report_events(limit=limit)}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_simcon_app(), host="127.0.0.1", port=8781)


if __name__ == "__main__":
    main()
