"""Safe preflight for the optional Python.NET/CLR integration.

The CLR bridge is a native runtime boundary. Importing pythonnet against an
unsupported Mono installation can abort the host process instead of raising a
Python exception, so callers must probe the environment before importing clr.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class RuntimeProbe:
    supported: bool
    reason: str


def _mono_version(executable: str) -> Optional[Tuple[int, int]]:
    try:
        output = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Mono JIT compiler version (\d+)\.(\d+)", output)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _netstandard_locations() -> tuple[Path, ...]:
    locations = []
    for root in (Path("/usr/lib/mono"), Path("/usr/local/lib/mono")):
        if root.is_dir():
            locations.extend(root.rglob("netstandard.dll"))
    return tuple(locations)


def probe_dotnet_runtime() -> RuntimeProbe:
    """Return whether importing pythonnet is safe in this process."""
    dotnet = shutil.which("dotnet")
    if dotnet:
        return RuntimeProbe(True, f"dotnet runtime available at {dotnet}")

    mono = shutil.which("mono")
    if not mono:
        return RuntimeProbe(
            False,
            "no dotnet runtime and no mono executable are available",
        )

    version = _mono_version(mono)
    if version is None:
        return RuntimeProbe(False, f"cannot determine Mono version from {mono}")
    if version < (6, 12):
        return RuntimeProbe(
            False,
            f"Mono {version[0]}.{version[1]} is below the Python.NET-safe minimum 6.12",
        )

    if not _netstandard_locations():
        return RuntimeProbe(
            False,
            "Mono is new enough but netstandard.dll is unavailable",
        )

    return RuntimeProbe(True, f"Mono {version[0]}.{version[1]} with netstandard.dll")


def require_dotnet_runtime() -> None:
    probe = probe_dotnet_runtime()
    if not probe.supported:
        raise RuntimeError(
            "Python.NET CLR integration is unavailable in this environment: "
            + probe.reason
        )


def configure_pythonnet_runtime() -> None:
    """Validate the CLR host and select CoreCLR for non-Windows processes.

    Python.NET must be configured before importing clr. Windows keeps its
    existing default (.NET Framework) host so the deployed net48 parser remains
    unchanged; WSL/Linux explicitly selects CoreCLR for the net8.0 parser.
    """
    require_dotnet_runtime()
    if sys.platform != "win32":
        from pythonnet import load

        load("coreclr")
