"""serial-tags REST API: 运行时串口角色标签的读取与保存（纯展示，不占串口）。

供"串口配置"页设置 角色↔COM 绑定（如 CCO → COM8），保存到
data/runtime/serial_tags.json。所有页面（listener/module_log/simcon）的
端口枚举已接入 SerialTagStore.merge_port_details()，会统一显示角色标签。

- GET /api/serial-tags：当前标签映射 + 角色说明 + 在线端口（供下拉选择）。
- PUT /api/serial-tags：保存标签映射（只落盘，不碰任何串口）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from shared.serial_tags import ROLES, ROLE_LABELS, SerialTagStore


class SerialTagsPayload(BaseModel):
    tags: dict[str, str]


def create_serial_tags_router(
    store: SerialTagStore | None = None,
    *,
    port_details_provider: Any = None,
) -> APIRouter:
    """创建 serial-tags 路由。

    store：标签存储；缺省用默认 runtime 目录。
    port_details_provider：可选，返回端口详情列表的可调用对象（无参），
        用于前端下拉展示当前在线串口。缺省不提供端口列表。
    """
    router = APIRouter(prefix="/api/serial-tags", tags=["serial-tags"])

    def _store() -> SerialTagStore:
        return store or SerialTagStore()

    @router.get("")
    def get_tags() -> dict[str, Any]:
        s = _store()
        payload = s.public()
        if port_details_provider is not None:
            try:
                details = port_details_provider()
                payload["port_details"] = [
                    {
                        "device": str(item.get("device") or ""),
                        "description": str(item.get("description") or ""),
                        "online": bool(item.get("online", True)),
                        "role": str(item.get("role") or ""),
                        "role_label": str(item.get("role_label") or ""),
                    }
                    for item in details
                ]
            except Exception:  # noqa: BLE001 - 端口枚举失败不阻塞标签读取
                payload["port_details"] = []
        return payload

    @router.put("")
    def put_tags(payload: SerialTagsPayload) -> dict[str, Any]:
        s = _store()
        submitted = payload.tags or {}
        cleaned = {role: str(submitted.get(role) or "") for role in ROLES}
        path = s.save(cleaned)
        return {"saved": True, "path": str(path), **s.public()}

    return router
