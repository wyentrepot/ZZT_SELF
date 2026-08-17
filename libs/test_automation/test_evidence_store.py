"""A3：EvidenceStore 证据追加/冻结契约测试（docs/03 §3、§9）。

覆盖：追加分配 stable id/run_id/sequence、sequence 单调递增、
冻结后拒绝追加、raw_ref 指向不可变原始内容、读取索引。
"""
import pytest

from test_automation.evidence_store import EvidenceStore
from test_automation.models import Evidence


class TestEvidenceStore:
    def test_append_assigns_id_runid_sequence(self):
        store = EvidenceStore(run_id="run-1")
        ev = store.append(kind="frame", source="listener", payload={"len": 68})
        assert ev.run_id == "run-1"
        assert ev.sequence == 1
        assert ev.id == "run-1-ev-1"
        assert ev.kind == "frame"
        assert ev.payload == {"len": 68}

    def test_sequence_monotonic(self):
        store = EvidenceStore(run_id="run-1")
        seqs = []
        for i in range(1, 5):
            ev = store.append(kind="event", source="loghooks", payload={"n": i})
            seqs.append(ev.sequence)
            assert ev.id == f"run-1-ev-{i}"
        assert seqs == [1, 2, 3, 4]

    def test_append_unknown_kind_rejected(self):
        store = EvidenceStore(run_id="run-1")
        with pytest.raises(ValueError):
            store.append(kind="bogus", source="x", payload={})

    def test_freeze_rejects_append(self):
        store = EvidenceStore(run_id="run-1")
        store.append(kind="frame", source="listener")
        store.freeze()
        with pytest.raises(RuntimeError):
            store.append(kind="frame", source="listener")

    def test_freeze_is_idempotent(self):
        store = EvidenceStore(run_id="run-1")
        store.freeze()
        store.freeze()  # 不抛错

    def test_list_returns_ordered_evidence(self):
        store = EvidenceStore(run_id="run-1")
        store.append(kind="frame", source="listener", payload={"a": 1})
        store.append(kind="event", source="loghooks", payload={"b": 2})
        items = store.list()
        assert [e.sequence for e in items] == [1, 2]
        assert [e.id for e in items] == ["run-1-ev-1", "run-1-ev-2"]

    def test_frozen_list_still_readable(self):
        store = EvidenceStore(run_id="run-1")
        store.append(kind="frame", source="listener")
        store.freeze()
        assert len(store.list()) == 1  # 冻结只禁追加，不禁读

    def test_raw_ref_stored(self):
        store = EvidenceStore(run_id="run-1")
        ev = store.append(kind="frame", source="listener", payload={}, raw_ref="blob:sha256:abc")
        assert ev.raw_ref == "blob:sha256:abc"
