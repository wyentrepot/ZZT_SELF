"""REQS-0034 BR-1：run.py 启动前置检查横幅（工作台使用优化）。

横幅是纯 print 的启动提示（不动启动逻辑），通过 import 冒烟 + capsys 断言三行内容。
"""
from __future__ import annotations

import workbench.run as workbench_run


def test_preflight_banner_lists_env_gates_and_health(monkeypatch, capsys):
    monkeypatch.delenv("WORKBENCH_LOCAL_FULL_ACCESS", raising=False)
    monkeypatch.setenv("HPLC_OPEN_WORKBENCH", "1")

    workbench_run._print_startup_preflight()

    out = capsys.readouterr().out
    assert "WORKBENCH_LOCAL_FULL_ACCESS" in out
    assert "HPLC_OPEN_WORKBENCH" in out
    assert f"http://127.0.0.1:{workbench_run.PORT}/api/health" in out


def test_preflight_banner_hints_when_full_access_missing(monkeypatch, capsys):
    monkeypatch.delenv("WORKBENCH_LOCAL_FULL_ACCESS", raising=False)

    workbench_run._print_startup_preflight()

    out = capsys.readouterr().out
    assert "=1" in out and "免 token" in out  # 缺省时给出可设值提示（v2 免 token）
