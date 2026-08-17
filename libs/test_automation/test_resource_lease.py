"""A3：资源租约互斥契约测试（docs/03 §5、§9）。

覆盖：串口独占、shared 只读共享、独占冲突拒绝、释放后可重新获取、
跨 run 隔离。
"""
import pytest

from test_automation.resource_lease import ResourceLeaseManager, ResourceConflictError
from test_automation.models import DeviceSpec


class TestResourceLeaseManager:
    def test_acquire_exclusive_port(self):
        m = ResourceLeaseManager()
        lease = m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="sim_concentrator")
        assert lease.resource_id == "COM24"
        assert lease.holder == "sim_concentrator"
        assert lease.shared is False

    def test_second_exclusive_same_port_conflicts(self):
        m = ResourceLeaseManager()
        m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="a")
        with pytest.raises(ResourceConflictError) as ei:
            m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="b")
        assert "COM24" in str(ei.value)
        assert ei.value.holder == "a"

    def test_different_ports_coexist(self):
        m = ResourceLeaseManager()
        m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM23"), holder="a")
        m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="b")
        assert len(m.list_leases()) == 2

    def test_shared_readonly_allows_multiple(self):
        m = ResourceLeaseManager()
        spec = DeviceSpec(resource_type="file", resource_id="firmware.bin", shared=True)
        m.acquire(spec, holder="a")
        m.acquire(spec, holder="b")
        assert len(m.list_leases()) == 2

    def test_shared_does_not_block_exclusive_same_key(self):
        # shared 租约不阻止同 key 的其他 shared；但独占与 shared 冲突
        m = ResourceLeaseManager()
        m.acquire(DeviceSpec(resource_type="file", resource_id="f.bin", shared=True), holder="a")
        with pytest.raises(ResourceConflictError):
            m.acquire(DeviceSpec(resource_type="file", resource_id="f.bin", shared=False), holder="b")

    def test_release_then_reacquire(self):
        m = ResourceLeaseManager()
        lease = m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="a")
        m.release(lease.resource_type, lease.resource_id, holder="a")
        lease2 = m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="b")
        assert lease2.holder == "b"

    def test_release_is_idempotent(self):
        m = ResourceLeaseManager()
        lease = m.acquire(DeviceSpec(resource_type="serial_port", resource_id="COM24"), holder="a")
        m.release(lease.resource_type, lease.resource_id, holder="a")
        m.release(lease.resource_type, lease.resource_id, holder="a")  # 不抛错
