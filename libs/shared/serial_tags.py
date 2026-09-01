"""运行时串口角色标签存储（P6b：界面可保存的角色↔COM 绑定，纯展示不独占）。

背景：config/serial_ports.json 只保留只读物理映射，且当前已清空（解除强绑定）。
本模块提供**运行时可编辑**的角色标签映射，供所有页面统一显示，例如：
    用户把 CCO 绑到 COM8 → 所有端口列表显示 “COM8 (CCO 日志口)”。
它不改变串口打开行为——任何模块仍可自由打开任何 COM（与物理独占解耦）。

角色固定四类：
    listener  侦听台
    cco       CCO 日志口
    sta       STA 日志口
    simcon    模拟集中器

存储路径：
    开发态  <仓库根>/data/runtime/serial_tags.json
    冻结态  exe 同级 runtime/serial_tags.json
    无文件/损坏 → 空标签，不阻塞串口枚举（本层永远是增强，不是前置依赖）。

文件格式（JSON）：
    { "version": 1, "tags": { "listener": "COM4", "cco": "COM8", "sta": "", "simcon": "" } }
    value 为空串表示该角色未绑定。

merge_port_details() 约定：
    输入为 SerialPortCatalog.merge_system_ports() 的输出（每条含 device/windows_com/
    linux_device/label/usage 等）。本函数按 COM 匹配，给命中的记录补：
        role        : 角色 id（如 "cco"）
        role_label  : 角色中文标签（如 "CCO 日志口"）
    未命中记录原样保留，不删除、不排序、不阻塞。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

# 角色 id -> 中文标签（与 serial_profile 的槽位语义一致）
ROLE_LABELS: dict[str, str] = {
    "listener": "侦听台",
    "cco": "CCO 日志口",
    "sta": "STA 日志口",
    "simcon": "模拟集中器",
}
ROLES = tuple(ROLE_LABELS)

_TAG_FILENAME = "serial_tags.json"


def default_runtime_dir() -> Path:
    """返回运行时可写目录：冻结态 exe 同级 runtime/，开发态仓库 data/runtime/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    # libs/shared/serial_tags.py → 仓库根
    return Path(__file__).resolve().parents[2] / "data" / "runtime"


def _normalise(value: str) -> str:
    return str(value or "").strip().upper()


def _normalise_role(role: str) -> str:
    return str(role or "").strip().lower()


class SerialTagStore:
    """角色↔COM 标签的读写存储；load 失败静默降级为空标签。"""

    def __init__(self, runtime_dir: Path | str | None = None):
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir()

    @property
    def path(self) -> Path:
        return self.runtime_dir / _TAG_FILENAME

    # -- 读写 -------------------------------------------------------------
    def load(self) -> dict[str, str]:
        """读取标签映射；无文件/损坏返回全空。返回 {role: COM}（COM 大写）。"""
        empty = {role: "" for role in ROLES}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return empty
        if not isinstance(raw, dict):
            return empty
        tags = raw.get("tags")
        if not isinstance(tags, dict):
            return empty
        result = dict(empty)
        for role, com in tags.items():
            r = _normalise_role(role)
            if r in result:
                result[r] = _normalise(com)
        return result

    def save(self, tags: dict[str, str]) -> Path:
        """保存标签映射；自动过滤非法角色与非法 COM 值。原子写。"""
        cleaned: dict[str, str] = {}
        for role in ROLES:
            cleaned[role] = _normalise(tags.get(role, ""))
        payload = {"version": 1, "tags": cleaned}
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".serial_tags.", suffix=".tmp", dir=str(self.runtime_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return self.path

    # -- 查询 -------------------------------------------------------------
    def com_for(self, role: str) -> str:
        """返回角色绑定的 COM（未绑定/非法返回空串）。"""
        return self.load().get(_normalise_role(role), "")

    def role_for_device(self, device: str) -> str:
        """给定设备名，返回命中角色 id（无命中返回空串）。"""
        wanted = _normalise(device)
        if not wanted:
            return ""
        for role, com in self.load().items():
            if com and com == wanted:
                return role
        return ""

    def label_for_device(self, device: str) -> str:
        """给定设备名，返回角色中文标签（无命中返回空串）。"""
        role = self.role_for_device(device)
        return ROLE_LABELS.get(role, "") if role else ""

    def public(self) -> dict[str, Any]:
        """API 用：标签映射 + 角色说明。"""
        return {
            "tags": self.load(),
            "roles": {role: ROLE_LABELS[role] for role in ROLES},
            "path": str(self.path),
        }

    # -- 合并进端口枚举 ------------------------------------------------
    def merge_port_details(self, details: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """把运行时角色标签合并进 port_details 列表（纯展示增强）。

        输入来自 SerialPortCatalog.merge_system_ports() 或兼容结构：
            每条至少含 device，可选 windows_com / linux_device。
        命中规则：device 或 windows_com（平台相关字段）任一等于某角色绑定
        的 COM 即打上 role / role_label；未命中原样保留。
        """
        tags = self.load()
        by_com: dict[str, str] = {}
        for role, com in tags.items():
            if com:
                by_com[com] = role
        if not by_com:
            return [dict(item) for item in details]

        merged: list[dict[str, Any]] = []
        for item in details:
            record = dict(item)
            device = _normalise(str(item.get("device") or ""))
            windows_com = _normalise(str(item.get("windows_com") or ""))
            role = by_com.get(device) or by_com.get(windows_com) or ""
            if role:
                record["role"] = role
                record["role_label"] = ROLE_LABELS.get(role, "")
            merged.append(record)
        return merged
