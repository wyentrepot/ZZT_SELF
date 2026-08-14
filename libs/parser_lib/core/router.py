"""协议嗅探路由器：默认自动嗅探，支持手动指定（自动在前、手动在后）。"""
from typing import List, Optional

from .adapter import ProtocolAdapter


class ProtocolRouter:
    def __init__(self, adapters: List[ProtocolAdapter]):
        self.adapters = {a.protocol: a for a in adapters}

    def select(self, raw: bytes, preferred: Optional[str] = None) -> Optional[ProtocolAdapter]:
        """选择解析 raw 的适配器。preferred 不为 None 时优先（手动指定）。"""
        if preferred and preferred in self.adapters:
            return self.adapters[preferred]
        best, best_s = None, -1.0
        for a in self.adapters.values():
            try:
                s = a.confidence(raw)
            except Exception:
                s = 0.0
            if s > best_s:
                best_s, best = s, a
        return best
