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


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_simcon_app(prefix: str = "/api/simcon", resource_registry: SerialResourceRegistry | None = None) -> FastAPI:
    """创建模拟集中器子应用。

    prefix 控制路由前缀：
    - 默认 "/api/simcon"：独立运行（端口 8781）或直接作为根挂载时使用，路由为 /api/simcon/*。
    - 传入 "" 时路由为相对路径（/status、/ports...），供 module_log 挂载到 /api/simcon 使用
      （app.mount("/api/simcon", create_simcon_app(prefix=""))，避免双前缀）。
    """
    app = FastAPI(title="模拟集中器验证工具", version="0.1.0")
    catalog = SerialPortCatalog.load()
    app.state.serial_port_catalog = catalog
    app.state.serial_resource_registry = resource_registry or SerialResourceRegistry()
    _holder = {"io": None, "lock": threading.Lock()}

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
        # 模拟集中器只展示自己的映射；未映射的实际串口保留作兼容手动选择。
        details = [
            detail for detail in list_serial_port_details(catalog)
            if detail.get("usage") in ("", "simcon")
        ]
        # 让既有 UI 在首次加载时优先选中维护的 simcon 默认映射。
        return sorted(details, key=lambda detail: (
            0 if detail.get("mapping_id") == "simcon" else 1,
            str(detail.get("device", "")),
        ))

    def _io() -> Optional[SerialIO]:
        with _holder["lock"]:
            return _holder["io"]

    def _open_io(resolved: dict[str, Any]) -> SerialIO:
        with _holder["lock"]:
            io = _holder["io"]
            if io is None or not io.is_open():
                io = SerialIO(
                    port=resolved["port"],
                    baudrate=resolved["baudrate"],
                    bytesize=resolved["bytesize"],
                    parity=resolved["parity"],
                    stopbits=resolved["stopbits"],
                    port_identity=resolved["port_identity"],
                    resource_registry=app.state.serial_resource_registry,
                )
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
            "port_identity": io.port_identity if io is not None else None,
            "mapping_error": catalog.mapping_error,
            "pending_frames": io.pending_frames() if io is not None else 0,
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
        return {
            "open": True,
            "port": io.port,
            "mapping_id": resolved["mapping_id"],
            "port_identity": io.port_identity,
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
            resolved = _resolve(
                task.port, task.mapping_id, task.baudrate,
                task.bytesize, task.parity, task.stopbits,
            )
            # 空任务/无步骤：不碰串口，但仍返回可审计的映射解析结果。
            if not task.steps:
                return {
                    "task_id": task.id,
                    "port": resolved["port"],
                    "baudrate": resolved["baudrate"],
                    "mapping_id": resolved["mapping_id"],
                    "port_identity": resolved["port_identity"],
                    "steps": [],
                    "summary": {"total": 0, "pass": 0, "fail": 0, "verdict": "fail"},
                }
            task_payload = task.model_dump()
            task_payload.update(resolved)
            io = _io()
            if io is None or not io.is_open():
                # 任务自带串口参数：自建并独占，执行后关闭。
                io = SerialIO(
                    port=resolved["port"],
                    baudrate=resolved["baudrate"],
                    bytesize=resolved["bytesize"],
                    parity=resolved["parity"],
                    stopbits=resolved["stopbits"],
                    port_identity=resolved["port_identity"],
                    resource_registry=app.state.serial_resource_registry,
                )
                io.open()
                try:
                    return execute_task(task_payload, io=io)
                finally:
                    io.close()
            return execute_task(task_payload, io=io)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"验证任务执行失败：{exc}") from exc

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_simcon_app(), host="127.0.0.1", port=8781)


if __name__ == "__main__":
    main()
