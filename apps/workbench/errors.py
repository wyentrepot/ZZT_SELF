"""workbench.errors —— 统一错误响应（D-04，13-设计契约偏差核查）。

骨架要求（docs/03 §6）：错误响应使用可机读的
``{code, message, details, request_id}`` 结构，并禁止泄漏本机路径和句柄。

兼容策略：响应体同时保留 ``detail`` 字段（前端现有错误展示依赖它），
新增 ``code/message/details/request_id`` 统一结构，前端无需改动即可平滑迁移。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def api_error_body(
    status_code: int,
    message: str,
    details: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造统一错误响应体（code/message/details/request_id + 兼容 detail）。"""
    rid = request_id or uuid.uuid4().hex[:12]
    return {
        "code": str(status_code),
        "message": message,
        "details": details,
        "request_id": rid,
        # 兼容：前端现有错误展示读取 detail
        "detail": message,
    }


def _request_id_from(request: Request) -> str:
    """请求关联标识：优先请求头 X-Request-ID，否则新生成。"""
    rid = request.headers.get("x-request-id")
    return rid or uuid.uuid4().hex[:12]


def register_error_handlers(app: FastAPI) -> None:
    """注册统一异常处理器（HTTPException / 校验错误 / 未捕获异常）。"""

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException):
        details = exc.detail
        if isinstance(details, dict):
            # 已是结构化 detail（dict）时透传，避免丢失内部结构
            body = dict(details)
            body.setdefault("code", str(exc.status_code))
            body.setdefault("message", str(details.get("detail") or details.get("message") or exc.status_code))
            body.setdefault("request_id", _request_id_from(request))
            body.setdefault("detail", body["message"])
            return JSONResponse(status_code=exc.status_code, content=body)
        return JSONResponse(
            status_code=exc.status_code,
            content=api_error_body(
                exc.status_code, str(details), request_id=_request_id_from(request)
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError):
        # 校验错误：422，details 携带字段级错误（不泄漏内部对象）
        return JSONResponse(
            status_code=422,
            content=api_error_body(
                422,
                "请求参数校验失败",
                details=[{"loc": list(e.get("loc", [])), "msg": str(e.get("msg", ""))} for e in exc.errors()],
                request_id=_request_id_from(request),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc_handler(request: Request, exc: Exception):
        # 未捕获异常：500，message 不泄漏内部异常详情（防本机路径/句柄泄漏）
        return JSONResponse(
            status_code=500,
            content=api_error_body(
                500,
                "服务器内部错误",
                request_id=_request_id_from(request),
            ),
        )
