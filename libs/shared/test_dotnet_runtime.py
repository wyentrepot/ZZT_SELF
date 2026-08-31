import sys
import types

from shared import dotnet_runtime


def test_probe_rejects_old_mono_before_pythonnet_import(monkeypatch):
    monkeypatch.setattr(
        dotnet_runtime.shutil,
        "which",
        lambda command: "/usr/bin/mono" if command == "mono" else None,
    )
    monkeypatch.setattr(dotnet_runtime, "_mono_version", lambda executable: (6, 8))
    monkeypatch.setattr(dotnet_runtime, "_netstandard_locations", lambda: ())

    result = dotnet_runtime.probe_dotnet_runtime()

    assert result.supported is False
    assert "below the Python.NET-safe minimum 6.12" in result.reason


def test_probe_accepts_a_dotnet_runtime_without_loading_pythonnet(monkeypatch):
    monkeypatch.setattr(
        dotnet_runtime.shutil,
        "which",
        lambda command: "/usr/bin/dotnet" if command == "dotnet" else None,
    )

    result = dotnet_runtime.probe_dotnet_runtime()

    assert result.supported is True
    assert result.reason == "dotnet runtime available at /usr/bin/dotnet"


def test_configure_pythonnet_uses_coreclr_on_linux_before_clr_import(monkeypatch):
    calls = []
    fake_pythonnet = types.SimpleNamespace(load=lambda runtime: calls.append(runtime))
    monkeypatch.setattr(
        dotnet_runtime,
        "probe_dotnet_runtime",
        lambda: dotnet_runtime.RuntimeProbe(True, "available"),
    )
    monkeypatch.setattr(dotnet_runtime.sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "pythonnet", fake_pythonnet)

    dotnet_runtime.configure_pythonnet_runtime()

    assert calls == ["coreclr"]


def test_configure_pythonnet_keeps_windows_default_runtime(monkeypatch):
    monkeypatch.setattr(
        dotnet_runtime,
        "probe_dotnet_runtime",
        lambda: dotnet_runtime.RuntimeProbe(True, "available"),
    )
    monkeypatch.setattr(dotnet_runtime.sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "pythonnet", raising=False)

    dotnet_runtime.configure_pythonnet_runtime()
