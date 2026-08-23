"""PyInstaller resource-contract tests for the workbench serial-session release."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "tools" / "packaging"
RUNTIME_HOOK = PACKAGING / "runtime_hooks" / "ensure_serial_ports_config.py"

SPECS = {
    "workbench.spec": {
        "shared.serial_mapping",
        "listener.index_registry",
        "workbench.ai_api",
        "workbench.ai_auth",
        "workbench.ai_operations",
        "workbench.ai_store",
        "regex",
    },
    "module_log.spec": {"shared.serial_mapping"},
    "hplc_parser.spec": {
        "shared.serial_mapping",
        "listener.serial_service",
        "listener.index_registry",
        "serial",
        "serial.tools.list_ports",
    },
    "hplc_parser_desktop.spec": {
        "shared.serial_mapping",
        "listener.serial_service",
        "listener.index_registry",
        "serial",
        "serial.tools.list_ports",
    },
}


@pytest.mark.parametrize(("spec_name", "required_imports"), SPECS.items())
def test_serial_session_resources_are_declared_for_every_serial_executable(
    spec_name: str, required_imports: set[str],
) -> None:
    text = (PACKAGING / spec_name).read_text(encoding="utf-8")

    assert 'str(ROOT / "config" / "serial_ports.json")' in text
    assert '"config"' in text
    assert 'ensure_serial_ports_config.py' in text
    for module_name in required_imports:
        assert f'"{module_name}"' in text


def test_frozen_runtime_hook_seeds_editable_mapping_without_overwriting_operator_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled = tmp_path / "_internal" / "config" / "serial_ports.json"
    bundled.parent.mkdir(parents=True)
    bundled.write_text('{"version": 1, "ports": []}\n', encoding="utf-8")
    executable = tmp_path / "工作台.exe"
    executable.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    runpy.run_path(str(RUNTIME_HOOK))

    external = tmp_path / "config" / "serial_ports.json"
    assert external.read_text(encoding="utf-8") == bundled.read_text(encoding="utf-8")

    external.write_text('{"version": 1, "ports": [{"id": "operator"}]}\n', encoding="utf-8")
    runpy.run_path(str(RUNTIME_HOOK))
    assert '"operator"' in external.read_text(encoding="utf-8")


def test_build_menu_exposes_workbench_package() -> None:
    text = (PACKAGING / "build_exe.bat").read_text(encoding="utf-8", errors="replace")

    assert 'tools\\packaging\\workbench.spec' in text
    assert ':seed_serial_config' in text
    assert 'copy /y "config\\serial_ports.json"' in text


@pytest.mark.parametrize(
    ("spec_name", "source_expression", "static_dir", "required_files"),
    [
        (
            "workbench.spec",
            'str(ROOT / "apps" / "workbench" / "static")',
            ROOT / "apps" / "workbench" / "static",
            {
                "index.html",
                "app.js",
                "pages/listener/index.html",
                "pages/listener/app.js",
                "pages/module-serial/module-serial.html",
                "pages/module-serial/module-serial.js",
            },
        ),
        (
            "module_log.spec",
            'str(ROOT / "apps" / "module_log" / "static")',
            ROOT / "apps" / "module_log" / "static",
            {"module-serial.html", "module-serial.js", "styles.css"},
        ),
        (
            "hplc_parser.spec",
            'str(ROOT / "apps" / "listener" / "static")',
            ROOT / "apps" / "listener" / "static",
            {"index.html", "app.js", "styles.css"},
        ),
        (
            "hplc_parser_desktop.spec",
            'str(ROOT / "apps" / "listener" / "static")',
            ROOT / "apps" / "listener" / "static",
            {"index.html", "app.js", "styles.css"},
        ),
    ],
)
def test_dynamic_static_pages_are_collected_by_their_release_spec(
    spec_name: str, source_expression: str, static_dir: Path, required_files: set[str],
) -> None:
    spec_text = (PACKAGING / spec_name).read_text(encoding="utf-8")
    assert source_expression in spec_text
    for relative_name in required_files:
        assert (static_dir / relative_name).is_file()


def test_build_dependency_bootstrap_includes_listener_and_module_requirements() -> None:
    text = (PACKAGING / "build_exe.bat").read_text(encoding="utf-8", errors="replace")

    assert 'apps\\listener\\requirements.txt' in text
    assert 'apps\\module_log\\requirements.txt' in text


def test_module_observation_regex_runtime_dependency_is_packaged() -> None:
    requirements = (ROOT / "apps" / "module_log" / "requirements.txt").read_text(encoding="utf-8")

    assert "regex" in requirements

def test_dependency_cache_is_bumped_for_new_listener_runtime_dependencies() -> None:
    text = (PACKAGING / "build_exe.bat").read_text(encoding="utf-8", errors="replace")

    assert '.venv\\.deps_build_v3' in text

def test_build_seed_does_not_overwrite_an_existing_operator_mapping() -> None:
    text = (PACKAGING / "build_exe.bat").read_text(encoding="utf-8", errors="replace")

    assert 'if not exist "%%~fD\\config\\serial_ports.json" copy /y' in text
