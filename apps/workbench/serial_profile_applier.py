"""P4 SerialProfileApplier: 一键应用已保存 Profile 到托管会话。

总实施计划 P4：
- 只在显式 apply 时调用 module/listener/simcon 服务（保存不碰硬件）。
- 固定处理顺序：侦听台 → CCO → STA → 模拟集中器。
- 对相同且已运行的托管目标返回 unchanged/reused。
- 空槽或禁用槽仅停止其托管会话。
- 单槽失败继续执行后续槽，不回滚成功项。
- 响应逐槽返回 started/reused/stopped/unchanged/skipped/failed、原因及当前状态。
- Apply 只读取已保存 Profile，不接受临时表单配置。
- 只停止配置页创建的托管会话，不影响人工动态会话（托管会话用 title 前缀标记）。

状态枚举（总计划）：started / reused / stopped / unchanged / skipped / failed。
"""
from __future__ import annotations

from typing import Any, Callable

from shared.serial_profile import PROFILE_SLOTS, SerialProfileStore

# 固定应用顺序（总计划 P4）
APPLY_ORDER = ["listener.main", "module_log.cco", "module_log.sta", "simcon.main"]

# 托管会话 title 前缀：applier 只管理带此前缀的会话，人工会话不受影响
MANAGED_TITLE_PREFIX = "托管-"

# 槽 -> module 类型（module 槽专用）
SLOT_MODULE = {
    "module_log.cco": "cco",
    "module_log.sta": "sta",
}


class SimconProfileAdapter:
    """把 simcon 子应用暴露的 open/close 服务函数包装成 applier 期望的接口。

    通过注入的 simcon_open_io / simcon_close_io（来自 create_simcon_app 的
    app.state），applier 可以不发 HTTP 直接控制模拟集中器串口。
    """

    def __init__(self, open_io: Callable[[dict[str, Any]], None],
                 close_io: Callable[[], None],
                 resolve: Callable[..., dict[str, Any]]):
        self._open_io = open_io
        self._close_io = close_io
        self._resolve = resolve
        self.open_port: str | None = None

    def open(self, port: str) -> None:
        resolved = self._resolve(port=port)
        self._open_io(resolved)
        self.open_port = resolved.get("port") or port

    def close(self) -> None:
        self._close_io()
        self.open_port = None


class _SlotResult(dict):
    """逐槽结果：status/reason/current_state。"""


class SerialProfileApplier:
    def __init__(self, *, module_service, listener_service, simcon_service=None,
                 profile_store: SerialProfileStore):
        self.module_service = module_service
        self.listener_service = listener_service
        self.simcon_service = simcon_service
        self.profile_store = profile_store

    def apply(self) -> dict[str, Any]:
        """应用已保存 Profile。返回 overall + 逐槽结果。"""
        profiles = self.profile_store.load()
        slots: list[dict[str, Any]] = []
        ok_count = 0
        fail_count = 0
        for slot in APPLY_ORDER:
            try:
                entry = self._apply_slot(slot, profiles.get(slot) or {})
            except Exception as exc:  # noqa: BLE001 - 单槽失败继续
                entry = _SlotResult(
                    slot=slot, status="failed",
                    reason=str(exc) or exc.__class__.__name__,
                    current_state=self._current_state(slot, profiles.get(slot) or {}),
                )
            if entry["status"] in ("failed",):
                fail_count += 1
            elif entry["status"] in ("started", "reused", "stopped"):
                ok_count += 1
            slots.append(entry)

        overall = "failed" if ok_count == 0 and fail_count > 0 else (
            "partial" if fail_count else "ok"
        )
        return {
            "overall": overall,
            "slots": slots,
            "ok_count": ok_count,
            "fail_count": fail_count,
        }

    # ------------------------------------------------------------------
    def _apply_slot(self, slot: str, profile: dict[str, Any]) -> dict[str, Any]:
        enabled = bool(profile.get("enabled", False))
        mapping_id = str(profile.get("mapping_id") or "")
        if not enabled:
            return self._stop_managed(slot, profile)
        if not mapping_id:
            # simcon 无固定映射：空映射 = 自动选择可用串口（端口不锁定）。
            if slot == "simcon.main":
                return self._apply_simcon(profile, None)
            return _SlotResult(slot=slot, status="skipped", reason="未选择映射",
                               current_state=self._current_state(slot, profile))
        device = self.profile_store.device_for(mapping_id)
        if slot == "listener.main":
            return self._apply_listener(profile, device)
        if slot in SLOT_MODULE:
            return self._apply_module(slot, profile, device)
        if slot == "simcon.main":
            return self._apply_simcon(profile, device)
        raise ValueError(f"未知槽：{slot}")

    def _apply_listener(self, profile: dict[str, Any], device: str) -> dict[str, Any]:
        service = self.listener_service
        status = service.status() or {}
        if status.get("state") == "running" and status.get("port") == device:
            return _SlotResult(slot="listener.main", status="unchanged",
                               reason="已运行且端口一致",
                               current_state={"state": "running", "port": device})
        result = service.start(
            port=device,
            baudrate=profile.get("baudrate"),
            bytesize=profile.get("bytesize"),
            parity=profile.get("parity"),
            stopbits=profile.get("stopbits"),
        )
        return _SlotResult(slot="listener.main", status="started",
                           current_state=result or {"state": "running", "port": device})

    def _apply_module(self, slot: str, profile: dict[str, Any], device: str) -> dict[str, Any]:
        module = SLOT_MODULE[slot]
        managed = self._find_managed_session(slot, module)
        if managed:
            state = (managed.get("state") or self._module_state(managed))
            if state == "running":
                return _SlotResult(slot=slot, status="reused",
                                   reason=f"托管会话 {managed['session_id']} 已运行",
                                   current_state={"state": "running",
                                                  "session_id": managed["session_id"],
                                                  "port": managed.get("port") or device})
        if managed:
            # 已存在但未运行 → 直接 start
            self.module_service.start_session(
                managed["session_id"], port=device,
                baudrate=profile.get("baudrate"), bytesize=profile.get("bytesize"),
                parity=profile.get("parity"), stopbits=profile.get("stopbits"),
            )
            return _SlotResult(slot=slot, status="started",
                               current_state={"state": "running", "session_id": managed["session_id"],
                                              "port": device})
        # 新建托管会话
        created = self.module_service.create_session(title=f"{MANAGED_TITLE_PREFIX}{module.upper()}",
                                                     module=module)
        sid = created["session_id"]
        self.module_service.start_session(
            sid, port=device,
            baudrate=profile.get("baudrate"), bytesize=profile.get("bytesize"),
            parity=profile.get("parity"), stopbits=profile.get("stopbits"),
        )
        return _SlotResult(slot=slot, status="started",
                           current_state={"state": "running", "session_id": sid, "port": device})

    def _apply_simcon(self, profile: dict[str, Any], device: str | None) -> dict[str, Any]:
        """device=None 表示自动选择串口；实际打开端口回填到 current_state。"""
        if self.simcon_service is None:
            return _SlotResult(slot="simcon.main", status="skipped", reason="模拟集中器服务不可用")
        open_port = getattr(self.simcon_service, "open_port", None)
        if device and open_port == device:
            return _SlotResult(slot="simcon.main", status="unchanged",
                               reason="模拟集中器已打开同端口",
                               current_state={"state": "open", "port": device})
        if not device and open_port:
            return _SlotResult(slot="simcon.main", status="unchanged",
                               reason="模拟集中器已打开（自动模式不限定端口）",
                               current_state={"state": "open", "port": open_port})
        self.simcon_service.open(device)
        actual_port = getattr(self.simcon_service, "open_port", None)
        return _SlotResult(slot="simcon.main", status="started",
                           current_state={"state": "open", "port": actual_port})

    def _stop_managed(self, slot: str, profile: dict[str, Any]) -> dict[str, Any]:
        """禁用/空槽：停止托管会话（不影响人工会话）。"""
        if slot == "listener.main":
            service = self.listener_service
            status = service.status() or {}
            if status.get("state") == "running":
                result = service.stop()
                return _SlotResult(slot=slot, status="stopped",
                                   current_state=result or {"state": "stopped"})
            return _SlotResult(slot=slot, status="unchanged", reason="侦听台未运行",
                               current_state=status or {"state": "stopped"})
        if slot in SLOT_MODULE:
            module = SLOT_MODULE[slot]
            managed = self._find_managed_session(slot, module)
            if managed:
                state = managed.get("state") or self._module_state(managed)
                if state == "running":
                    self.module_service.stop_session(managed["session_id"])
                    return _SlotResult(slot=slot, status="stopped",
                                       current_state={"state": "stopped",
                                                      "session_id": managed["session_id"]})
            return _SlotResult(slot=slot, status="unchanged", reason="无运行托管会话",
                               current_state={"state": "stopped"})
        if slot == "simcon.main":
            if self.simcon_service is not None and getattr(self.simcon_service, "open_port", None):
                self.simcon_service.close()
                return _SlotResult(slot=slot, status="stopped", current_state={"state": "closed"})
            return _SlotResult(slot=slot, status="unchanged", reason="模拟集中器未打开",
                               current_state={"state": "closed"})
        return _SlotResult(slot=slot, status="skipped", reason="未知槽")

    # ------------------------------------------------------------------
    def _find_managed_session(self, slot: str, module: str) -> dict[str, Any] | None:
        """找本槽的托管会话：title 前缀 + module 匹配。"""
        sessions = self.module_service.list_sessions() or []
        for item in sessions:
            title = str(item.get("title") or "")
            mod = str(item.get("module") or "")
            if title.startswith(MANAGED_TITLE_PREFIX) and mod == module:
                return item
        return None

    def _module_state(self, managed: dict[str, Any]) -> str:
        channel = managed.get("channel") or {}
        status = channel.get("status")
        if callable(status):
            try:
                return (status() or {}).get("state", "stopped")
            except Exception:  # noqa: BLE001
                return "stopped"
        return str(managed.get("state") or "stopped")

    def _current_state(self, slot: str, profile: dict[str, Any]) -> dict[str, Any]:
        """尽力返回当前状态（失败/跳过时用于报告）。"""
        if slot == "listener.main":
            try:
                return self.listener_service.status() or {"state": "unknown"}
            except Exception:  # noqa: BLE001
                return {"state": "unknown"}
        if slot in SLOT_MODULE:
            managed = self._find_managed_session(slot, SLOT_MODULE[slot])
            if managed:
                return {"state": managed.get("state") or self._module_state(managed),
                        "session_id": managed.get("session_id")}
            return {"state": "stopped"}
        if slot == "simcon.main":
            return {"state": "open" if getattr(self.simcon_service, "open_port", None) else "closed"}
        return {"state": "unknown"}

    def snapshot(self) -> dict[str, Any]:
        """只读状态快照：四槽当前状态与占用，不打开/关闭任何串口。

        用途：P6 配置页「状态/占用」字段的数据源（ADR-35 要求该两项，
        此前前端渲染后从不赋值）。

        复用 `_current_state()`；按 `PROFILE_SLOTS` 顺序返回（与前端四槽顺序一致）；
        单槽取数失败不影响其他槽，只把该槽标为 unknown。
        """
        profiles = self.profile_store.load()
        slots: list[dict[str, Any]] = []
        for slot in PROFILE_SLOTS:
            profile = profiles.get(slot) or {}
            try:
                state = self._current_state(slot, profile) or {}
            except Exception as exc:  # noqa: BLE001 - 单槽失败继续
                state = {"state": "unknown",
                         "reason": str(exc) or exc.__class__.__name__}
            # 占用：module 槽是托管会话 ID，listener/simcon 是实际端口
            owner = state.get("session_id") or state.get("port") or ""
            if slot == "simcon.main" and not owner:
                owner = getattr(self.simcon_service, "open_port", None) or ""
            slots.append({
                "slot": slot,
                "state": state.get("state") or "unknown",
                "owner": owner,
                "detail": state,
            })
        return {"slots": slots}
