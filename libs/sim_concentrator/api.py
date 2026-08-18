"""AI 验证 REST API：模拟集中器验证任务的 HTTP 入口。

独立 FastAPI 子应用（create_simcon_app），可独立运行（python -m
sim_concentrator.api --port 8781）或挂载到侦听台 create_app。

接口：
- GET  /api/simcon/status           串口/模块状态
- GET  /api/simcon/responders       列出当前生效应答规则（内置+覆盖）
- GET  /api/simcon/ports            列出可用串口
- POST /api/simcon/verify           执行一个验证任务，返回结论 JSON
- POST /api/simcon/open             打开串口
- POST /api/simcon/close            关闭串口
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sim_concentrator.runner import execute_task
from sim_concentrator.responder import Responder
from sim_concentrator.serial_io import SerialIO, list_serial_ports


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------
class OpenSpec(BaseModel):
    port: str = "COM3"
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1


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
    port: str = "COM3"
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
    enable_responder: bool = True
    fail_fast: bool = True
    responders: Optional[List[Dict[str, Any]]] = None
    steps: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_simcon_app(prefix: str = "/api/simcon") -> FastAPI:
    """创建模拟集中器子应用。

    prefix 控制路由前缀：
    - 默认 "/api/simcon"：独立运行（端口 8781）或直接作为根挂载时使用，路由为 /api/simcon/*。
    - 传入 "" 时路由为相对路径（/status、/ports...），供 module_log 挂载到 /api/simcon 使用
      （app.mount("/api/simcon", create_simcon_app(prefix=""))，避免双前缀）。
    """
    app = FastAPI(title="模拟集中器验证工具", version="0.1.0")
    _holder = {"io": None, "lock": threading.Lock()}

    def _io() -> Optional[SerialIO]:
        with _holder["lock"]:
            return _holder["io"]

    def _open_io(port: str, baudrate: int, bytesize: int = 8,
                 parity: str = "N", stopbits: int = 1) -> SerialIO:
        with _holder["lock"]:
            io = _holder["io"]
            if io is None or not io.is_open():
                io = SerialIO(port=port, baudrate=baudrate,
                              bytesize=bytesize, parity=parity, stopbits=stopbits)
                io.open()
                _holder["io"] = io
            return io

    def _close_io() -> None:
        with _holder["lock"]:
            io, _holder["io"] = _holder["io"], None
        if io is not None:
            io.close()

    @app.get(f"{prefix}/status")
    async def status():
        io = _io()
        return {
            "open": io is not None and io.is_open(),
            "port": io.port if io is not None else None,
            "pending_frames": io.pending_frames() if io is not None else 0,
        }

    @app.get(f"{prefix}/ports")
    async def ports():
        return {"ports": list_serial_ports()}

    @app.get(f"{prefix}/responders")
    async def responders():
        r = Responder()
        return {"rules": r.list_rules()}

    @app.post(f"{prefix}/open")
    async def open_serial(request: OpenSpec):
        try:
            io = _open_io(request.port, request.baudrate,
                          request.bytesize, request.parity, request.stopbits)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"串口打开失败：{exc}") from exc
        return {"open": True, "port": io.port}

    @app.post(f"{prefix}/close")
    async def close_serial():
        _close_io()
        return {"open": False}

    @app.post(f"{prefix}/verify")
    async def verify(task: VerifyTask):
        """执行验证任务并返回逐步判定 + 汇总结论 JSON。"""
        # 空任务/无步骤：不碰串口，直接返回空结论
        if not task.steps:
            return {
                "task_id": task.id,
                "port": task.port,
                "baudrate": task.baudrate,
                "steps": [],
                "summary": {"total": 0, "pass": 0, "fail": 0, "verdict": "fail"},
            }
        io = _io()
        try:
            if io is None or not io.is_open():
                # 任务自带串口参数：自建并独占，执行后关闭
                from sim_concentrator.serial_io import SerialIO as _SIO
                io = _SIO(port=task.port, baudrate=task.baudrate,
                          bytesize=task.bytesize, parity=task.parity, stopbits=task.stopbits)
                io.open()
                try:
                    return execute_task(task.model_dump(), io=io)
                finally:
                    io.close()
            return execute_task(task.model_dump(), io=io)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"验证任务执行失败：{exc}") from exc

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_simcon_app(), host="127.0.0.1", port=8781)


if __name__ == "__main__":
    main()
