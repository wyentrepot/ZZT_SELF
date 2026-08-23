"""Test-only HPLC fixture root resolution."""
from __future__ import annotations

from pathlib import Path

import conftest


def test_hplc_test_data_root_prefers_environment(monkeypatch, tmp_path: Path):
    configured = tmp_path / "external-fixtures"
    monkeypatch.setenv("HPLC_TEST_DATA_ROOT", str(configured))

    assert conftest.hplc_test_data_root() == configured


def test_hplc_test_data_root_falls_back_to_legacy_directory(monkeypatch):
    monkeypatch.delenv("HPLC_TEST_DATA_ROOT", raising=False)

    assert conftest.hplc_test_data_root().name == "测试文件"
