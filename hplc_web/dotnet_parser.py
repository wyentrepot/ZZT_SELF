from pathlib import Path


class DotNetHplcParser:
    def __init__(self, dll_path: Path):
        resolved = Path(dll_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"找不到协议解析库：{resolved}")

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

