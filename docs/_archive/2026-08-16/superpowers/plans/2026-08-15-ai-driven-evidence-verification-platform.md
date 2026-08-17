# AI 需求驱动证据验证平台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a versioned, loadable test-case platform that turns a development requirement into an evidence-backed verification Run spanning simulated-concentrator interactions, listener frames, module logs, and a final `passed` / `failed` / `inconclusive` conclusion.

**Architecture:** Add a reusable `libs/test_automation` domain layer rather than extending the current coarse `workbench.orchestration` directly. Case packages remain declarative JSON assets; the execution engine owns resource leases, real-time evidence windows, actions, assertions, and report construction. `apps/workbench` exposes the catalog and Run APIs and renders results, while it reuses `sim_concentrator`, `listener`, `module_log`, and `loghooks` through explicit adapters.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, pytest, standard-library JSON/SQLite/asyncio/threading, existing pyserial, `libs/sim_concentrator`, `libs/loghooks`, and `apps/workbench`.

## Global Constraints

- Do not copy `D:\03-自动化改造\GW-CASS\web_gateway\ToolThread.py`, `GlobalVariable.py`, or its global state model into this repository.
- Milestone 1 case packages use JSON only; no new YAML runtime dependency is introduced.
- A case package may contain data, rules, fixtures, and templates only. It must not execute arbitrary uploaded Python code.
- A `Run` must start its evidence window before any stimulus is sent and freeze evidence before any assertion is evaluated.
- Every verdict must be one of `passed`, `failed`, `inconclusive`, or `aborted`; `inconclusive` must never be counted as passed.
- Every device operation must hold an explicit resource lease; a real COM port cannot be opened by two independent adapters in the same Run.
- Every dangerous action (`flash`, `relay_set`, `power_set`) requires `dangerous=true` in the action specification and `allow_dangerous_actions=true` in the Run request.
- Preserve existing independent listener and module-log applications and their APIs. Integrate through adapters; do not merge their code into workbench.
- All new domain behavior needs pytest coverage with fake adapters or golden data. Real-hardware tests are additional acceptance evidence, not the only test layer.
- Each task ends with the named test command and a focused commit.

---

## Target File Structure

```text
libs/test_automation/
├── __init__.py
├── models.py                     # Case/Plan/Run/Evidence typed contract
├── catalog.py                    # package discovery, validation, hashing
├── resources.py                  # resource leases and conflict detection
├── evidence.py                   # append-only evidence JSONL and snapshots
├── assertions.py                 # frame/event/roster/absence assertions
├── executor.py                   # lifecycle and plan execution
├── adapters/
│   ├── __init__.py
│   ├── base.py                   # Adapter and EvidenceSource protocols
│   ├── simcon.py                 # current sim_concentrator wrapper
│   ├── listener.py               # listener frame evidence adapter
│   └── loghooks.py               # module log event adapter
└── test_*.py

test_assets/cases/
└── anhui-minute-collection/
    ├── case.json
    ├── plan.json
    ├── parameters.schema.json
    ├── roster.schema.json
    ├── listener.filters.json
    ├── loghooks.rules.json
    └── fixtures/fake-roster.json

apps/workbench/
├── automation_api.py             # /api/cases, /api/verification-runs
├── automation_service.py         # bridge to catalog and executor
├── orchestration/models.py       # add compatibility links from old Run to new Run
├── static/pages/automation/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── test_automation_api.py
```

## Interface Contract

All later tasks use these names and shapes; do not rename them during implementation.

```python
# libs/test_automation/models.py
Verdict = Literal["passed", "failed", "inconclusive", "aborted"]

class CaseManifest(BaseModel):
    id: str
    version: str
    name: str
    entry_plan: str = "plan.json"
    source: dict[str, str] = Field(default_factory=dict)
    required_resources: list[ResourceRequest] = Field(default_factory=list)

class Evidence(BaseModel):
    evidence_id: str
    run_id: str
    step_id: str
    source: Literal["simcon", "listener", "module_log", "device", "system"]
    kind: str
    captured_at: datetime
    monotonic_ns: int
    correlation: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str = ""

class AssertionResult(BaseModel):
    id: str
    verdict: Literal["pass", "fail", "inconclusive"]
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)

class VerificationRunRequest(BaseModel):
    case_id: str
    case_version: str | None = None
    parameters: dict[str, Any]
    roster: dict[str, Any] | None = None
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    requirement_id: str = ""
    commit_sha: str = ""
    allow_dangerous_actions: bool = False
```

### Task 1: Create the core test-automation domain model

**Files:**

- Create: `libs/test_automation/__init__.py`
- Create: `libs/test_automation/models.py`
- Create: `libs/test_automation/test_models.py`

**Interfaces:**

- Consumes: Pydantic already used by `apps/workbench/orchestration/models.py`.
- Produces: `CaseManifest`, `CasePlan`, `PlanStep`, `ResourceRequest`, `Evidence`, `AssertionSpec`, `AssertionResult`, `VerificationRunRequest`, and `VerificationRunReport` for all following tasks.

- [ ] **Step 1: Write the failing model tests**

```python
# libs/test_automation/test_models.py
import pytest
from pydantic import ValidationError
from test_automation.models import CaseManifest, VerificationRunRequest


def test_case_manifest_requires_semver_and_safe_package_id():
    manifest = CaseManifest(id="anhui.minute-collection", version="1.0.0", name="分钟采集")
    assert manifest.entry_plan == "plan.json"
    with pytest.raises(ValidationError):
        CaseManifest(id="../unsafe", version="1.0", name="bad")


def test_verification_run_request_rejects_empty_parameters():
    with pytest.raises(ValidationError):
        VerificationRunRequest(case_id="anhui.minute-collection", parameters={})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_models.py -v`  
Expected: FAIL because package `test_automation` does not yet exist.

- [ ] **Step 3: Implement the complete model contract**

```python
# libs/test_automation/models.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
import re

from pydantic import BaseModel, Field, field_validator, model_validator

Verdict = Literal["passed", "failed", "inconclusive", "aborted"]
_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class FirmwareInfo(BaseModel):
    version: str = ""
    commit: str = ""
    flash_file_sha256: str = ""


class ResourceRequest(BaseModel):
    id: str
    mode: Literal["exclusive", "observer"] = "exclusive"


class CaseManifest(BaseModel):
    id: str
    version: str
    name: str
    entry_plan: str = "plan.json"
    source: dict[str, str] = Field(default_factory=dict)
    required_resources: list[ResourceRequest] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not _PACKAGE_ID.fullmatch(value):
            raise ValueError("case id must contain lowercase letters, digits, dots, or hyphens")
        return value

    @field_validator("version")
    @classmethod
    def semver(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("version must use MAJOR.MINOR.PATCH")
        return value


class AssertionSpec(BaseModel):
    id: str
    type: Literal["frame", "event", "event_absent", "roster_coverage", "aggregation"]
    within_ms: int = Field(default=5000, ge=1, le=3_600_000)
    expected: dict[str, Any] = Field(default_factory=dict)
    required: bool = True


class PlanStep(BaseModel):
    id: str
    action: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    expect: list[AssertionSpec] = Field(default_factory=list)
    dangerous: bool = False


class CasePlan(BaseModel):
    id: str
    case_id: str
    setup: list[PlanStep] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    cleanup: list[PlanStep] = Field(default_factory=list)


class Evidence(BaseModel):
    evidence_id: str
    run_id: str
    step_id: str
    source: Literal["simcon", "listener", "module_log", "device", "system"]
    kind: str
    captured_at: datetime
    monotonic_ns: int
    correlation: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str = ""


class AssertionResult(BaseModel):
    id: str
    verdict: Literal["pass", "fail", "inconclusive"]
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class VerificationRunRequest(BaseModel):
    case_id: str
    case_version: str | None = None
    parameters: dict[str, Any]
    roster: dict[str, Any] | None = None
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    requirement_id: str = ""
    commit_sha: str = ""
    allow_dangerous_actions: bool = False

    @model_validator(mode="after")
    def require_parameters(self):
        if not self.parameters:
            raise ValueError("parameters must not be empty")
        return self


class VerificationRunReport(BaseModel):
    run_id: str
    case_id: str
    case_version: str
    verdict: Verdict
    assertions: list[AssertionResult] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    requirement_id: str = ""
    commit_sha: str = ""
```

```python
# libs/test_automation/__init__.py
"""Versioned, evidence-first verification automation domain."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_models.py -v`  
Expected: PASS with 2 passed tests.

- [ ] **Step 5: Commit**

```powershell
git add libs/test_automation
git commit -m "feat: add verification automation domain models"
```

### Task 2: Implement package discovery, validation, and immutable fingerprints

**Files:**

- Create: `libs/test_automation/catalog.py`
- Create: `libs/test_automation/test_catalog.py`
- Create: `test_assets/cases/anhui-minute-collection/case.json`
- Create: `test_assets/cases/anhui-minute-collection/plan.json`

**Interfaces:**

- Consumes: `CaseManifest` and `CasePlan` from Task 1.
- Produces: `CasePackage`, `CaseCatalog.list_cases()`, and `CaseCatalog.load(case_id, version=None)`.

- [ ] **Step 1: Write failing catalog tests**

```python
from pathlib import Path
import pytest
from test_automation.catalog import CaseCatalog, CasePackageError


def test_catalog_loads_versioned_case_package(tmp_path: Path):
    package = tmp_path / "case"; package.mkdir()
    (package / "case.json").write_text('{"id":"demo.query","version":"1.0.0","name":"Demo"}', encoding="utf-8")
    (package / "plan.json").write_text('{"id":"demo.query.v1","case_id":"demo.query","steps":[]}', encoding="utf-8")
    case = CaseCatalog(tmp_path).load("demo.query")
    assert case.manifest.id == "demo.query"
    assert len(case.content_sha256) == 64


def test_catalog_rejects_plan_for_another_case(tmp_path: Path):
    package = tmp_path / "case"; package.mkdir()
    (package / "case.json").write_text('{"id":"demo.query","version":"1.0.0","name":"Demo"}', encoding="utf-8")
    (package / "plan.json").write_text('{"id":"wrong.v1","case_id":"wrong.case","steps":[]}', encoding="utf-8")
    with pytest.raises(CasePackageError, match="case_id"):
        CaseCatalog(tmp_path).load("demo.query")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_catalog.py -v`  
Expected: FAIL because `test_automation.catalog` does not exist.

- [ ] **Step 3: Implement deterministic discovery and hashing**

```python
# Required public implementation in libs/test_automation/catalog.py
@dataclass(frozen=True)
class CasePackage:
    root: Path
    manifest: CaseManifest
    plan: CasePlan
    content_sha256: str


class CasePackageError(ValueError):
    pass


class CaseCatalog:
    def __init__(self, root: Path): ...
    def list_cases(self) -> list[CaseManifest]: ...
    def load(self, case_id: str, version: str | None = None) -> CasePackage: ...

    # Load exactly case.json and manifest.entry_plan with json.loads.
    # Resolve both paths then reject any file whose resolved path is outside package root.
    # Use CaseManifest.model_validate and CasePlan.model_validate.
    # Reject plan.case_id != manifest.id.
    # SHA-256 must be calculated from sorted relative paths and bytes for every regular file in the package.
```

Create the initial package with `case.json`:

```json
{
  "id": "anhui.minute-collection",
  "version": "1.0.0",
  "name": "安徽分钟采集全链路验证",
  "source": {"system": "GW-CASS", "category": "安徽扩展"},
  "required_resources": [
    {"id": "simcon.cco", "mode": "exclusive"},
    {"id": "listener.hplc", "mode": "observer"},
    {"id": "module-log.cco", "mode": "observer"}
  ]
}
```

Create `plan.json` with an empty but schema-valid `setup`, `steps`, and `cleanup` list. Task 6 replaces this plan with executable actions after the executor exists.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_catalog.py -v`  
Expected: PASS with 2 passed tests.

- [ ] **Step 5: Commit**

```powershell
git add libs/test_automation/catalog.py libs/test_automation/test_catalog.py test_assets/cases
git commit -m "feat: add declarative verification case catalog"
```

### Task 3: Add resource leases and append-only evidence storage

**Files:**

- Create: `libs/test_automation/resources.py`
- Create: `libs/test_automation/evidence.py`
- Create: `libs/test_automation/test_resources.py`
- Create: `libs/test_automation/test_evidence.py`

**Interfaces:**

- Consumes: `ResourceRequest` and `Evidence` from Task 1.
- Produces: `ResourceLeaseManager.acquire_all()`, `ResourceLease.release()`, and `EvidenceStore.append()/freeze()/read_all()`.

- [ ] **Step 1: Write failing isolation tests**

```python
import pytest
from test_automation.resources import ResourceBusyError, ResourceLeaseManager


def test_exclusive_lease_blocks_second_owner():
    leases = ResourceLeaseManager()
    first = leases.acquire_all("run-1", ["simcon.cco"])
    with pytest.raises(ResourceBusyError, match="simcon.cco"):
        leases.acquire_all("run-2", ["simcon.cco"])
    first.release()
    leases.acquire_all("run-2", ["simcon.cco"])
```

```python
from datetime import datetime, timezone
from test_automation.evidence import EvidenceStore
from test_automation.models import Evidence


def test_frozen_evidence_store_refuses_late_append(tmp_path):
    store = EvidenceStore(tmp_path, "run-1")
    store.append(Evidence(evidence_id="e1", run_id="run-1", step_id="s1", source="system", kind="start", captured_at=datetime.now(timezone.utc), monotonic_ns=1))
    store.freeze()
    assert [e.evidence_id for e in store.read_all()] == ["e1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_resources.py libs/test_automation/test_evidence.py -v`  
Expected: FAIL because the resource and evidence modules do not yet exist.

- [ ] **Step 3: Implement resource and evidence semantics**

```python
# Required behavior
class ResourceBusyError(RuntimeError):
    pass

class ResourceLease:
    def __init__(self, manager: "ResourceLeaseManager", run_id: str, resource_ids: list[str]): ...
    def release(self) -> None: ...
    def __enter__(self) -> "ResourceLease": return self
    def __exit__(self, *_: object) -> None: self.release()

class ResourceLeaseManager:
    def acquire_all(self, run_id: str, resource_ids: list[str]) -> ResourceLease:
        # Hold a threading.Lock; sort and de-duplicate IDs before testing availability.
        # On conflict, leave no partial acquisition behind and raise ResourceBusyError.
        ...

class EvidenceStore:
    def __init__(self, root: Path, run_id: str): ...
    @property
    def path(self) -> Path: ...  # root / run_id / "evidence.jsonl"
    def append(self, evidence: Evidence) -> None: ...
    def freeze(self) -> Path: ...
    def read_all(self) -> list[Evidence]: ...

# append() writes one sorted-key JSON line, flushes it, and rejects evidence whose run_id differs.
# freeze() writes root/run_id/manifest.json containing the run_id, evidence count, and SHA-256 of evidence.jsonl.
# append() raises RuntimeError("evidence store is frozen") after freeze().
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_resources.py libs/test_automation/test_evidence.py -v`  
Expected: PASS with 2 passed tests.

- [ ] **Step 5: Commit**

```powershell
git add libs/test_automation/resources.py libs/test_automation/evidence.py libs/test_automation/test_resources.py libs/test_automation/test_evidence.py
git commit -m "feat: add verification resource leases and evidence storage"
```

### Task 4: Define source adapters and a real-time evidence window

**Files:**

- Create: `libs/test_automation/adapters/__init__.py`
- Create: `libs/test_automation/adapters/base.py`
- Create: `libs/test_automation/adapters/simcon.py`
- Create: `libs/test_automation/adapters/listener.py`
- Create: `libs/test_automation/adapters/loghooks.py`
- Create: `libs/test_automation/test_adapters.py`

**Interfaces:**

- Consumes: `EvidenceStore` from Task 3 and existing `sim_concentrator`, `loghooks`, and listener service interfaces.
- Produces: `EvidenceSource.start(run_id)`, `EvidenceSource.stop()`, `EvidenceSource.drain()`, and `SimconAdapter.send(action, step_id)`.

- [ ] **Step 1: Write failing adapter tests using fake sources**

```python
from datetime import datetime, timezone
from test_automation.adapters.base import BufferedEvidenceSource
from test_automation.evidence import EvidenceStore


def test_source_only_emits_records_created_inside_run_window(tmp_path):
    source = BufferedEvidenceSource("listener")
    source.publish("frame", {"afn": "03"}, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    store = EvidenceStore(tmp_path, "run-1")
    source.start("run-1")
    source.publish("frame", {"afn": "E4"})
    source.drain_into(store, "collect")
    assert [e.payload["afn"] for e in store.read_all()] == ["E4"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_adapters.py -v`  
Expected: FAIL because `BufferedEvidenceSource` does not exist.

- [ ] **Step 3: Implement the adapter boundary**

```python
# libs/test_automation/adapters/base.py
class EvidenceSource(Protocol):
    name: str
    def start(self, run_id: str) -> None: ...
    def stop(self) -> None: ...
    def drain_into(self, store: EvidenceStore, step_id: str) -> int: ...

class BufferedEvidenceSource:
    # Keep (created_monotonic_ns, kind, payload, correlation) records under a Lock.
    # start() records the current monotonic_ns and clears only records older than the start boundary.
    # drain_into() converts records created after start() to Evidence and does not duplicate drained records.
    ...
```

Implement adapters as thin wrappers:

- `SimconAdapter` records every outgoing and incoming frame as `source="simcon"`, with parsed frame fields and `task_no`, `sta_mac`, and `nid` when available.
- `ListenerEvidenceAdapter` receives already-indexed or live listener frames and records `source="listener"`; it must never open the same serial port held by `SimconAdapter`.
- `LoghooksEvidenceAdapter` receives `loghooks.engine.Event` values and records source `module_log`, raw-line reference, rule ID, event type, and captures.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_adapters.py libs/sim_concentrator/test_runner.py libs/loghooks/test_loghooks.py -v`  
Expected: PASS; existing simulator and loghooks tests remain green.

- [ ] **Step 5: Commit**

```powershell
git add libs/test_automation/adapters libs/test_automation/test_adapters.py
git commit -m "feat: add live verification evidence adapters"
```

### Task 5: Implement assertions for frames, events, and STA roster coverage

**Files:**

- Create: `libs/test_automation/assertions.py`
- Create: `libs/test_automation/test_assertions.py`

**Interfaces:**

- Consumes: `AssertionSpec`, `Evidence`, and `AssertionResult` from Tasks 1 and 3.
- Produces: `evaluate_assertion(spec, evidence, roster=None) -> AssertionResult` and `evaluate_all(specs, evidence, roster=None) -> list[AssertionResult]`.

- [ ] **Step 1: Write failing behavior tests**

```python
from test_automation.assertions import evaluate_assertion
from test_automation.models import AssertionSpec, Evidence
from datetime import datetime, timezone


def _ev(mac: str, kind: str = "frame") -> Evidence:
    return Evidence(evidence_id=mac, run_id="r", step_id="s", source="listener", kind=kind,
                    captured_at=datetime.now(timezone.utc), monotonic_ns=1,
                    correlation={"sta_mac": mac, "task_no": "7"}, payload={"afn": "E4"})


def test_roster_coverage_lists_missing_sta():
    spec = AssertionSpec(id="reports", type="roster_coverage", expected={"correlation_key":"sta_mac", "task_no":"7"})
    result = evaluate_assertion(spec, [_ev("A")], roster={"stas":[{"mac":"A","enabled":True},{"mac":"B","enabled":True}]})
    assert result.verdict == "fail"
    assert result.actual["missing"] == ["B"]


def test_event_absent_passes_when_forbidden_event_is_missing():
    spec = AssertionSpec(id="no-error", type="event_absent", expected={"event_type":"collect.minute.parse_error"})
    result = evaluate_assertion(spec, [_ev("A")])
    assert result.verdict == "pass"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_assertions.py -v`  
Expected: FAIL because `test_automation.assertions` does not exist.

- [ ] **Step 3: Implement each assertion deterministically**

```python
# Required assertion matching rules
# frame: match evidence.kind == "frame" and every key in spec.expected["fields"] equals evidence.payload[key].
# event: match evidence.kind == "event" and evidence.payload["event_type"] equals spec.expected["event_type"].
# event_absent: pass only when no matching event exists.
# roster_coverage: use enabled roster stas only; compare unique correlation_key values, return actual={"expected": [...], "observed": [...], "missing": [...], "unexpected": [...]}.
# aggregation: compare unique STA values in spec.expected["source_kind"] and spec.expected["target_kind"].
# Any assertion with zero evidence from a required source returns inconclusive rather than fail.
```

Do not implement protocol byte parsing in this module. It only evaluates normalized evidence emitted by adapters.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_assertions.py -v`  
Expected: PASS with 2 passed tests.

- [ ] **Step 5: Commit**

```powershell
git add libs/test_automation/assertions.py libs/test_automation/test_assertions.py
git commit -m "feat: add evidence assertions and roster coverage"
```

### Task 6: Build the evidence-first verification executor

**Files:**

- Create: `libs/test_automation/executor.py`
- Create: `libs/test_automation/test_executor.py`
- Modify: `apps/workbench/orchestration/models.py`

**Interfaces:**

- Consumes: package catalog, resource leases, evidence sources, assertion evaluator, and existing `FirmwareInfo` semantics.
- Produces: `VerificationExecutor.execute(request) -> VerificationRunReport`; never use `apps/workbench/orchestration/runner.py` for this new path.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_executor_starts_sources_before_simcon_stimulus(tmp_path, fake_case, fake_sources, fake_actions):
    report = VerificationExecutor(fake_case, fake_sources, fake_actions, artifact_root=tmp_path).execute(fake_request)
    assert fake_actions.calls == ["evidence.start", "simcon.send", "evidence.freeze"]
    assert report.verdict == "passed"


def test_executor_returns_inconclusive_when_listener_has_no_window_evidence(tmp_path, fake_case, fake_sources, fake_actions):
    fake_sources.listener.enabled = False
    report = VerificationExecutor(fake_case, fake_sources, fake_actions, artifact_root=tmp_path).execute(fake_request)
    assert report.verdict == "inconclusive"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_executor.py -v`  
Expected: FAIL because `VerificationExecutor` does not exist.

- [ ] **Step 3: Implement the exact lifecycle**

```python
class VerificationExecutor:
    def execute(self, request: VerificationRunRequest) -> VerificationRunReport:
        # 1. load the exact package version and validate parameters/roster
        # 2. acquire all manifest resources in sorted order
        # 3. create EvidenceStore and call source.start(run_id) for every source
        # 4. record system evidence kind="run.start"
        # 5. run setup and steps; before/after every action call drain_into(store, step.id)
        # 6. deny dangerous action unless request.allow_dangerous_actions is true
        # 7. always run cleanup in reverse order, even after assertion/action failure
        # 8. drain sources one final time, stop sources, freeze the EvidenceStore
        # 9. evaluate assertions only over frozen EvidenceStore.read_all()
        # 10. select verdict: aborted for cancellation; failed for a decisive failure;
        #     inconclusive when a required assertion is inconclusive; passed only otherwise
        # 11. release resource lease in finally
        ...
```

Extend `apps/workbench/orchestration/models.py` only with additive compatibility fields:

```python
class Run(BaseModel):
    # retain all existing fields
    verification_case_id: str = ""
    verification_case_version: str = ""
    verification_verdict: str = ""
    evidence_manifest_path: str = ""
```

Do not change the old `RunExecutor` ordering in this task; it remains a legacy scenario path until a dedicated migration task proves parity.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_executor.py apps/workbench/test_orchestration.py -v`  
Expected: PASS; the old orchestration tests retain their existing behavior.

- [ ] **Step 5: Commit**

```powershell
git add libs/test_automation/executor.py libs/test_automation/test_executor.py apps/workbench/orchestration/models.py
git commit -m "feat: execute evidence-first verification runs"
```

### Task 7: Implement the first real case package: Anhui minute collection

**Files:**

- Modify: `test_assets/cases/anhui-minute-collection/plan.json`
- Create: `test_assets/cases/anhui-minute-collection/parameters.schema.json`
- Create: `test_assets/cases/anhui-minute-collection/roster.schema.json`
- Create: `test_assets/cases/anhui-minute-collection/listener.filters.json`
- Create: `test_assets/cases/anhui-minute-collection/loghooks.rules.json`
- Create: `test_assets/cases/anhui-minute-collection/fixtures/fake-roster.json`
- Create: `libs/test_automation/test_minute_collection_case.py`

**Interfaces:**

- Consumes: executor and normalized evidence from Task 6.
- Produces: a complete package proving configuration dispatch → all STA reports → CCO forward report → evidence-backed verdict.

- [ ] **Step 1: Write a failing golden-flow test**

```python
def test_minute_collection_case_requires_every_enabled_sta(tmp_path, minute_case, fake_environment):
    fake_environment.emit_config_for("001122334455")
    fake_environment.emit_config_for("001122334466")
    fake_environment.emit_sta_report("001122334455", task_no="7")
    fake_environment.emit_cco_forward(["001122334455"])
    report = run_case(minute_case, fake_environment, tmp_path)
    assert report.verdict == "failed"
    coverage = next(a for a in report.assertions if a.id == "sta-minute-coverage")
    assert coverage.actual["missing"] == ["001122334466"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_minute_collection_case.py -v`  
Expected: FAIL because the package plan and its action mapping do not exist.

- [ ] **Step 3: Create executable package content**

`parameters.schema.json` must require `task_no` (string), `period_minutes` (integer 1–1440), and `cco_address` (string). `roster.schema.json` must require non-empty `stas`; every enabled STA must contain unique 12-hex-digit `mac` and a `tei`.

Use the following plan shape in `plan.json`:

```json
{
  "id": "anhui.minute-collection.v1",
  "case_id": "anhui.minute-collection",
  "setup": [
    {"id":"start-evidence","action":"evidence.start"},
    {"id":"flush-ports","action":"device.flush"}
  ],
  "steps": [
    {"id":"configure-cco","action":"simcon.send_minute_config","with":{"task_no":"${parameters.task_no}"},"expect":[{"id":"config-ack","type":"frame","expected":{"fields":{"kind":"config_ack"}}}]},
    {"id":"distribute-to-stas","action":"evidence.wait","with":{"kind":"config_dispatch"},"expect":[{"id":"config-coverage","type":"roster_coverage","within_ms":30000,"expected":{"correlation_key":"sta_mac","kind":"config_dispatch"}}]},
    {"id":"sta-minute-report","action":"evidence.wait","with":{"kind":"minute_report"},"expect":[{"id":"sta-minute-coverage","type":"roster_coverage","within_ms":90000,"expected":{"correlation_key":"sta_mac","kind":"minute_report","task_no":"${parameters.task_no}"}},{"id":"no-parse-error","type":"event_absent","expected":{"event_type":"collect.minute.parse_error"}}]},
    {"id":"cco-forward-report","action":"simcon.wait_report","expect":[{"id":"cco-forward","type":"frame","within_ms":30000,"expected":{"fields":{"kind":"cco_minute_report"}}},{"id":"forward-aggregation","type":"aggregation","expected":{"source_kind":"minute_report","target_kind":"cco_minute_report","correlation_key":"sta_mac"}}]}
  ],
  "cleanup": [
    {"id":"freeze-evidence","action":"evidence.freeze"},
    {"id":"release-resources","action":"resource.release"}
  ]
}
```

Implement `simcon.send_minute_config`, `simcon.wait_report`, `evidence.start`, `evidence.wait`, `evidence.freeze`, and `device.flush` in an action registry. In fake tests, each action emits normalized evidence, never raw bytes only.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest libs/test_automation/test_minute_collection_case.py libs/test_automation/test_executor.py -v`  
Expected: PASS; rerun with all two fake STAs reporting and confirm the verdict becomes `passed`.

- [ ] **Step 5: Commit**

```powershell
git add test_assets/cases/anhui-minute-collection libs/test_automation/test_minute_collection_case.py libs/test_automation
git commit -m "feat: add minute collection evidence verification case"
```

### Task 8: Expose catalog and verification Runs through workbench APIs

**Files:**

- Create: `apps/workbench/automation_service.py`
- Create: `apps/workbench/automation_api.py`
- Modify: `apps/workbench/app.py`
- Create: `apps/workbench/test_automation_api.py`

**Interfaces:**

- Consumes: `CaseCatalog` and `VerificationExecutor`.
- Produces: `GET /api/cases`, `GET /api/cases/{case_id}`, `POST /api/verification-runs`, and `GET /api/verification-runs/{run_id}/report`.

- [ ] **Step 1: Write failing FastAPI tests**

```python
def test_list_case_packages(client):
    response = client.get("/api/cases")
    assert response.status_code == 200
    assert any(c["id"] == "anhui.minute-collection" for c in response.json())


def test_start_verification_run_returns_evidence_report(client):
    response = client.post("/api/verification-runs", json={
        "case_id": "anhui.minute-collection",
        "parameters": {"task_no": "7", "period_minutes": 15, "cco_address": "001"},
        "roster": {"stas": [{"mac": "001122334455", "tei": "001", "enabled": True}]}
    })
    assert response.status_code == 200
    assert response.json()["case_id"] == "anhui.minute-collection"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest apps/workbench/test_automation_api.py -v`  
Expected: FAIL with 404 because automation routes are not mounted.

- [ ] **Step 3: Implement API and persistence policy**

```python
# apps/workbench/automation_api.py
router = APIRouter(prefix="/api")

@router.get("/cases")
async def list_cases() -> list[dict]: ...

@router.get("/cases/{case_id}")
async def get_case(case_id: str) -> dict: ...

@router.post("/verification-runs")
async def create_verification_run(request: VerificationRunRequest) -> dict: ...

@router.get("/verification-runs/{run_id}/report")
async def get_verification_report(run_id: str) -> dict: ...
```

`automation_service.py` must store report JSON under the existing workbench runtime report directory as `verification-{run_id}.json`; it must not overwrite an existing report. Mount `automation_api.router` in `create_workbench_app()` after the existing orchestration router.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest apps/workbench/test_automation_api.py apps/workbench/test_app.py -v`  
Expected: PASS; pre-existing workbench routes remain available.

- [ ] **Step 5: Commit**

```powershell
git add apps/workbench/automation_service.py apps/workbench/automation_api.py apps/workbench/app.py apps/workbench/test_automation_api.py
git commit -m "feat: expose verification case and run APIs"
```

### Task 9: Add the Automation Verification page and report evidence drill-down

**Files:**

- Create: `apps/workbench/static/pages/automation/index.html`
- Create: `apps/workbench/static/pages/automation/app.js`
- Create: `apps/workbench/static/pages/automation/styles.css`
- Modify: `apps/workbench/static/index.html`
- Modify: `apps/workbench/static/app.js`
- Modify: `apps/workbench/static/styles.css`
- Create: `apps/workbench/test_automation_ui.py`

**Interfaces:**

- Consumes: APIs from Task 8.
- Produces: a case catalog, parameter/roster form, Run timeline, assertion table, and evidence references.

- [ ] **Step 1: Write failing HTML integration tests**

```python
def test_workbench_shell_registers_automation_page():
    text = Path("apps/workbench/static/index.html").read_text(encoding="utf-8")
    assert 'data-page="automation"' in text
    js = Path("apps/workbench/static/app.js").read_text(encoding="utf-8")
    assert 'pages/automation/index.html' in js


def test_automation_page_uses_verification_run_api():
    js = Path("apps/workbench/static/pages/automation/app.js").read_text(encoding="utf-8")
    assert 'fetch("/api/cases")' in js
    assert 'fetch("/api/verification-runs"' in js
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest apps/workbench/test_automation_ui.py -v`  
Expected: FAIL because the page and navigation entry do not exist.

- [ ] **Step 3: Implement the minimum useful page**

The page must have four accessible regions with stable IDs: `case-catalog`, `run-config`, `run-timeline`, and `verification-report`. Render a badge for every case’s automation status and a red/amber/green badge for Run verdict. Render the STA coverage table before the raw evidence list. Every evidence ID must be a button that shows `source`, `step_id`, `captured_at`, `correlation`, `payload`, and `raw_ref` without deleting or transforming the raw evidence.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest apps/workbench/test_automation_ui.py apps/workbench/test_app.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/workbench/static apps/workbench/test_automation_ui.py
git commit -m "feat: add automation verification workbench page"
```

### Task 10: Add the AI change-to-verification audit contract

**Files:**

- Create: `apps/workbench/ai_validation/models.py`
- Create: `apps/workbench/ai_validation/service.py`
- Create: `apps/workbench/test_ai_validation.py`
- Modify: `apps/workbench/automation_api.py`

**Interfaces:**

- Consumes: `VerificationRunRequest` and `VerificationRunReport`.
- Produces: `ChangeProposal`, `ApprovalState`, `create_proposal()`, `approve_proposal()`, and `attach_run()`; it does not directly invoke a model provider in this milestone.

- [ ] **Step 1: Write failing audit tests**

```python
from apps.workbench.ai_validation.service import ChangeValidationService


def test_hardware_run_cannot_attach_before_proposal_is_approved(tmp_path):
    service = ChangeValidationService(tmp_path)
    proposal = service.create_proposal(requirement_id="REQ-42", summary="修复分钟采集任务号", files=["libs/parser_lib/x.py"])
    with pytest.raises(PermissionError):
        service.attach_run(proposal.id, "run-real-hardware", hardware=True)
    service.approve_proposal(proposal.id, approver="tester")
    assert service.attach_run(proposal.id, "run-real-hardware", hardware=True).run_ids == ["run-real-hardware"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest apps/workbench/test_ai_validation.py -v`  
Expected: FAIL because the audit package does not exist.

- [ ] **Step 3: Implement auditable, provider-neutral AI collaboration**

```python
class ChangeProposal(BaseModel):
    id: str
    requirement_id: str
    summary: str
    files: list[str]
    approval: Literal["draft", "approved", "rejected"] = "draft"
    run_ids: list[str] = Field(default_factory=list)

class ChangeValidationService:
    def create_proposal(self, requirement_id: str, summary: str, files: list[str]) -> ChangeProposal: ...
    def approve_proposal(self, proposal_id: str, approver: str) -> ChangeProposal: ...
    def attach_run(self, proposal_id: str, run_id: str, hardware: bool) -> ChangeProposal: ...
```

Store proposal JSON in the workbench runtime directory. The later model-provider adapter may create proposals, but it must use this service and cannot bypass approval for hardware Runs. Add an optional `proposal_id` to `VerificationRunRequest` and include it in reports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest apps/workbench/test_ai_validation.py apps/workbench/test_automation_api.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/workbench/ai_validation apps/workbench/test_ai_validation.py apps/workbench/automation_api.py
git commit -m "feat: audit AI change proposals and verification runs"
```

## System Acceptance Run

After Tasks 1–10 are complete, execute the following in sequence:

1. Run the entire unit suite:

```powershell
.venv\Scripts\python.exe -m pytest apps/workbench libs/test_automation libs/sim_concentrator libs/loghooks apps/listener apps/module_log -v
```

Expected: all unit and fake-adapter tests pass; hardware-dependent tests remain explicitly skipped unless a configured lab environment is present.

2. Start the workbench and execute the fake minute-collection Run from the API.

```powershell
.venv\Scripts\python.exe -m uvicorn workbench.app:app --host 127.0.0.1 --port 8790
```

Expected: `GET /api/cases` lists `anhui.minute-collection`; a Run with a missing STA yields `failed`; the same Run with all STA reports yields `passed`; an unavailable listener source yields `inconclusive`.

3. Perform one controlled hardware Run only after a proposal is approved and a device operator confirms the test topology. Acceptance evidence must include the immutable evidence manifest, the roster coverage result, the simulator TX/RX records, listener frames, module-log events, report JSON, and the linked proposal/commit identifiers.

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 provide loadable versioned test cases; Tasks 3–6 establish evidence-first execution; Task 7 implements the requested minute-collection proof; Tasks 8–9 integrate the unified workbench; Task 10 makes the AI requirement-to-verification chain auditable.
- **Placeholder scan:** The plan intentionally does not rely on unbounded external model calls, arbitrary uploaded Python, implicit global state, or undefined “later” interfaces. The only deferred work is a provider adapter, which is explicitly bounded by Task 10’s existing `ChangeValidationService` contract.
- **Type consistency:** `CaseManifest`, `CasePlan`, `VerificationRunRequest`, `Evidence`, `AssertionResult`, and `VerificationRunReport` are introduced in Task 1 and used unchanged in every later task.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-15-ai-driven-evidence-verification-platform.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh implementer and reviewer per task, with an integration checkpoint after Tasks 3, 6, and 8.
2. **Inline Execution** — implement tasks in this session using the specified test-first order, stopping at the same integration checkpoints.
