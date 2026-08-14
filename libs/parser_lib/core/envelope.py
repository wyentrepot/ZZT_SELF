"""统一响应封装：把解析结果包成分页 Envelope（后端 → 前端）。"""
from typing import Any, Dict, List


def build_envelope(protocol: str, frames: List[Dict[str, Any]],
                   page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    page = max(1, int(page))
    total = len(frames)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "protocol": protocol,
        "total_frames": total,
        "page": page,
        "page_size": page_size,
        "frames": frames[start:end],
    }
