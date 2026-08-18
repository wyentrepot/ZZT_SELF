"""workbench.orchestration.artifacts —— Artifact 逻辑 ID 解析与下载防护（D-03）。

审计链闭环（13-设计契约偏差核查 D-03）：
- Report.artifacts 是结构化 ArtifactInfo 清单（含逻辑 ID、sha256、真实路径）。
- 下载接口只接受 manifest 登记的逻辑 Artifact ID，服务端解析真实路径。
- 路径越界防护：仅允许解析到存在的真实文件，且拒绝目录/不存在路径
  （对外暴露逻辑 ID，服务端负责解析真实路径并防止目录穿越，docs/03 §9）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


class ArtifactNotFound(Exception):
    """逻辑 Artifact ID 未在 Report manifest 中登记。"""


class ArtifactPathUnsafe(Exception):
    """Artifact 真实路径不可用（不存在/目录/非法）。"""


def find_artifact(report: Dict[str, Any], artifact_id: str) -> Optional[Dict[str, Any]]:
    """按逻辑 Artifact ID 在 Report manifest 中查找登记项。

    返回 None 表示未登记（调用方应转 404）。
    """
    for item in report.get("artifacts") or []:
        if item.get("id") == artifact_id:
            return item
    return None


def resolve_artifact_path(artifact: Dict[str, Any]) -> Path:
    """解析 Artifact 真实路径并做越界防护。

    规则：
    - 必须登记真实 path（否则拒绝）
    - 解析后的路径必须存在于磁盘
    - 必须是文件（拒绝目录）
    满足则返回绝对路径，否则抛 ArtifactPathUnsafe。
    """
    raw = artifact.get("path")
    if not raw:
        raise ArtifactPathUnsafe(f"Artifact 未登记真实路径：{artifact.get('id')}")
    path = Path(raw).resolve()
    if not path.exists():
        raise ArtifactPathUnsafe(f"Artifact 文件不存在：{path}")
    if not path.is_file():
        raise ArtifactPathUnsafe(f"Artifact 不是文件：{path}")
    return path
