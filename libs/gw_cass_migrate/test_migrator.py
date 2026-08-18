"""gw_cass_migrate 契约测试（任务3：GW-CASS 用例迁移）。"""
import json

import pytest

from gw_cass_migrate.migrator import (
    migrate_case,
    _detect_afn_group,
    _needs_hardware,
    _extract_assertions,
)

SAMPLE_F1 = {
    "标题": "AFN=00H-F1确认",
    "步骤": [
        {"操作": "1.模拟集中器发送AFN=12H-F2路由暂停", "预期结果": "1.CCO端正确应答00H-F1确认帧"},
    ],
    "合并行": 1,
}

SAMPLE_F2 = {
    "标题": "AFN=00H-F2否认",
    "步骤": [
        {"操作": "1.模拟集中器发送AFN=03H-F18", "预期结果": "1.CCO端应答否认帧，返回错误原因=0A"},
    ],
}

SAMPLE_QUERY = {
    "标题": "AFN=03H-F1厂商代码和版本信息",
    "步骤": [
        {"操作": "1.模拟集中器发送AFN=03H-F1", "预期结果": "1.CCO端正确返回03H-F1厂商代码"},
    ],
}

SAMPLE_HW = {
    "标题": "停复电-645继电器上下电主动上报",
    "步骤": [
        {"操作": "1.经继电器串口发送断电", "预期结果": "1.模拟表端失电"},
    ],
}


class TestDetectAfnGroup:
    def test_afn_group_extracted(self):
        assert _detect_afn_group("AFN=00H-F1确认") == "00H"
        assert _detect_afn_group("AFN = 05H-F101设置中心节点时间") == "05H"
        assert _detect_afn_group("AFN=12H-F3恢复路由") == "12H"

    def test_no_afn_returns_scene(self):
        assert _detect_afn_group("上电识别") == "场景"


class TestNeedsHardware:
    def test_hardware_detected(self):
        assert _needs_hardware(SAMPLE_HW["标题"], SAMPLE_HW["步骤"]) is True

    def test_pure_frame_not_hardware(self):
        assert _needs_hardware(SAMPLE_F1["标题"], SAMPLE_F1["步骤"]) is False


class TestExtractAssertions:
    def test_confirm_frame_assertion(self):
        assertions = _extract_assertions(SAMPLE_F1["步骤"][0], 1)
        assert len(assertions) >= 1
        assert assertions[0].kind == "present"
        assert assertions[0].expected == "00H-F1"

    def test_deny_frame_assertion(self):
        assertions = _extract_assertions(SAMPLE_F2["步骤"][0], 1)
        assert len(assertions) >= 1
        assert assertions[0].expected == "00H-F2"

    def test_query_return_afn_assertion(self):
        assertions = _extract_assertions(SAMPLE_QUERY["步骤"][0], 1)
        assert len(assertions) >= 1
        assert assertions[0].expected == "03H-F1"


class TestMigrateCase:
    def test_basic_fields(self):
        case = migrate_case(SAMPLE_F1, 1)
        assert case.case_id == "gw_cass_01"
        assert case.name == "AFN=00H-F1确认"
        assert "GW-CASS" in case.description
        assert case.parameters["source"] == "gw_cass"
        assert case.parameters["index"] == 1
        assert case.parameters["afn_group"] == "00H"
        assert case.parameters["needs_hardware"] is False
        assert len(case.parameters["steps"]) == 1
        assert case.device is not None

    def test_serializable(self):
        case = migrate_case(SAMPLE_F1, 1)
        data = json.loads(case.model_dump_json())
        assert data["case_id"] == "gw_cass_01"
        assert "fingerprint"  # fingerprint 方法可调用
        case.fingerprint()

    def test_hardware_flag(self):
        case = migrate_case(SAMPLE_HW, 70)
        assert case.parameters["needs_hardware"] is True
