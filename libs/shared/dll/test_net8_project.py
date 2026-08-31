"""跨平台 C# 解析库的项目契约测试。"""
from pathlib import Path
from xml.etree import ElementTree


DLL_DIR = Path(__file__).resolve().parent
NET8_PROJECT = DLL_DIR / "GwHPLCAnalysis.Net8.csproj"
JSON_COMPAT = DLL_DIR / "src" / "JsonCompat.cs"


def _project_values(project_path: Path) -> dict[str, str]:
    root = ElementTree.parse(project_path).getroot()
    return {
        child.tag.removeprefix("{").split("}")[-1]: (child.text or "").strip()
        for child in root.iter()
        if child.tag.removeprefix("{").split("}")[-1]
        in {"TargetFramework", "AssemblyName", "RootNamespace"}
    }


def test_net8_project_preserves_the_public_assembly_identity():
    """WSL 目标必须与 Windows DLL 使用同一程序集和命名空间。"""
    assert NET8_PROJECT.exists(), "缺少 WSL 的 net8.0 解析库项目"

    values = _project_values(NET8_PROJECT)
    assert values["TargetFramework"] == "net8.0"
    assert values["AssemblyName"] == "GwHPLCAnalysis"
    assert values["RootNamespace"] == "TestDll"


def test_net8_project_compiles_every_existing_parser_source_file():
    """不得维护 Linux 专属解析分支，必须复用 Windows 的同一份解析源码。"""
    assert NET8_PROJECT.exists(), "缺少 WSL 的 net8.0 解析库项目"
    project_text = NET8_PROJECT.read_text(encoding="utf-8")

    for filename in (
        "Properties/AssemblyInfo.cs",
        "src/comFunc.cs",
        "src/snifferFrame.cs",
        "src/inrfDesc.cs",
        "src/hplcFrame.cs",
        "src/intf.cs",
        "src/JsonCompat.cs",
    ):
        assert filename in project_text.replace("\\", "/")


def test_net8_json_compat_preserves_byte_array_as_numeric_json_array():
    """JavaScriptSerializer emits byte[] as numbers, not as a Base64 string."""
    source = JSON_COMPAT.read_text(encoding="utf-8")

    assert "ByteArrayAsNumberArrayConverter" in source
    assert "new ByteArrayAsNumberArrayConverter()" in source
