from __future__ import annotations

import pytest


def test_mapping_aliases_are_exclusive_across_backend_services():
    from shared.serial_resources import SerialResourceConflict, SerialResourceRegistry

    registry = SerialResourceRegistry()
    first = registry.reserve(
        "module:ms-cco",
        label="模块日志会话 CCO",
        resource_id="cco-main",
        aliases=("COM8", "/dev/ttyACM0"),
    )
    assert first["resource_id"] == "cco-main"

    with pytest.raises(SerialResourceConflict) as error:
        registry.reserve(
            "listener:listener-main",
            label="侦听台",
            resource_id="cco-main",
            aliases=("COM8", "/dev/ttyACM0"),
        )

    assert error.value.owner_id == "module:ms-cco"
    assert error.value.owner_label == "模块日志会话 CCO"
    assert registry.release("module:ms-cco") is True

    second = registry.reserve(
        "listener:listener-main",
        label="侦听台",
        resource_id="cco-main",
        aliases=("/dev/ttyACM0", "COM8"),
    )
    assert second["owner_id"] == "listener:listener-main"


def test_unmapped_ports_are_still_normalized_and_releasable():
    from shared.serial_resources import SerialResourceConflict, SerialResourceRegistry

    registry = SerialResourceRegistry()
    registry.reserve("module:one", label="模块会话", aliases=(r"\\.\COM77",))
    with pytest.raises(SerialResourceConflict):
        registry.reserve("module:two", label="另一会话", aliases=("com77",))
    assert registry.release("module:one") is True
    registry.reserve("module:two", label="另一会话", aliases=("COM77",))
    assert registry.snapshot()[0]["owner_id"] == "module:two"
