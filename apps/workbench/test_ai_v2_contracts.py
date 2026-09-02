"""REQS-0021 P1：v2 契约与真实对端访问域测试。"""
from __future__ import annotations

from starlette.requests import Request


def _request(host: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/ai/v2/capabilities",
        "raw_path": b"/api/ai/v2/capabilities",
        "query_string": b"",
        "headers": headers or [],
        "client": (host, 53100),
        "server": ("testserver", 80),
    })


def test_loopback_with_explicit_flag_resolves_to_local_full():
    from workbench.ai_access import resolve_access_context

    context = resolve_access_context(_request("127.0.0.1"), local_full_enabled=True)

    assert context.zone == "local_full"
    assert context.actor == "local:loopback"


def test_ipv6_loopback_with_explicit_flag_resolves_to_local_full():
    from workbench.ai_access import resolve_access_context

    assert resolve_access_context(_request("::1"), local_full_enabled=True).zone == "local_full"


def test_loopback_without_explicit_flag_stays_lan_scoped():
    from workbench.ai_access import resolve_access_context

    assert resolve_access_context(_request("127.0.0.1"), local_full_enabled=False).zone == "lan_scoped"


def test_forwarded_headers_never_upgrade_a_remote_peer():
    from workbench.ai_access import resolve_access_context

    context = resolve_access_context(
        _request("192.168.1.20", [(b"host", b"localhost"), (b"x-forwarded-for", b"127.0.0.1")]),
        local_full_enabled=True,
    )

    assert context.zone == "lan_scoped"
    assert context.actor == "lan:pending_grant"


def test_contract_models_are_named_and_non_observation_jobs_have_no_verdict():
    from workbench.ai_contracts import CapabilitySnapshot, JobEnvelope, SourceHealth

    schema = CapabilitySnapshot.model_json_schema()

    assert "Capability" in schema["$defs"]
    assert "AccessContext" in schema["$defs"]
    health_values = schema["properties"]["source_health"]["additionalProperties"]
    assert health_values == {"$ref": "#/$defs/SourceHealth"}
    snapshot = CapabilitySnapshot(
        capability_revision="ai-v2-p1",
        access={"zone": "local_full", "actor": "local:loopback"},
        capabilities=[],
        resource_aliases=[],
        source_health={"module_log": SourceHealth(available=True)},
    )
    assert snapshot.source_health["module_log"].available is True
    assert JobEnvelope(job_id="job-p1", job_state="succeeded").verdict is None
