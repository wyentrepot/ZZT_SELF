"""Windows 侧纯解析服务：加载 net48 GwHPLCAnalysis.dll，暴露解析端点。

- 只做裸解析（不 enrich、不采集、不碰串口）；enrich 由 WSL 侧 ParserService 完成。
- 必须在明文区（.build_plain）运行以规避 E-SafeNet 透明加密。
- DLL 缺失/加载失败时服务降级：/health 返回 dll_available=false，/api/parse 503。
"""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.dotnet_parser import DotNetHplcParser, default_dll_path
from shared.parser_service import FrameValidationError, normalize_hex_frame


class ParseRequest(BaseModel):
    hex: str


def _load_parser():
    """加载 net48 DLL；失败返回 None（服务保持可用，仅解析降级）。"""
    try:
        return DotNetHplcParser(default_dll_path())
    except Exception:
        return None


def create_app(parser=None) -> FastAPI:
    parser = _load_parser() if parser is None else parser
    lock = threading.Lock()
    app = FastAPI(title="HPLC 解析服务（Windows）", version="1.0.0")

    @app.get("/health")
    def health():
        return {
            "status": "ok" if parser is not None else "degraded",
            "dll_available": parser is not None,
        }

    @app.get("/api/version")
    def version():
        if parser is None:
            raise HTTPException(status_code=503, detail="协议解析库不可用")
        with lock:
            return parser.version()

    @app.post("/api/parse")
    def parse(request: ParseRequest):
        if parser is None:
            raise HTTPException(status_code=503, detail="协议解析库不可用")
        try:
            frame = normalize_hex_frame(request.hex)
        except FrameValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            with lock:
                simple = parser.parse_simple(frame)
                full = parser.parse_full(frame)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"DLL 解析失败：{exc}") from exc
        return {"simple": simple, "full": full}

    return app


app = create_app()
