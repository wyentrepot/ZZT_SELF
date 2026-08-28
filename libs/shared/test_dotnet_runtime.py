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
