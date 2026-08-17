"""资源租约管理（docs/03 §5、§7、§9）。

- 以 (resource_type, resource_id) 为键管理租约。
- 独占（shared=False）默认：同一键只允许一个持有者。
- shared=True（离线文件只读）：同一键允许多个持有者共享；但独占请求与
  任何现存租约冲突。
- release 幂等：释放不存在/已释放的租约不抛错。
"""
from __future__ import annotations

from .models import DeviceSpec, ResourceLease


class ResourceConflictError(RuntimeError):
    """资源冲突：同一键已被独占占用。"""

    def __init__(self, resource_type: str, resource_id: str, holder: str):
        super().__init__(f"资源 {resource_id} 已被 {holder} 独占占用")
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.holder = holder


class ResourceLeaseManager:
    def __init__(self):
        # key=(resource_type, resource_id) -> 租约列表（shared 可多个，独占最多一个）
        self._leases: dict[tuple[str, str], list[ResourceLease]] = {}

    def acquire(self, spec: DeviceSpec, holder: str) -> ResourceLease:
        key = (spec.resource_type, spec.resource_id)
        existing = self._leases.get(key, [])
        if existing:
            # 已有租约：独占请求一律冲突；shared 只在全部 shared 时共存
            if not spec.shared:
                raise ResourceConflictError(spec.resource_type, spec.resource_id, existing[0].holder)
            if any(not lease.shared for lease in existing):
                raise ResourceConflictError(spec.resource_type, spec.resource_id, existing[0].holder)
        lease = ResourceLease(
            resource_type=spec.resource_type,
            resource_id=spec.resource_id,
            holder=holder,
            shared=spec.shared,
        )
        self._leases.setdefault(key, []).append(lease)
        return lease

    def release(self, resource_type: str, resource_id: str, holder: str) -> None:
        """释放指定持有者的租约；幂等。"""
        key = (resource_type, resource_id)
        leases = self._leases.get(key)
        if not leases:
            return
        kept = [lease for lease in leases if lease.holder != holder]
        if kept:
            self._leases[key] = kept
        else:
            self._leases.pop(key, None)

    def list_leases(self) -> list[ResourceLease]:
        out: list[ResourceLease] = []
        for leases in self._leases.values():
            out.extend(leases)
        return out

    def is_held(self, resource_type: str, resource_id: str) -> bool:
        return (resource_type, resource_id) in self._leases
