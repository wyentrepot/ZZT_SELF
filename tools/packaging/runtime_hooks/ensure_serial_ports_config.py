"""Seed the editable serial-port mapping beside a frozen executable.

PyInstaller data files live below ``sys._MEIPASS`` and may be read-only.  The
application intentionally reads ``<exe>/config/serial_ports.json`` so field
engineers can update port mappings without rebuilding the executable.  This
hook copies the packaged default only on first launch and never overwrites an
operator-maintained file.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def install_default_serial_ports_config() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", ""))
    source = bundle_root / "config" / "serial_ports.json"
    target = Path(sys.executable).resolve().parent / "config" / "serial_ports.json"
    if not source.is_file() or target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError:
        # Startup must remain usable even on a read-only installation path.
        # The catalog reports a clear mapping error and can still enumerate ports.
        return


install_default_serial_ports_config()
