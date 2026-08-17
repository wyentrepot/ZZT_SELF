"""A1：用例指纹契约测试（docs/03 §4 不可变指纹）。

覆盖：与键顺序无关、内容变更指纹变化、无效样例（extra 字段）拒绝。
"""
import pytest

from test_automation.fingerprint import case_fingerprint
from test_automation.models import CasePackage


def _make_case(**overrides):
    base = {
        "case_id": "anhui_minute_collect",
        "version": "1.0.0",
        "name": "安徽分钟采集",
        "timeout_s": 30.0,
        "device": {"resource_type": "serial_port", "resource_id": "COM24", "shared": False},
        "assertions": [
            {"id": "a1", "kind": "present", "source": "frame"},
        ],
    }
    base.update(overrides)
    return CasePackage(**base)


class TestFingerprint:
    def test_fingerprint_is_sha256_hex(self):
        fp = _make_case().fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 64
        int(fp, 16)  # 全部为十六进制字符

    def test_key_order_independent(self):
        a = CasePackage(
            case_id="c1", assertions=[{"id": "a1", "kind": "present"}],
            device={"resource_type": "serial_port", "resource_id": "COM24"},
        )
        b = CasePackage(
            device={"resource_id": "COM24", "resource_type": "serial_port"},
            assertions=[{"kind": "present", "id": "a1"}],
            case_id="c1",
        )
        assert case_fingerprint(a) == case_fingerprint(b)

    def test_content_change_changes_fingerprint(self):
        base = _make_case()
        changed = _make_case(timeout_s=60.0)
        assert base.fingerprint() != changed.fingerprint()

    def test_parameter_change_changes_fingerprint(self):
        base = _make_case(parameters={"rate": 9600})
        changed = _make_case(parameters={"rate": 19200})
        assert base.fingerprint() != changed.fingerprint()

    def test_unknown_field_rejected(self):
        with pytest.raises(Exception):
            CasePackage(case_id="c1", not_a_field=1)

    def test_unsorted_assertions_changes_fingerprint(self):
        """断言顺序不同视为不同用例（指纹含顺序信息）。"""
        base = CasePackage(
            case_id="c1",
            assertions=[
                {"id": "a1", "kind": "present"},
                {"id": "a2", "kind": "absent"},
            ],
        )
        reordered = CasePackage(
            case_id="c1",
            assertions=[
                {"id": "a2", "kind": "absent"},
                {"id": "a1", "kind": "present"},
            ],
        )
        assert base.fingerprint() != reordered.fingerprint()
