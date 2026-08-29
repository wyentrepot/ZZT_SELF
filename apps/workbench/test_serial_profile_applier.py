"""P4 SerialProfileApplier RED/GREEN tests (fake services, no real serial).

总实施计划 P4：
- 固定处理顺序：侦听台 → CCO → STA → 模拟集中器。
- 对相同且已运行的托管目标返回 unchanged/reused。
- 空槽或禁用槽仅停止其托管会话。
- 单槽失败继续执行后续槽，不回滚成功项。
- 响应逐槽返回 started/reused/stopped/unchanged/skipped/failed、原因及当前状态。
- Apply 只读取已保存 Profile，不接受临时表单配置。

平台说明：SerialProfileApplier 按 os.name 解析设备名（Windows=COM 名、
Linux=/dev/tty* 名）。断言不硬编码设备名，而是用 profile_store.device_for(...)
取当前平台实际解析结果，使 Windows/Linux 均可验证（与 apps/listener/
test_serial_service.py 的平台测试风格一致，见 commit 305a3b0）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.serial_profile import SerialProfileStore
from workbench.serial_profile_applier import SerialProfileApplier


class FakeModuleService:
    """fake module 会话服务：记录 start/stop，支持运行态与冲突。"""

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self._seq = 0
        self.fail_port: str | None = None  # 指定 port 时 start 抛错

    def create_session(self, title="", module="cco", legacy_channel=None):
        self._seq += 1
        sid = f"ms-fake-{self._seq}"
        self.sessions[sid] = {
            "session_id": sid, "module": module, "title": title or module,
            "channel": {"status": lambda: {"state": "stopped"}},
        }
        return self.sessions[sid]

    def list_sessions(self):
        return [self._payload(s) for s in self.sessions.values()]

    def _payload(self, s):
        return {
            "session_id": s["session_id"], "module": s["module"],
            "title": s.get("title", ""),
            "state": s.get("_state", "stopped"),
            "port": s.get("_port", ""), "mapping_id": s.get("_mapping", ""),
        }

    def start_session(self, session_id, port, baudrate=115200, bytesize=8, parity="N", stopbits=1):
        if self.fail_port and port == self.fail_port:
            raise RuntimeError(f"打开 {port} 失败（fake）")
        s = self.sessions[session_id]
        s["_state"] = "running"
        s["_port"] = port
        s["_mapping"] = port
        self.started.append({"session_id": session_id, "port": port, "baudrate": baudrate})
        return self._payload(s)

    def stop_session(self, session_id, force=False):
        s = self.sessions[session_id]
        if s.get("_state") == "running":
            s["_state"] = "stopped"
            self.stopped.append(session_id)
        return self._payload(s)

    def get_session(self, session_id):
        return self._payload(self.sessions[session_id])


class FakeListenerService:
    """fake listener 采集服务。"""

    def __init__(self):
        self._state = "stopped"
        self._port = ""
        self.started = 0
        self.stopped = 0
        self.fail_port: str | None = None

    def status(self):
        return {"state": self._state, "port": self._port}

    def start(self, port=None, baudrate=None, bytesize=None, parity=None, stopbits=None):
        if self.fail_port and port == self.fail_port:
            raise RuntimeError(f"打开 {port} 失败（fake）")
        self._state = "running"
        self._port = port or ""
        self.started += 1
        return self.status()

    def stop(self):
        if self._state == "running":
            self._state = "stopped"
            self.stopped += 1
        return self.status()


class FakeSimcon:
    """fake 模拟集中器：open/close 回调记录。"""

    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.open_port: str | None = None

    def open(self, port: str):
        self.open_port = port
        self.opened += 1

    def close(self):
        if self.open_port:
            self.closed += 1
            self.open_port = None


@pytest.fixture
def profile_store(tmp_path: Path) -> SerialProfileStore:
    mapping = tmp_path / "serial_ports.json"
    mapping.write_text(json.dumps({
        "version": 1,
        "ports": [
            {"id": "listener", "linux_device": "/dev/ttyUSB0", "windows_com": "COM4",
             "label": "侦听台", "usage": "listener", "module": "",
             "baudrate": 115200, "parity": "E", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "cco-main", "linux_device": "/dev/ttyACM1", "windows_com": "COM9",
             "label": "CCO 日志口", "usage": "module_log", "module": "cco",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "sta-main", "linux_device": "/dev/ttyACM0", "windows_com": "COM8",
             "label": "STA 日志口", "usage": "module_log", "module": "sta",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "simcon", "linux_device": "/dev/ttyUSB1", "windows_com": "COM19",
             "label": "模拟集中器", "usage": "simcon", "module": "",
             "baudrate": 9600, "parity": "E", "bytesize": 8, "stopbits": 1, "enabled": True},
        ],
    }), encoding="utf-8")
    return SerialProfileStore(runtime_dir=tmp_path / "runtime", mapping_config_path=mapping)


def _applier(profile_store, module=None, listener=None, simcon=None):
    return SerialProfileApplier(
        module_service=module or FakeModuleService(),
        listener_service=listener or FakeListenerService(),
        simcon_service=simcon or FakeSimcon(),
        profile_store=profile_store,
    )


# ---------------------------------------------------------------------------
# 默认 Profile（全禁用）：零启动
# ---------------------------------------------------------------------------

def test_default_profile_starts_nothing(profile_store):
    module = FakeModuleService()
    listener = FakeListenerService()
    simcon = FakeSimcon()
    result = _applier(profile_store, module, listener, simcon).apply()

    assert result["overall"] == "ok"
    assert listener.started == 0
    assert module.started == []
    assert simcon.opened == 0
    # 每槽 skipped 或 unchanged（默认禁用 => skipped）
    for slot in result["slots"]:
        assert slot["status"] in ("skipped", "unchanged")


# ---------------------------------------------------------------------------
# 启用后按顺序启动
# ---------------------------------------------------------------------------

def test_enabled_slots_start_in_fixed_order(profile_store):
    profile_store.update_slot("listener.main", mapping_id="listener", enabled=True)
    profile_store.update_slot("module_log.cco", mapping_id="cco-main", enabled=True)
    profile_store.update_slot("module_log.sta", mapping_id="sta-main", enabled=True)
    profile_store.update_slot("simcon.main", mapping_id="simcon", enabled=True)

    module = FakeModuleService()
    listener = FakeListenerService()
    simcon = FakeSimcon()
    result = _applier(profile_store, module, listener, simcon).apply()

    statuses = [s["slot"] for s in result["slots"]]
    # 固定顺序：listener → module_log.cco → module_log.sta → simcon.main
    assert statuses == ["listener.main", "module_log.cco", "module_log.sta", "simcon.main"]
    assert listener.started == 1
    assert listener._port == profile_store.device_for("listener")
    assert len(module.started) == 2
    assert simcon.opened == 1
    assert simcon.open_port == profile_store.device_for("simcon")
    for s in result["slots"]:
        assert s["status"] == "started"


# ---------------------------------------------------------------------------
# simcon 槽空映射 = 自动选择串口
# ---------------------------------------------------------------------------

def test_simcon_slot_empty_mapping_uses_auto_port(profile_store):
    profile_store.update_slot("simcon.main", mapping_id="", enabled=True)
    entry = profile_store.load()["simcon.main"]
    # 自动模式：空映射 + 1376.2 本地总线缺省参数
    assert entry["mapping_id"] == ""
    assert entry["baudrate"] == 9600 and entry["parity"] == "E"

    simcon = FakeSimcon()
    result = _applier(profile_store, simcon=simcon).apply()

    slot = next(s for s in result["slots"] if s["slot"] == "simcon.main")
    assert slot["status"] == "started"
    assert simcon.opened == 1
    # 自动模式不限定端口：透传 None，由 simcon 执行核心自行选择
    assert simcon.open_port is None


def test_simcon_slot_auto_mode_unchanged_when_already_open(profile_store):
    profile_store.update_slot("simcon.main", mapping_id="", enabled=True)
    simcon = FakeSimcon()
    simcon.open_port = "COM7"  # 已在其他端口打开
    result = _applier(profile_store, simcon=simcon).apply()

    slot = next(s for s in result["slots"] if s["slot"] == "simcon.main")
    assert slot["status"] == "unchanged"
    assert simcon.opened == 0


# ---------------------------------------------------------------------------
# 相同且已运行的托管目标返回 unchanged/reused
# ---------------------------------------------------------------------------

def test_reused_when_already_running(profile_store):
    profile_store.update_slot("listener.main", mapping_id="listener", enabled=True)
    listener = FakeListenerService()
    listener.start(port=profile_store.device_for("listener"))
    listener.started = 99  # 记录初始调用

    module = FakeModuleService()
    result = _applier(profile_store, module, listener, FakeSimcon()).apply()

    list_slot = next(s for s in result["slots"] if s["slot"] == "listener.main")
    assert list_slot["status"] == "unchanged"
    # 没有重复 start
    assert listener.started == 99


# ---------------------------------------------------------------------------
# 禁用槽停止其托管会话
# ---------------------------------------------------------------------------

def test_disabled_slot_stops_managed_session(profile_store):
    profile_store.update_slot("module_log.cco", mapping_id="cco-main", enabled=False)
    module = FakeModuleService()
    # 预置一个运行中的 cco 托管会话
    sid = module.create_session(title="托管-CCO", module="cco")["session_id"]
    module.start_session(sid, port="cco-main")

    result = _applier(profile_store, module, FakeListenerService(), FakeSimcon()).apply()

    cco_slot = next(s for s in result["slots"] if s["slot"] == "module_log.cco")
    assert cco_slot["status"] == "stopped"
    assert sid in module.stopped


# ---------------------------------------------------------------------------
# 单槽失败继续执行后续槽，不回滚成功项
# ---------------------------------------------------------------------------

def test_partial_failure_continues_and_keeps_successes(profile_store):
    profile_store.update_slot("listener.main", mapping_id="listener", enabled=True)
    profile_store.update_slot("module_log.cco", mapping_id="cco-main", enabled=True)
    profile_store.update_slot("module_log.sta", mapping_id="sta-main", enabled=True)

    module = FakeModuleService()
    module.fail_port = profile_store.device_for("cco-main")  # cco 启动失败（device 解析结果）
    listener = FakeListenerService()
    result = _applier(profile_store, module, listener, FakeSimcon()).apply()

    by_slot = {s["slot"]: s for s in result["slots"]}
    # listener 成功
    assert by_slot["listener.main"]["status"] == "started"
    # cco 失败
    assert by_slot["module_log.cco"]["status"] == "failed"
    assert "原因" in by_slot["module_log.cco"]["reason"] or "reason" in by_slot["module_log.cco"]
    # sta 仍被尝试（继续执行后续槽）
    assert by_slot["module_log.sta"]["status"] == "started"
    # overall 为部分成功
    assert result["overall"] == "partial"
    assert result["ok_count"] == 2
    assert result["fail_count"] == 1


# ---------------------------------------------------------------------------
# Apply 只读已保存 Profile（不接受临时表单）
# ---------------------------------------------------------------------------

def test_apply_reads_saved_profile_only(profile_store):
    # 保存 profile，不传任何表单
    profile_store.update_slot("listener.main", mapping_id="listener", enabled=True)
    listener = FakeListenerService()
    result = _applier(profile_store, module=FakeModuleService(), listener=listener,
                      simcon=FakeSimcon()).apply()
    assert result["slots"][0]["status"] == "started"
    assert listener.started == 1
