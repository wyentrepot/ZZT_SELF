"""侦听台独立应用（端口 8765）。

与模块日志（module_log，8766）完全解耦：独立 FastAPI 应用、独立端口、
独立串口服务。复用 shared/ 下的解析链路（dotnet_parser / parser_service /
application_service）与共享基础设施（shared.infra）。

启动：python -m listener.run
"""
import base64
import binascii
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared import infra
from shared import remote_parser
from shared.dotnet_parser import DotNetHplcParser
from shared.dotnet_parser import default_dll_relative_path
from shared.parser_service import FrameValidationError, ParserService
from listener.index_registry import ListenerIndexRegistry
from listener.log_service import LogFileService
from listener.serial_service import SerialCaptureService
from listener.nwk_service import NwkService
from listener.trace_service import FeatureError, TraceService


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _base_dir() -> Path:
    """打包数据根：frozen 下为 PyInstaller _MEIPASS，否则为 listener 包目录。"""
    if _is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _runtime_dir() -> Path:
    """运行时数据目录：frozen 下为 exe 同目录 runtime/，否则为包内 runtime/。"""
    if _is_frozen():
        return Path(sys.executable).resolve().parent / "runtime"
    return _base_dir() / "runtime"


def _log_dir() -> Path:
    """项目根 data/logs/ 目录：串口采集数据落盘位置。"""
    if _is_frozen():
        root = Path(sys.executable).resolve().parent
    else:
        root = _repo_root()
    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _repo_root() -> Path:
    """仓库根：apps/listener → apps → 根。"""
    return _base_dir().parent.parent


def _default_dll() -> Path:
    """解析 DLL 默认路径：Windows 为 net48，WSL/Linux 为 net8.0。"""
    if _is_frozen():
        return _base_dir() / "dll" / default_dll_relative_path()
    return (_repo_root() / "libs" / "shared" / "dll" / default_dll_relative_path()).resolve()


BASE_DIR = _base_dir()
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DLL = _default_dll()
DEFAULT_SERIAL_PORT = "COM19"


def _build_parser_service():
    """解析门面三档降级：本地 net8.0 DLL → 远程 Windows 服务 → None。

    - 档 1：本地 net8.0 DLL 存在且运行时可加载 → ``parse_backend=local``。
    - 档 2：无本地 DLL，但配置了 ``HPLC_REMOTE_PARSE_URL`` /
      ``config/remote_parse.json`` 且远程服务可达（``/api/version`` 探测成功，
      表示服务在线且 DLL 已加载）→ ``parse_backend=remote``。
    - 档 3：均不可用 → None（采集/日志/证据照常，深度解析 503）。

    降级后仅 DLL 相关路由（/api/parse、/api/version 的解析部分）不可用，
    串口实时采集与日志索引功能照常工作（帧仍入库，只是不做深度解析）。
    """
    try:
        service = ParserService(DotNetHplcParser(DEFAULT_DLL))
        service.parse_backend = "local"
        return service
    except Exception:
        pass

    remote_url = remote_parser.resolve_remote_parse_url()
    if remote_url:
        remote = remote_parser.RemoteHplcParser(remote_url)
        try:
            remote.version()  # 探测：服务可达 + DLL 已加载
            service = ParserService(remote)
            service.parse_backend = "remote"
            service._remote_parser = remote  # 持有引用，防止被回收
            return service
        except Exception:
            remote.close()
    return None


def _list_serial_devices():
    """枚举本机串口设备名（优先真实存在的设备，WSL 下为 /dev/ttyUSB*）。"""
    try:
        from serial.tools import list_ports

        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


def _default_serial_port():
    """默认串口：优先取第一个存在的 USB 串口，否则回退常量。"""
    devices = _list_serial_devices()
    if devices:
        for dev in devices:
            if "USB" in dev or dev.startswith("/dev/ttyUSB") or dev.startswith("/dev/ttyACM"):
                return dev
        return devices[0]
    return DEFAULT_SERIAL_PORT
DEFAULT_INDEX = _runtime_dir() / "log_index.sqlite3"
DEFAULT_INDEXES_DIR = _runtime_dir() / "indexes"
LAST_PATH_FILE = _runtime_dir() / "last_path.txt"


# ---- 与 shared.infra 的模块级兼容封装（测试 mock 这些符号；路由调用它们）----
def _windows_drives():
    return infra.windows_drives()


def _list_directory(path_text: str):
    return infra.list_directory(path_text)


def _read_last_path() -> str:
    return infra.read_last_path(LAST_PATH_FILE)


def _pick_file_via_tkinter_dialog(initial_dir: str = "", timeout_s: int = 60) -> str:
    return infra.pick_file_via_tkinter_dialog(initial_dir, timeout_s=timeout_s)


# 以下 _POWERSHELL_PICK_FILE_SCRIPT 与 _pick_file_via_native_dialog 与
# shared/infra.py 存在重复实现。保留原因为测试（test_native_picker_*）
# 通过 inspect.getsource 断言源码结构，封装到 infra 后 getsource 不可见。
# 路由 _fs_pick 实际调用的是 _pick_file_via_tkinter_dialog（封装 infra），
# 不经过此函数；仅测试引用。
_POWERSHELL_PICK_FILE_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '选择日志文件'
$dialog.Filter = '日志文件|*.txt;*.log;*.dat;*.csv;*.bin;*.raw|所有文件|*.*'
$initial = $env:HPLC_PICKER_INITIAL_DIR
if ($initial -and (Test-Path -LiteralPath $initial -PathType Leaf)) {
    $initial = Split-Path -LiteralPath $initial -Parent
}
if ($initial -and (Test-Path -LiteralPath $initial -PathType Container)) {
    $dialog.InitialDirectory = $initial
}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($dialog.FileName)
    [Console]::Out.Write([Convert]::ToBase64String($bytes))
}
"""


def _pick_file_via_native_dialog(initial_dir: str = "") -> str:
    """在本机桌面会话中打开 Windows 文件管理器选文件，并返回完整路径。"""
    environment = os.environ.copy()
    environment["HPLC_PICKER_INITIAL_DIR"] = initial_dir
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command",
             _POWERSHELL_PICK_FILE_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"无法启动 Windows 文件选择器：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "PowerShell 未返回错误详情"
        raise RuntimeError(f"Windows 文件选择器启动失败：{detail}")
    encoded_path = completed.stdout.strip()
    if not encoded_path:
        return ""
    try:
        return base64.b64decode(encoded_path, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise RuntimeError("Windows 文件选择器返回了无效路径") from exc


class ParseRequest(BaseModel):
    hex: str


class OpenLogRequest(BaseModel):
    path: str = Field(..., max_length=1024)


def create_app(service: ParserService, log_service=None, serial_service=None) -> FastAPI:
    app = FastAPI(title="国网 HPLC 日志解析工具 - 侦听台")
    # 统一工作台的 AI 控制面直接复用这些后端服务；不通过 HTTP 重开串口。
    app.state.serial_service = serial_service
    app.state.log_service = log_service
    app.state.parser_service = service
    # 通信流追踪（需求 0009）：与 log_service 同生命周期；live 句柄注册表在内部
    trace_service = TraceService(log_service) if log_service is not None else None
    app.state.trace_service = trace_service
    # 组网观测（REQS-0024）：基于 adapter_dualmac 的组网事件流/网络总览
    nwk_service = NwkService(log_service) if log_service is not None else None
    app.state.nwk_service = nwk_service
    if serial_service is not None and trace_service is not None:
        serial_service.on_frames_appended = trace_service.on_frames_appended
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/version")
    def version():
        base = {
            "picker_api_revision": 2,
            "minute_analysis_api_revision": 3,
            "frame_filter_api_revision": 2,
            "serial_api_revision": 1,
        }
        if service is None:
            base["dll_available"] = False
            base["parse_backend"] = "none"
            base["name"] = "侦听台"
            base["version"] = "0.1.0"
            base["date"] = ""
            return base
        base["dll_available"] = True
        base["parse_backend"] = getattr(service, "parse_backend", "local")
        base.update(service.version())
        return base

    @app.post("/api/parse")
    def parse(request: ParseRequest):
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="协议解析库不可用（当前环境未提供 GwHPLCAnalysis.dll 且未配置远程解析服务，串口采集不受影响）",
            )
        try:
            return service.parse(request.hex)
        except FrameValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except remote_parser.RemoteParseError as exc:
            raise HTTPException(
                status_code=503, detail=f"远程解析服务不可用：{exc}"
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"DLL 解析失败：{exc}") from exc

    @app.post("/api/logs/open", status_code=202)
    def open_log(request: OpenLogRequest):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        if serial_service is not None:
            serial_state = serial_service.status().get("state")
            if serial_state in ("running", "starting"):
                raise HTTPException(
                    status_code=409,
                    detail="串口监听正在运行，请先停止串口采集，再建立日志索引",
                )
        try:
            result = log_service.start_index(request.path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        infra.write_last_path(LAST_PATH_FILE, request.path)
        return result

    @app.get("/api/logs/status")
    def log_status():
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.status()

    @app.get("/api/fs/roots")
    def fs_roots():
        drives = _windows_drives()
        if drives:
            return {"roots": drives}
        home = str(Path.home().resolve())
        return {"roots": [{"name": home, "path": home}]}

    @app.get("/api/fs/list")
    def fs_list(path: str = Query("", max_length=1024)):
        if not path.strip():
            raise HTTPException(status_code=400, detail="请提供目录路径")
        return _list_directory(path)

    @app.get("/api/fs/last")
    def fs_last():
        return {"path": _read_last_path()}

    @app.get("/api/fs/pick")
    def fs_pick():
        if os.name != "nt":
            raise HTTPException(status_code=501, detail="仅 Windows 支持原生文件选择")
        path = _pick_file_via_tkinter_dialog(_read_last_path())
        return {"path": path}

    @app.get("/api/logs/frames")
    def log_frames(
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        query: str = Query("", max_length=100),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        after_id: int | None = Query(None, ge=0),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.list_frames(
                offset=offset,
                limit=limit,
                query=query,
                nid=nid,
                start_time=start_time,
                end_time=end_time,
                after_id=after_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/logs/minute-analysis")
    def minute_analysis(
        period_minutes: int = Query(15, ge=1, le=1440),
        cco_tei: str = Query("001", min_length=3, max_length=3,
                             pattern=r"^[0-9A-Fa-f]{3}$"),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            periods = log_service.list_minute_periods(period_minutes, cco_tei, nid)
            delete_stats = log_service.delete_config_stats(cco_tei, nid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "periods": periods,
            "summary": {
                "total_periods": len(periods),
                "report_count": sum(p["report_count"] for p in periods),
            },
            "delete_config_stats": delete_stats,
            "filters": {
                "period_minutes": period_minutes,
                "cco_tei": cco_tei.upper(),
                "nid": nid.strip().upper(),
            },
        }

    @app.get("/api/logs/delete-config-details")
    def delete_config_details(
        cco_tei: str = Query("001", min_length=3, max_length=3,
                             pattern=r"^[0-9A-Fa-f]{3}$"),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            details = log_service.delete_config_details(cco_tei, nid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "down_count": len(details["down"]),
            "up_count": len(details["up"]),
            "down": details["down"],
            "up": details["up"],
            "filters": {"cco_tei": cco_tei.upper()},
        }

    # ---------- 网络承载能力评估（按中央信标周期 + 网络隔离）----------

    def _run_network_assessment(index_id="", start_time="", end_time="", nid=""):
        """统一入口：调用 log_service 扫描网络并评估；无信标时走 fallback。"""
        kwargs = {}
        if index_id.strip():
            kwargs["index_id"] = index_id
        if start_time or end_time:
            kwargs["start_time"] = start_time
            kwargs["end_time"] = end_time
        if nid.strip():
            kwargs["nid"] = nid
        return log_service.list_beacon_periods(**kwargs)

    @app.get("/api/network/events")
    def network_events(
        index_id: str = Query("", max_length=64),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        nid: str = Query("", max_length=16),
        event: str = Query("", max_length=32),
        group: str = Query("", max_length=16),
        direction: str = Query("", max_length=8),
        query: str = Query("", max_length=64),
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        level: str = Query("", max_length=32),
    ):
        if nwk_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            data = nwk_service.list_events(
                index_id, start_time, end_time, nid, event, group,
                direction, query, limit, offset,
                level=level,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"组网事件查询失败：{exc}") from exc
        return data

    @app.get("/api/network/digest")
    def network_digest(
        index_id: str = Query("", max_length=64),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        nid: str = Query("", max_length=16),
    ):
        """印象结论包（REQS-0026，≤4KB）：人 + AI 同源的 L1 结论层。"""
        if nwk_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return nwk_service.digest(index_id, start_time, end_time, nid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"组网结论生成失败：{exc}") from exc

    @app.get("/api/network/events/{frame_id}/brief")
    def network_event_brief(frame_id: int, index_id: str = Query("", max_length=64)):
        """单帧粗略解析（REQS-0026，≤2KB）：点击事件时按需调用，不预载全量。"""
        if nwk_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return nwk_service.frame_brief(frame_id, index_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"找不到帧 #{frame_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"帧粗略解析失败：{exc}") from exc

    @app.get("/api/network/overview")
    def network_overview(
        index_id: str = Query("", max_length=64),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        nid: str = Query("", max_length=16),
    ):
        if nwk_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return nwk_service.overview(index_id, start_time, end_time, nid)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"组网总览失败：{exc}") from exc

    @app.get("/api/network/beacons")
    def network_beacons(
        index_id: str = Query("", max_length=64),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        nid: str = Query("", max_length=16),
        bcn_type: str = Query("beacon_central", max_length=24),
        limit: int = Query(50, ge=1, le=200),
    ):
        if nwk_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return nwk_service.list_beacons(
                index_id, start_time, end_time, nid, bcn_type, limit
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"信标明细查询失败：{exc}") from exc

    @app.get("/api/network/assessment")
    def network_assessment(
        index_id: str = Query("", max_length=64),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        nid: str = Query("", max_length=16),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            data = _run_network_assessment(index_id, start_time, end_time, nid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"网络评估失败：{exc}") from exc
        networks = data.get("networks") or []
        if not networks or not any(n.get("beacon_period_ms") for n in networks):
            return {
                "networks": [],
                "beacon_period_ms": None,
                "overall_health": None,
                "fallback": "beacon_undetected",
                "message": "未识别到中央信标帧（可能日志为其他设备帧或采样不足），"
                           "无法按实测信标周期分桶评估",
            }
        return {
            **data,
            "fallback": None,
        }

    @app.get("/api/network/status")
    def network_status(
        index_id: str = Query("", max_length=64),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        nid: str = Query("", max_length=16),
    ):
        """轻量快照（AI 查询用，≤1KB）：机器可读，评级枚举 healthy/degraded/fault。"""
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            data = _run_network_assessment(index_id, start_time, end_time, nid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"网络评估失败：{exc}") from exc
        networks = data.get("networks") or []
        if not networks or not any(n.get("beacon_period_ms") for n in networks):
            return {
                "networks": [],
                "beacon_period_ms": None,
                "overall_health": None,
                "latest_cycle": None,
                "fallback": "beacon_undetected",
            }
        latest_cycle = None
        latest_end = None
        snapshots = []
        for network in networks:
            cycles = network.get("cycles") or []
            if cycles:
                last = cycles[-1]
                if latest_end is None or (last.get("period_end") or 0) > latest_end:
                    latest_end = last.get("period_end") or 0
                    latest_cycle = {
                        "start_time": last.get("start_time"),
                        "end_time": last.get("end_time"),
                        "success_rate": last.get("success_rate"),
                        "rating": last.get("rating") or last.get("level"),
                    }
            snapshots.append({
                "nid": network.get("nid"),
                "cco_mac": network.get("cco_mac"),
                "beacon_period_ms": network.get("beacon_period_ms"),
                "overall_health": (network.get("summary") or {}).get("overall_health"),
                "latest_success_rate": (
                    (network.get("cycles") or [{}])[-1].get("success_rate")
                    if network.get("cycles") else None
                ),
            })
        return {
            "networks": snapshots,
            "beacon_period_ms": data.get("beacon_period_ms"),
            "overall_health": data.get("overall_health"),
            "latest_cycle": latest_cycle,
            "fallback": None,
        }

    @app.get("/api/logs/task-minute-analysis")
    def task_minute_analysis(task_no: str = Query(..., pattern=r"^\d{1,3}$"), period_minutes: int | None = Query(None, ge=1, le=1440), cco_tei: str = Query("001", pattern=r"^[0-9A-Fa-f]{3}$"), nid: str = Query("", max_length=16), start_time: str = Query("", max_length=12), end_time: str = Query("", max_length=12)):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        if start_time or end_time:
            return log_service.list_task_minute_periods(task_no, period_minutes, cco_tei, nid, start_time, end_time)
        return log_service.list_task_minute_periods(task_no, period_minutes, cco_tei, nid)

    @app.get("/api/logs/task-derived-period")
    def task_derived_period(
        task_no: str = Query(..., min_length=1, max_length=3, pattern=r"^\d{1,3}$"),
        cco_tei: str = Query("001", min_length=3, max_length=3,
                             pattern=r"^[0-9A-Fa-f]{3}$"),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.task_derived_period(cco_tei, task_no, nid)

    @app.get("/api/logs/task-config-tasks")
    def task_config_tasks(
        cco_tei: str = Query("001", min_length=3, max_length=3,
                             pattern=r"^[0-9A-Fa-f]{3}$"),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        if start_time or end_time:
            return {"tasks": log_service.list_task_config_numbers(cco_tei, nid, start_time, end_time)}
        return {"tasks": log_service.list_task_config_numbers(cco_tei, nid)}

    @app.get("/api/logs/task-config-summary")
    def task_config_summary(
        task_no: str = Query(..., min_length=1, max_length=3, pattern=r"^\d{1,3}$"),
        cco_tei: str = Query("001", min_length=3, max_length=3,
                             pattern=r"^[0-9A-Fa-f]{3}$"),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            data = (log_service.task_config_summary(cco_tei, task_no, nid, start_time, end_time)
                    if start_time or end_time else log_service.task_config_summary(cco_tei, task_no, nid))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            **data,
            "filters": {"cco_tei": cco_tei.upper(), "nid": nid.strip().upper()},
        }

    @app.get("/api/logs/task-config-lifecycle")
    def task_config_lifecycle(
        task_no: str = Query(..., min_length=1, max_length=3, pattern=r"^\d{1,3}$"),
        cycle_index: int | None = Query(None, ge=0),
        cco_tei: str = Query("001", min_length=3, max_length=3, pattern=r"^[0-9A-Fa-f]{3}$"),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.task_config_lifecycle_summary(cco_tei, task_no, nid, cycle_index)

    @app.get("/api/listener/indexes")
    @app.get("/api/indexes")
    def listener_indexes():
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        return log_service.list_indexes()

    @app.get("/api/listener/indexes/{index_id}/frames")
    @app.get("/api/indexes/{index_id}/frames")
    def listener_index_frames(
        index_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        query: str = Query("", max_length=100),
        nid: str = Query("", max_length=16, pattern=r"^[0-9A-Fa-f]{0,8}$"),
        start_time: str = Query("", max_length=12),
        end_time: str = Query("", max_length=12),
        after_id: int | None = Query(None, ge=0),
    ):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.list_index_frames(
                index_id,
                offset=offset,
                limit=limit,
                query=query,
                nid=nid,
                start_time=start_time,
                end_time=end_time,
                after_id=after_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该索引") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/listener/indexes/{index_id}/frames/{frame_id}")
    @app.get("/api/indexes/{index_id}/frames/{frame_id}")
    def listener_index_frame_detail(index_id: str, frame_id: int):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.get_index_frame(index_id, frame_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该索引或帧") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"帧详情解析失败：{exc}") from exc

    @app.get("/api/logs/frames/{frame_id}")
    def log_frame_detail(frame_id: int):
        if log_service is None:
            raise HTTPException(status_code=503, detail="日志服务未启用")
        try:
            return log_service.get_frame(frame_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该帧") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"帧详情解析失败：{exc}") from exc

    # ---------- 通信流追踪（需求 0009）----------

    @app.post("/api/listener/traces")
    def create_trace(body: dict):
        """创建追踪：window.mode=live 注册 live 句柄（201），否则同步回放（200）。"""
        if trace_service is None:
            raise HTTPException(status_code=503, detail="追踪服务未启用")
        try:
            if (body.get("window") or {}).get("mode") == "live":
                return trace_service.register_live(body)
            return trace_service.run_replay(body)
        except FeatureError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/listener/traces")
    def list_traces():
        if trace_service is None:
            raise HTTPException(status_code=503, detail="追踪服务未启用")
        return {"traces": trace_service.list_live()}

    @app.get("/api/listener/traces/{trace_id}")
    def get_trace(trace_id: str):
        if trace_service is None:
            raise HTTPException(status_code=503, detail="追踪服务未启用")
        try:
            return trace_service.live_snapshot(trace_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该追踪") from exc

    @app.delete("/api/listener/traces/{trace_id}")
    def stop_trace(trace_id: str):
        if trace_service is None:
            raise HTTPException(status_code=503, detail="追踪服务未启用")
        try:
            return trace_service.stop_live(trace_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="找不到该追踪") from exc

    # ---------- 串口实时采集 ----------

    @app.get("/api/serial/ports")
    def serial_ports():
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        port_details = serial_service.list_available_ports()
        mapping_error = getattr(serial_service, "mapping_error", lambda: "")()
        return {
            "ports": port_details,
            "port_details": port_details,
            "mapping_error": mapping_error,
        }

    @app.get("/api/serial/status")
    def serial_status():
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        return serial_service.status()

    class SerialStartRequest(BaseModel):
        port: str = Field("COM19", min_length=1, max_length=64)
        baudrate: int = Field(115200, ge=300, le=921600)
        bytesize: int = Field(8, ge=5, le=8)
        parity: str = Field("N", min_length=1, max_length=1)
        stopbits: int = Field(1, ge=1, le=2)

    @app.post("/api/serial/start", status_code=202)
    def serial_start(request: SerialStartRequest):
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        if log_service is not None:
            log_state = log_service.status().get("state")
            if log_state in ("indexing", "queued"):
                raise HTTPException(
                    status_code=409,
                    detail="日志正在建立索引，请等待完成后再启动串口采集",
                )
        try:
            result = serial_service.start(
                port=request.port,
                baudrate=request.baudrate,
                bytesize=request.bytesize,
                parity=request.parity,
                stopbits=request.stopbits,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"串口启动失败：{exc}") from exc
        return result

    @app.post("/api/serial/stop")
    def serial_stop():
        if serial_service is None:
            raise HTTPException(status_code=503, detail="串口服务未启用")
        return serial_service.stop()

    return app


# ---------- 模块级装配（供 uvicorn / 测试引用 module-level `app`）----------
# DLL 缺失（WSL 等环境）时 parser_service 降级为 None，串口/日志功能不受影响。
parser_service = _build_parser_service()
listener_index_registry = ListenerIndexRegistry(DEFAULT_INDEXES_DIR)
log_file_service = LogFileService(
    parser_service, DEFAULT_INDEX, index_registry=listener_index_registry
)
serial_capture_service = SerialCaptureService(
    log_file_service,
    port=_default_serial_port(),
    log_dir=_log_dir() / "侦听台",
)
app = create_app(parser_service, log_file_service, serial_capture_service)
