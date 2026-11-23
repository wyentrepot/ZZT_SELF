"""simcon 全局设置存储（data/runtime/simcon_settings.json）。

背景（2026-11-19 现场，10H-F2 查询从节点信息）：

    请求 start=0, count=16 -> 68 12 00 43 ... 10 02 00 | 00 00 10 | 6D 16
    响应                  -> 68 12 00 83 ... 10 02 00 | 03 00 00 | A0 16   ← 总数量3、本次应答0
    请求 start=1, count=16 -> 68 12 00 43 ... 10 02 00 | 01 00 10 | 6F 16
    响应                  -> 68 2A 00 83 ... 10 02 00 | 03 00 03 | 01..10 88 00 00 00 00 00 00 18 00 00 00 00 00 88 00 10 | ED 16
                                                                         ← 总数量3、本次应答3，3 条明细齐全

两次请求的唯一差别就是「从节点起始序号」2 字节小端：00 00 → 01 00。
本场模块的档案列表是 **1 起编号**：0 号位不存在，传 0 会安静地返回「总数量对、明细 0 条」，
看起来像"查询不回"。故提供全局开关 query_start_base（0 | 1），默认 1。

谁读这个开关：
- 工作台「从模块读取档案」按钮（前端把 start_base 传进 /api/simcon/archive/query）；
- simcon recipe clear_archive 的分页起点（后端 load_settings）；
- AI 经同一 HTTP 端点或 libs 调用时与页面看到的口径一致。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_FILENAME = "simcon_settings.json"

# 从节点起始序号基址：1 = 本场模块的 1 起编号（默认）；0 = 0 起编号的模块
VALID_START_BASES = (0, 1)

DEFAULTS: dict[str, Any] = {
    "query_start_base": 1,
}

# 字段中文说明（供前端展示勾选项）
FIELD_LABELS: dict[str, str] = {
    "query_start_base": "10H-F2 从节点起始序号从 1 开始",
}


def default_runtime_dir() -> Path:
    """运行时可写目录：冻结态 exe 同级 runtime/，开发态仓库 data/runtime/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    # libs/shared/simcon_settings.py → 仓库根
    return Path(__file__).resolve().parents[2] / "data" / "runtime"


def _normalise_settings(raw: Any) -> dict[str, Any]:
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    base = raw.get("query_start_base")
    try:
        base_int = int(base)
    except (TypeError, ValueError):
        base_int = DEFAULTS["query_start_base"]
    out["query_start_base"] = base_int if base_int in VALID_START_BASES else DEFAULTS["query_start_base"]
    return out


class SimconSettingsStore:
    """全局设置的读写；load 失败静默降级为默认值（本层是增强，不阻塞业务）。"""

    def __init__(self, runtime_dir: Path | str | None = None):
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir()

    @property
    def path(self) -> Path:
        return self.runtime_dir / _FILENAME

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return dict(DEFAULTS)
        return _normalise_settings(raw)

    def save(self, settings: dict[str, Any]) -> Path:
        payload = {"version": 1, **_normalise_settings(settings)}
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".simcon_settings.", suffix=".tmp", dir=str(self.runtime_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return self.path

    # -- 便捷读取 ---------------------------------------------------------
    def query_start_base(self) -> int:
        """10H-F2 起始序号基址（0 或 1）。"""
        return int(self.load()["query_start_base"])

    def public(self) -> dict[str, Any]:
        """API 用：当前值 + 字段说明。"""
        return {
            "settings": self.load(),
            "labels": FIELD_LABELS,
            "defaults": dict(DEFAULTS),
        }


def load_settings(runtime_dir: Path | str | None = None) -> dict[str, Any]:
    return SimconSettingsStore(runtime_dir).load()


def query_start_base(runtime_dir: Path | str | None = None) -> int:
    return SimconSettingsStore(runtime_dir).query_start_base()
