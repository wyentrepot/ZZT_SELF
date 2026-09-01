"""验证 frozen（PyInstaller）与非 frozen 两种运行形态下的路径解析。

frozen 语义：
- _base_dir() == sys._MEIPASS（打包数据根：static/DLL 所在）
- _runtime_dir() == exe 所在目录 / runtime（可写持久位置）
- _default_dll() == _MEIPASS / dll/bin/Debug/GwHPLCAnalysis.dll
 非 frozen 语义与现状一致：
- _base_dir() == listener 包目录（Path(__file__).parent）
- _runtime_dir() == _base_dir() / runtime
- _default_dll() == 仓库根 / libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll
"""
import sys
from pathlib import Path

import pytest

from listener import app as app_module

HPLC_WEB_DIR = Path(app_module.__file__).resolve().parent
REPO_ROOT = HPLC_WEB_DIR.parent.parent  # apps/listener → apps → 根


@pytest.fixture
def frozen_environment(monkeypatch, tmp_path):
    """模拟 PyInstaller onedir 冻结环境：_MEIPASS 与 exe 同上级目录。"""
    dist_dir = tmp_path / "dist" / "侦听台"
    internal_dir = dist_dir / "_internal"
    internal_dir.mkdir(parents=True)
    exe = dist_dir / "侦听台.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return internal_dir, exe


def test_non_frozen_base_dir_is_package_dir():
    assert app_module._base_dir() == HPLC_WEB_DIR


def test_non_frozen_runtime_dir_is_package_runtime():
    assert app_module._runtime_dir() == HPLC_WEB_DIR / "runtime"


def test_non_frozen_default_dll_is_repo_dll():
    assert app_module._default_dll() == REPO_ROOT / "libs" / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"


def test_non_frozen_default_dll_uses_net8_artifact_on_linux(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "linux")

    assert app_module._default_dll() == (
        REPO_ROOT
        / "libs"
        / "shared"
        / "dll"
        / "bin"
        / "Debug"
        / "net8.0"
        / "GwHPLCAnalysis.dll"
    )


def test_build_parser_service_attempts_linux_initialization(monkeypatch):
    class _Service:
        pass

    service = _Service()
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    monkeypatch.setattr(app_module, "DEFAULT_DLL", Path("/repo/GwHPLCAnalysis.dll"))
    monkeypatch.setattr(app_module, "DotNetHplcParser", lambda dll_path: service)
    monkeypatch.setattr(app_module, "ParserService", lambda parser: service)

    result = app_module._build_parser_service()
    assert result is service
    assert result.parse_backend == "local"


def test_frozen_base_dir_is_meipass(frozen_environment):
    internal_dir, _ = frozen_environment
    assert app_module._base_dir() == internal_dir


def test_frozen_runtime_dir_is_next_to_exe(frozen_environment):
    _, exe = frozen_environment
    assert app_module._runtime_dir() == exe.resolve().parent / "runtime"


def test_frozen_default_dll_is_under_meipass(frozen_environment):
    internal_dir, _ = frozen_environment
    assert app_module._default_dll() == internal_dir / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"


def test_non_frozen_log_dir_is_repo_root_log(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_repo_root", lambda: tmp_path)
    assert app_module._log_dir() == tmp_path / "data" / "logs"


def test_non_frozen_log_dir_creates_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_repo_root", lambda: tmp_path)
    log_dir = app_module._log_dir()
    assert log_dir.is_dir()


def test_frozen_log_dir_is_next_to_exe(frozen_environment, monkeypatch):
    internal_dir, exe = frozen_environment
    monkeypatch.setattr(app_module, "_is_frozen", lambda: True)
    assert app_module._log_dir() == exe.resolve().parent / "data" / "logs"
