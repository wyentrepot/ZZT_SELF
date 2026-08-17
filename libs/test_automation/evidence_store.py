"""EvidenceStore：证据追加式存储（docs/03 §3、§7、§9）。

- 追加分配 stable id（``<run_id>-ev-<seq>``）、run_id、单调递增 sequence。
- 写入立即生效；freeze() 后拒绝追加（冻结证据窗口）。
- 冻结只禁追加，不禁读取；freeze 幂等。
"""
from __future__ import annotations

from .models import Evidence


class EvidenceStore:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._items: list[Evidence] = []
        self._frozen = False

    def append(
        self,
        kind: str,
        source: str,
        payload: dict | None = None,
        raw_ref: str | None = None,
        correlation_key: str | None = None,
        metadata: dict | None = None,
    ) -> Evidence:
        """追加一条证据；冻结后抛 RuntimeError。"""
        if self._frozen:
            raise RuntimeError(f"证据窗口已冻结，拒绝追加（run_id={self.run_id}）")
        seq = len(self._items) + 1
        ev = Evidence(
            id=f"{self.run_id}-ev-{seq}",
            run_id=self.run_id,
            sequence=seq,
            kind=kind,
            source=source,
            payload=payload or {},
            raw_ref=raw_ref,
            correlation_key=correlation_key,
            metadata=metadata or {},
        )
        self._items.append(ev)
        return ev

    def freeze(self) -> None:
        """冻结证据窗口（幂等）；冻结后禁止追加。"""
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    def list(self) -> list[Evidence]:
        """按 sequence 升序返回全部证据（只读）。"""
        return list(self._items)
