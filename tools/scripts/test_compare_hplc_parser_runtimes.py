import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("compare_hplc_parser_runtimes.py")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "compare_hplc_parser_runtimes", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_compare_payloads_ignores_only_version_date():
    module = _load_module()
    windows = {
        "version": {"name": "GW_SMAnalysis", "version": "V1.0.23", "date": "A"},
        "cases": {"minimal": {"simple": {"FrmType": "GW"}, "full": {"Error": None}}},
    }
    wsl = {
        "version": {"name": "GW_SMAnalysis", "version": "V1.0.23", "date": "B"},
        "cases": {"minimal": {"simple": {"FrmType": "GW"}, "full": {"Error": None}}},
    }

    assert module.compare_payloads(windows, wsl) == []


def test_compare_payloads_reports_json_semantic_difference():
    module = _load_module()
    windows = {
        "version": {"name": "GW_SMAnalysis", "version": "V1.0.23", "date": "A"},
        "cases": {"minimal": {"simple": {"FrmType": "GW"}, "full": {"Error": None}}},
    }
    wsl = {
        "version": {"name": "GW_SMAnalysis", "version": "V1.0.23", "date": "A"},
        "cases": {"minimal": {"simple": {"FrmType": "ERROR"}, "full": {"Error": None}}},
    }

    assert module.compare_payloads(windows, wsl) == [
        "cases.minimal.simple differs"
    ]
