"""REQS-0032 T4：CLI（parser_lib.cli 与 tools/scripts/appframe.py 启动器）单测。

契约：JSON 打到 stdout；exit 0 = ok 且 verified（verify 时），1 = 业务失败，2 = 用法错误。
"""
import json
import subprocess
import sys
from pathlib import Path

from parser_lib import cli

REPO = Path(__file__).resolve().parents[2]
F645 = "6812345678901268910833333433AB896745CC16"
F645_BAD_CS = "6812345678901268910833333433AB8967451B16"  # CS 改错（结构成立）


def test_cli_parse_ok(capsys):
    rc = cli.main(["parse", "--hex", F645])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["protocol"] == "645"


def test_cli_parse_unrecognized_exit_1(capsys):
    rc = cli.main(["parse", "--hex", F645[:-4] + "0000"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and out["error"] == "unrecognized"


def test_cli_parse_specified_bad_frame_ok_with_warnings(capsys):
    rc = cli.main(["parse", "--hex", F645_BAD_CS, "--protocol", "645"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert any("CS校验失败" in w for w in out["warnings"])


def test_cli_build_645(capsys):
    rc = cli.main(["build", "--protocol", "645",
                   "--params", '{"addr":"123456789012","control":"11","di":"00010000"}'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["frame_hex"].startswith("68")


def test_cli_build_bad_json_exit_1(capsys):
    rc = cli.main(["build", "--protocol", "645", "--params", "{bad json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and out["error"] == "invalid_json"


def test_cli_verify_expect_mismatch_exit_1(capsys):
    rc = cli.main(["verify", "--hex", F645,
                   "--expect", '{"地址域": "FFFFFFFFFFFF"}'])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is True and out["verified"] is False


def test_cli_usage_error_exit_2(capsys):
    rc = cli.main(["frobnicate"])
    assert rc == 2


def test_appframe_launcher_smoke():
    """免配置入口：tools/scripts/appframe.py 自插 sys.path 后行为与 cli 一致。"""
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools" / "scripts" / "appframe.py"),
         "parse", "--hex", F645],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and out["protocol"] == "645"
