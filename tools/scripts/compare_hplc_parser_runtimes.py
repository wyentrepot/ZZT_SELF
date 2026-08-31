"""Compare Windows net48 and WSL CoreCLR net8.0 parser JSON contracts.

Run this command from Windows with HPLC_TEST_DATA_ROOT pointing to the existing
read-only fixture directory. It invokes WSL for the net8.0 side and compares
decoded JSON values, so whitespace and property order cannot hide a difference.
The only allowed environment-specific value is version.date.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
for package_root in (REPO_ROOT / "apps", REPO_ROOT / "libs"):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))


def _frame_from_line(line: str) -> bytes:
    payload = line.rsplit("]", 1)[-1].strip()
    if not payload.startswith("7E "):
        raise ValueError(f"not a listener frame line: {line[:80]}")
    return bytes.fromhex(payload)


def _frame_at(log_path: Path, line_number: int) -> bytes:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return _frame_from_line(lines[line_number - 1])


def _first_matching_frame(log_path: Path, marker: str) -> bytes:
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if marker in (" " + line.upper() + " "):
            return _frame_from_line(line)
    raise ValueError(f"{log_path} does not contain frame marker {marker!r}")


def load_golden_corpus(data_root: Path) -> dict[str, bytes]:
    """Load only the fixed, documented representative frames."""
    from shared.test_fixtures import GW_FRAME_HEX

    listener_log = REPO_ROOT / "apps" / "listener" / "test_data" / "gw_log_sample.txt"
    return {
        "minimal_gw": bytes.fromhex(GW_FRAME_HEX),
        # The same bounded fixture is the existing short-physical-block
        # regression input: simple parsing succeeds and full parsing returns
        # a partial description instead of throwing.
        "short_physical_block": bytes.fromhex(GW_FRAME_HEX),
        "e4_application_payload": _first_matching_frame(
            data_root / "测试文本.txt", "11 E4"
        ),
        "central_beacon": _frame_at(listener_log, 25),
        "carrier_after_central_beacon": _frame_at(listener_log, 42),
        "concurrent_meter": _first_matching_frame(
            data_root / "并发抄表-样本.txt", "11 03 00 00"
        ),
    }


def emit_payload(data_root: Path) -> dict[str, Any]:
    from shared.dotnet_parser import DotNetHplcParser, default_dll_path

    parser = DotNetHplcParser(default_dll_path())
    cases = {}
    for name, frame in load_golden_corpus(data_root).items():
        cases[name] = {
            "simple": json.loads(parser.parse_simple(frame)),
            "full": json.loads(parser.parse_full(frame)),
        }
    return {"version": parser.version(), "cases": cases}


def _without_version_date(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(payload))
    normalized.get("version", {}).pop("date", None)
    return normalized


def compare_payloads(
    windows_payload: dict[str, Any], wsl_payload: dict[str, Any]
) -> list[str]:
    """Return every differing JSON branch after the approved date whitelist."""
    windows = _without_version_date(windows_payload)
    wsl = _without_version_date(wsl_payload)
    differences: list[str] = []

    if windows.get("version") != wsl.get("version"):
        differences.append("version differs")

    windows_cases = windows.get("cases", {})
    wsl_cases = wsl.get("cases", {})
    for case_name in sorted(set(windows_cases) | set(wsl_cases)):
        if case_name not in windows_cases or case_name not in wsl_cases:
            differences.append(f"cases.{case_name} missing")
            continue
        for result_name in ("simple", "full"):
            if windows_cases[case_name].get(result_name) != wsl_cases[case_name].get(
                result_name
            ):
                differences.append(f"cases.{case_name}.{result_name} differs")
    return differences


def _to_wsl_path(path: Path) -> str:
    completed = subprocess.run(
        ["wsl.exe", "-e", "wslpath", "-a", str(path)],
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("utf-8").strip()


def run_wsl_payload(data_root: Path) -> dict[str, Any]:
    wsl_root = _to_wsl_path(REPO_ROOT)
    wsl_data_root = _to_wsl_path(data_root)
    wsl_python = os.environ.get("HPLC_WSL_PYTHON", "python3")
    dotnet_root = os.environ.get("HPLC_WSL_DOTNET_ROOT", "/root/.dotnet")
    command = "; ".join(
        [
            f"export DOTNET_ROOT={shlex.quote(dotnet_root)}",
            f"export PATH={shlex.quote(dotnet_root)}:$PATH",
            "export PYTHONPATH="
            + shlex.quote(f"{wsl_root}:{wsl_root}/apps:{wsl_root}/libs"),
            " ".join(
                [
                    shlex.quote(wsl_python),
                    shlex.quote(
                        f"{wsl_root}/tools/scripts/compare_hplc_parser_runtimes.py"
                    ),
                    "--emit",
                    "--data-root",
                    shlex.quote(wsl_data_root),
                ]
            ),
        ]
    )
    completed = subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", command],
        check=True,
        capture_output=True,
    )
    return json.loads(completed.stdout.decode("utf-8"))


def main() -> int:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--emit", action="store_true")
    arg_parser.add_argument("--compare", action="store_true")
    arg_parser.add_argument(
        "--data-root",
        type=Path,
        default=os.environ.get("HPLC_TEST_DATA_ROOT"),
        help="read-only fixture root containing 测试文本.txt and 并发抄表-样本.txt",
    )
    args = arg_parser.parse_args()
    if args.emit == args.compare:
        arg_parser.error("choose exactly one of --emit or --compare")
    if args.data_root is None:
        arg_parser.error("--data-root or HPLC_TEST_DATA_ROOT is required")

    payload = emit_payload(args.data_root)
    if args.emit:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    differences = compare_payloads(payload, run_wsl_payload(args.data_root))
    if differences:
        print(json.dumps({"differences": differences}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "result": "equal",
                "case_count": len(payload["cases"]),
                "version_date_whitelist": ["version.date"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
