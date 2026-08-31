from pathlib import Path
import sys

from shared.dotnet_runtime import configure_pythonnet_runtime


def default_dll_relative_path() -> Path:
    """Return the platform-specific parser artifact path under dll."""
    if sys.platform == "win32":
        return Path("bin") / "Debug" / "GwHPLCAnalysis.dll"
    return Path("bin") / "Debug" / "net8.0" / "GwHPLCAnalysis.dll"


def default_dll_path() -> Path:
    """默认解析 DLL：Windows 使用 net48，WSL/Linux 使用 net8.0。"""
    return Path(__file__).resolve().parent / "dll" / default_dll_relative_path()


class DotNetHplcParser:
    def __init__(self, dll_path: Path):
        resolved = Path(dll_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"找不到协议解析库：{resolved}")

        # Never import pythonnet before checking the native runtime.  An
        # unsupported Mono can abort the whole Python process during import.
        configure_pythonnet_runtime()
        import clr

        clr.AddReference(str(resolved))
        from NW import NwHPLCAnalysis

        self._parser = NwHPLCAnalysis()

    @staticmethod
    def _to_dotnet_bytes(frame: bytes):
        from System import Array, Byte

        return Array[Byte](frame)

    def parse_simple(self, frame: bytes) -> str:
        return self._parser.GetProtocolSimpleDesc(
            self._to_dotnet_bytes(frame), len(frame), None
        )

    def parse_full(self, frame: bytes) -> str:
        return self._parser.GetProtocolFullDesc(
            self._to_dotnet_bytes(frame), len(frame), None
        )

    def version(self) -> dict:
        name, version, date = self._parser.GetProtocolVersion(None, None, None)
        return {"name": name, "version": version, "date": date}

