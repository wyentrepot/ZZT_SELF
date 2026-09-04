"""P4 serial-profile REST API: GET/PUT save-only + POST apply.

总实施计划 P4：
- GET /api/serial-profile：读取已保存 Profile。
- PUT /api/serial-profile：只保存，不操作硬件。
- POST /api/serial-profile/apply：手动一键应用已保存版本（调 SerialProfileApplier）。
- Apply 只读取已保存 Profile，不接受临时表单配置。

P6 配置页补充（ADR-35 状态/占用）：
- GET /api/serial-profile/status：只读状态快照，四槽当前状态与占用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from shared.serial_profile import (
    PROFILE_SLOTS,
    InvalidProfileError,
    SerialProfileStore,
    UnknownMappingError,
)
from .serial_profile_applier import SerialProfileApplier


def _profile_runtime_dir() -> Path:
    """开发态 data/runtime/，冻结态 exe 同级 runtime/（与 AI storage 同约定）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    override = os.environ.get("WORKBENCH_SERIAL_PROFILE_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "runtime"


def create_serial_profile_router(
    profile_store: SerialProfileStore | None = None,
    applier: SerialProfileApplier | None = None,
    *,
    mapping_config_path: Path | str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/serial-profile", tags=["serial-profile"])

    def _store() -> SerialProfileStore:
        return profile_store or SerialProfileStore(
            runtime_dir=_profile_runtime_dir(),
            mapping_config_path=mapping_config_path,
        )

    def _applier_for(store: SerialProfileStore) -> SerialProfileApplier:
        if applier is not None:
            return applier
        raise HTTPException(
            status_code=503,
            detail="串口 Profile 应用器未配置（依赖 module/listener/simcon 服务）",
        )

    @router.get("")
    def get_profile(request: Request) -> dict[str, Any]:
        store = _store()
        try:
            profiles = store.load()
        except InvalidProfileError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"profiles": profiles, "slots": PROFILE_SLOTS}

    @router.get("/status")
    def get_status() -> dict[str, Any]:
        """只读状态快照：四槽当前状态与占用，不打开/关闭任何串口。

        P6 配置页「状态/占用」字段的数据源（ADR-35）。依赖各子应用服务，
        未配置 applier 时返回 503，而不是伪装成全空闲。
        """
        store = _store()
        applier_instance = _applier_for(store)
        return applier_instance.snapshot()

    @router.put("")
    def put_profile(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        """只保存：把提交的四槽配置写入 runtime（不操作任何串口）。"""
        submitted = payload.get("profiles") or payload
        store = _store()
        try:
            for slot in PROFILE_SLOTS:
                entry = submitted.get(slot)
                if entry is None:
                    continue
                store.update_slot(
                    slot,
                    mapping_id=entry.get("mapping_id"),
                    enabled=bool(entry.get("enabled", False)),
                    baudrate=entry.get("baudrate"),
                    parity=entry.get("parity"),
                    bytesize=entry.get("bytesize"),
                    stopbits=entry.get("stopbits"),
                )
        except (UnknownMappingError, InvalidProfileError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"saved": True, "profiles": store.load()}

    @router.post("/apply")
    def apply_profile() -> dict[str, Any]:
        """一键应用已保存版本。只读 profile，不接收表单。"""
        store = _store()
        applier_instance = _applier_for(store)
        result = applier_instance.apply()
        return result

    return router
