"""workbench.orchestration.evidence —— Run 级三源 Evidence 接入（任务 3 剩余）。

把三路原始数据统一转成 test_automation.Evidence 并写入 run 级 EvidenceStore，
使一个 Run 同时消费三源证据（docs/03 §3 Evidence 契约、§5 SourceAdapter）：

- monitor（loghooks 事件）→ kind=event,   source=loghooks
- stimulus（sim_concentrator 步骤）→ kind=interaction, source=sim_concentrator
- listener（侦听台帧）→ kind=frame, source=listener

复用 libs/test_automation 的 EvidenceStore（追加分配 stable id / 单调 sequence /
freeze 冻结窗口）与三源适配器（sources.py）。数据源可注入（frame_records /
step_results / events 列表），不依赖真实串口与运行时，便于单测。

串口资源租约：stimulus 涉及的串口按 (resource_type, resource_id) 独占 acquire，
冲突抛 ResourceConflictError（任务 3 验收出口：资源冲突可预测）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from test_automation.evidence_store import EvidenceStore
from test_automation.resource_lease import (
    ResourceConflictError,
    ResourceLeaseManager,
)
from test_automation.sources import (
    ListenerFrameAdapter,
    LoghooksEventAdapter,
    SimConcentratorAdapter,
    loghooks_event_evidence,
)

#: 三源标识（与 test_automation.sources 的 source 字段对齐）
SOURCE_LOGHOOKS = "loghooks"
SOURCE_SIMCON = "sim_concentrator"
SOURCE_LISTENER = "listener"


def _dict_to_loghooks_event(ev: Dict[str, Any]) -> Any:
    """把 _scan_logs 产出的 dict 事件包装成 sources.loghooks_event_evidence 可消费的对象。

    _scan_logs 返回的 events 是 dict（{type,label,message,time,rule_id,category,source}），
    loghooks_event_evidence 用 getattr 读字段。这里返回一个轻量代理：dict 存在对应键时
    返回键值，否则返回空，与 loghooks Event dataclass 字段兼容。
    """
    class _EventProxy:
        def __init__(self, data: Dict[str, Any]):
            self._data = data

        def __getattr__(self, name: str) -> Any:
            return self._data.get(name, "")

    return _EventProxy(ev)


def _evidence_to_store_sink(store: EvidenceStore):
    """把 Evidence 对象写入 EvidenceStore（适配器 sink 契约 → EvidenceStore.append 契约）。

    SourceAdapter.collect 约定 evidence_sink(ev) 接收 Evidence 对象；而
    EvidenceStore.append 是字段式 API（kind/source/payload...）。此处桥接：
    将 Evidence 对象的字段映射为 append 参数。EvidenceStore 内部会重新分配
    stable id/sequence（追加式），保留原始 raw_ref/correlation_key/metadata。
    """
    def _sink(ev: Any) -> None:
        store.append(
            kind=ev.kind,
            source=ev.source,
            payload=dict(ev.payload or {}),
            raw_ref=ev.raw_ref,
            correlation_key=ev.correlation_key,
            metadata=dict(ev.metadata or {}),
        )

    return _sink


def collect_three_source_evidence(
    run_id: str,
    events: Optional[List[Any]] = None,
    step_results: Optional[List[Dict[str, Any]]] = None,
    frame_records: Optional[List[tuple]] = None,
    case_id: str = "",
) -> EvidenceStore:
    """三源数据 → 同一 run 级 EvidenceStore（按源顺序追加，sequence 单调）。

    入参为三源原始数据列表（可注入 mock），复用 sources.py 适配器转 Evidence：
    - events: loghooks Event 列表
    - step_results: sim_concentrator 步骤结果 dict 列表
    - frame_records: listener 帧记录 (sequence, log_time, hex_frame) 列表
    返回已填充全部证据的 EvidenceStore（调用方按需 freeze）。
    """
    store = EvidenceStore(run_id=run_id)
    run_context = {"run_id": run_id, "case_id": case_id}
    sink = _evidence_to_store_sink(store)

    if events:
        adapter = LoghooksEventAdapter(
            [_dict_to_loghooks_event(ev) if isinstance(ev, dict) else ev for ev in events]
        )
        adapter.start(run_context)
        adapter.collect(sink)  # type: ignore[arg-type]

    if step_results:
        adapter = SimConcentratorAdapter(list(step_results), case_id=case_id)
        adapter.start(run_context)
        adapter.collect(sink)  # type: ignore[arg-type]

    if frame_records:
        adapter = ListenerFrameAdapter(list(frame_records))
        adapter.start(run_context)
        adapter.collect(sink)  # type: ignore[arg-type]

    return store


def acquire_serial_lease(
    manager: ResourceLeaseManager,
    holder: str,
    resource_id: str,
    resource_type: str = "serial_port",
) -> Any:
    """串口资源独占租约封装（任务 3：资源冲突可预测）。

    holder 对 resource_id 独占 acquire；已被他人独占时抛 ResourceConflictError。
    返回 ResourceLease（供调用方 release 时使用）。
    """
    from test_automation.models import DeviceSpec

    spec = DeviceSpec(resource_type=resource_type, resource_id=resource_id, shared=False)
    return manager.acquire(spec, holder)


def evidence_index(store: EvidenceStore) -> Dict[str, Any]:
    """EvidenceStore → 可下钻的 evidence_index（Report 暴露，不导出原始 Evidence 全量）。

    index 按 source 分组：{source: [raw_ref, ...]}，raw_ref 为证据可追溯锚点
    （listener:seq:N / simcon:step:N / loghooks:<rule_id>），满足"报告可追到原始帧/日志"。
    """
    index: Dict[str, List[str]] = {}
    total = 0
    for ev in store.list():
        total += 1
        index.setdefault(ev.source, []).append(ev.raw_ref or f"{ev.source}:ev:{ev.sequence}")
    return {"total": total, "sources": index}
