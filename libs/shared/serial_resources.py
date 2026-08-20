"""Thread-safe in-process ownership for physical serial resources.

This registry coordinates backend services only. UI and AI are consumers of the
same backend handle and never reserve a physical port themselves.  Different
processes remain protected by the operating system's serial-port lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable


def _normalise_alias(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("\\\\.\\"):
        text = text[4:]
    return text.replace("\\\\?\\", "").casefold()


@dataclass(frozen=True)
class SerialReservation:
    owner_id: str
    owner_label: str
    resource_id: str
    aliases: tuple[str, ...]

    def public(self) -> dict:
        return {
            "owner_id": self.owner_id,
            "owner_label": self.owner_label,
            "resource_id": self.resource_id,
            "aliases": list(self.aliases),
        }


class SerialResourceConflict(RuntimeError):
    """Raised when another backend session already owns a physical port."""

    def __init__(self, reservation: SerialReservation):
        self.owner_id = reservation.owner_id
        self.owner_label = reservation.owner_label
        self.resource_id = reservation.resource_id
        super().__init__(
            f"串口资源已被 {self.owner_label or self.owner_id} 占用"
        )


class SerialResourceRegistry:
    """Atomic reservation registry scoped to one Python process."""

    def __init__(self):
        self._lock = threading.RLock()
        self._by_key: dict[str, SerialReservation] = {}
        self._by_owner: dict[str, SerialReservation] = {}

    @staticmethod
    def _keys(resource_id: str, aliases: Iterable[str]) -> tuple[str, ...]:
        keys: set[str] = set()
        normalized_resource = _normalise_alias(resource_id)
        if normalized_resource:
            keys.add("mapping:" + normalized_resource)
        for value in aliases:
            normalized = _normalise_alias(value)
            if normalized:
                keys.add("port:" + normalized)
        if not keys:
            raise ValueError("串口资源至少需要映射 ID 或实际端口别名")
        return tuple(sorted(keys))

    def reserve(
        self,
        owner_id: str,
        *,
        label: str = "",
        resource_id: str = "",
        aliases: Iterable[str] = (),
    ) -> dict:
        owner_id = str(owner_id or "").strip()
        if not owner_id:
            raise ValueError("串口资源 owner_id 不能为空")
        keys = self._keys(resource_id, aliases)
        reservation = SerialReservation(
            owner_id=owner_id,
            owner_label=str(label or owner_id),
            resource_id=str(resource_id or ""),
            aliases=tuple(sorted({
                str(value).strip() for value in aliases if str(value).strip()
            })),
        )
        with self._lock:
            previous = self._by_owner.get(owner_id)
            if previous is not None:
                previous_keys = {
                    key for key, item in self._by_key.items() if item.owner_id == owner_id
                }
                if previous_keys == set(keys):
                    return previous.public()
                raise RuntimeError(
                    f"后端会话 {owner_id} 已持有其他串口资源，必须先释放后再切换"
                )
            for key in keys:
                existing = self._by_key.get(key)
                if existing is not None and existing.owner_id != owner_id:
                    raise SerialResourceConflict(existing)
            self._by_owner[owner_id] = reservation
            for key in keys:
                self._by_key[key] = reservation
            return reservation.public()

    def release(self, owner_id: str) -> bool:
        owner_id = str(owner_id or "").strip()
        with self._lock:
            reservation = self._by_owner.pop(owner_id, None)
            if reservation is None:
                return False
            for key, item in list(self._by_key.items()):
                if item.owner_id == owner_id:
                    self._by_key.pop(key, None)
            return True

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                item.public() for item in sorted(
                    self._by_owner.values(), key=lambda item: item.owner_id
                )
            ]
