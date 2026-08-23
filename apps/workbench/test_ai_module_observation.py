"""Module-log observation matcher and bounded cursor tests."""
from __future__ import annotations

from datetime import datetime

import pytest

from workbench.ai_operations import AIControlService, InvalidObservation
from workbench.test_ai_operations import FakeModuleService


def _service_with(*lines: dict) -> AIControlService:
    module = FakeModuleService()
    module.lines = list(lines) or module.lines
    return AIControlService(module_service=module)


def _line(seq: int, text: str, ts: str = "2026-08-23T12:00:00+00:00") -> dict:
    return {"seq": seq, "ts": ts, "dir": "RX", "text": text}


def _request(match: dict, window: dict) -> dict:
    return {
        "source": "module_log",
        "target": {"session_id": "ms-cco"},
        "window": window,
        "match": match,
        "context": {"before": 1, "after": 1},
    }


def test_regex_observation_matches_with_bounded_artifact_and_condition():
    service = _service_with(_line(1, "boot"))
    operation = service.create_observation(
        _request(
            {"kind": "regex", "value": r"ready\s+\d+"},
            {"mode": "live", "start": "now", "timeout_seconds": 60},
        ),
        actor="ai:grant-test",
        client_request_id="regex-1",
    )
    service.module_service.lines.append(_line(2, "ready 42"))

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "matched"
    assert result["result"]["condition_met"] is True
    assert result["result"]["log"]["match_lines"] == [2]
    artifact_id = result["result"]["log"]["artifact_id"]
    assert service.read_artifact(artifact_id)["content"]["condition_met"] is True
    assert service.create_observation(
        _request(
            {"kind": "regex", "value": r"ready\s+\d+"},
            {"mode": "live", "start": "now", "timeout_seconds": 60},
        ),
        actor="ai:grant-test",
        client_request_id="regex-1",
    )["operation_id"] == operation["operation_id"]


def test_loghook_rule_observation_reuses_module_scoped_rule_engine():
    service = _service_with(_line(1, "boot"))
    operation = service.create_observation(
        _request(
            {"kind": "loghook_rule", "rule_id": "common.join_onnet"},
            {"mode": "live", "start": "now", "timeout_seconds": 60},
        ),
        actor="ai:grant-test",
    )
    service.module_service.lines.append(_line(2, "onnet cnt = 12"))

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "matched"
    assert result["result"]["log"]["match_lines"] == [2]


@pytest.mark.parametrize(
    "match",
    [
        {"kind": "unknown", "value": "x"},
        {"kind": "regex", "value": "x" * 257},
        {"kind": "sequence", "steps": [{"kind": "literal", "value": "a"}]},
        {"kind": "not_seen", "matcher": {"kind": "sequence", "steps": []}},
    ],
)
def test_module_observation_rejects_invalid_matcher_shapes(match):
    service = _service_with(_line(1, "boot"))

    with pytest.raises(InvalidObservation):
        service.create_observation(
            _request(match, {"mode": "live", "start": "now", "timeout_seconds": 60}),
            actor="ai:grant-test",
        )


def test_sequence_matches_ordered_steps_within_maximum_interval():
    service = _service_with(
        _line(2, "begin", "2026-08-23T12:00:00.000+00:00"),
        _line(3, "finish", "2026-08-23T12:00:00.400+00:00"),
    )

    result = service.create_observation(
        _request(
            {
                "kind": "sequence",
                "steps": [
                    {"kind": "literal", "value": "begin"},
                    {"kind": "regex", "value": "finish"},
                ],
                "max_interval_ms": 500,
            },
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "matched"
    assert result["result"]["log"]["match_lines"] == [2, 3]


def test_sequence_completion_count_counts_completed_sequences_not_its_steps():
    service = _service_with(
        _line(2, "begin", "2026-08-23T12:00:00.000+00:00"),
        _line(3, "finish", "2026-08-23T12:00:00.400+00:00"),
    )

    result = service.create_observation(
        {
            **_request(
                {
                    "kind": "sequence",
                    "steps": [
                        {"kind": "literal", "value": "begin"},
                        {"kind": "literal", "value": "finish"},
                    ],
                    "max_interval_ms": 500,
                },
                {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
            ),
            "completion": {"match_count": 2},
        },
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False


@pytest.mark.parametrize(
    "lines,max_interval_ms",
    [
        (
            [
                _line(2, "finish", "2026-08-23T12:00:00.000+00:00"),
                _line(3, "begin", "2026-08-23T12:00:00.100+00:00"),
            ],
            500,
        ),
        (
            [
                _line(2, "begin", "2026-08-23T12:00:00.000+00:00"),
                _line(3, "finish", "2026-08-23T12:00:01.000+00:00"),
            ],
            500,
        ),
    ],
)
def test_cursor_sequence_reports_false_for_wrong_order_or_interval(lines, max_interval_ms):
    service = _service_with(*lines)

    result = service.create_observation(
        _request(
            {
                "kind": "sequence",
                "steps": [
                    {"kind": "literal", "value": "begin"},
                    {"kind": "literal", "value": "finish"},
                ],
                "max_interval_ms": max_interval_ms,
            },
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False
    assert result["result"]["log"]["artifact_id"].startswith("art-")


def test_not_seen_live_without_trusted_deadline_coverage_times_out(monkeypatch):
    import workbench.ai_operations as operations

    service = _service_with(_line(1, "boot"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.0)
    operation = service.create_observation(
        _request(
            {"kind": "not_seen", "matcher": {"kind": "literal", "value": "panic"}},
            {"mode": "live", "start": "now", "timeout_seconds": 1},
        ),
        actor="ai:grant-test",
    )
    monkeypatch.setattr(operations.time, "monotonic", lambda: 102.0)

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "timed_out"
    assert result["result"]["condition_met"] is False
    assert result["result"]["log"]["match_lines"] == []
    assert result["result"]["reason"] == "live_window_unverified"


def test_not_seen_cursor_counterexample_returns_minimal_evidence():
    service = _service_with(_line(2, "panic"))

    result = service.create_observation(
        _request(
            {"kind": "not_seen", "matcher": {"kind": "literal", "value": "panic"}},
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 2},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False
    assert result["result"]["log"]["match_lines"] == [2]
    assert [line["seq"] for line in result["result"]["snippet"]] == [2]


def test_cursor_range_is_closed_and_includes_its_first_and_last_seq():
    service = _service_with(_line(2, "first"), _line(3, "last marker"))

    result = service.create_observation(
        _request(
            {"kind": "literal", "value": "last marker"},
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "matched"
    assert result["result"]["log"]["line_start"] == 3
    assert result["result"]["log"]["line_end"] == 3


def test_time_range_reads_only_the_session_memory_buffer_and_completes_immediately():
    service = _service_with(
        _line(2, "before", "2026-08-23T11:59:59.000+00:00"),
        _line(3, "range marker", "2026-08-23T12:00:00.500+00:00"),
        _line(4, "after", "2026-08-23T12:00:01.000+00:00"),
    )

    result = service.create_observation(
        _request(
            {"kind": "literal", "value": "range marker"},
            {
                "mode": "time_range",
                "start": "2026-08-23T12:00:00.000+00:00",
                "end": "2026-08-23T12:00:00.999+00:00",
            },
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "matched"
    assert result["result"]["log"]["match_lines"] == [3]


def test_time_range_normalises_zone_less_module_clock_as_local_time():
    service = _service_with(_line(3, "local marker", "20260823-20:00:00:500"))
    offset = datetime.now().astimezone().strftime("%z")
    iso_offset = offset[:3] + ":" + offset[3:]

    result = service.create_observation(
        _request(
            {"kind": "literal", "value": "local marker"},
            {
                "mode": "time_range",
                "start": "2026-08-23T20:00:00.500" + iso_offset,
                "end": "2026-08-23T20:00:00.500" + iso_offset,
            },
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "matched"


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-08-23T11:59:59.000+00:00", "2026-08-23T12:00:00.500+00:00"),
        ("2026-08-23T12:00:00.500+00:00", "2026-08-23T12:00:02.000+00:00"),
    ],
)
def test_time_range_rejects_bounds_outside_retained_memory(start, end):
    service = _service_with(_line(3, "marker", "2026-08-23T12:00:00.500+00:00"))

    with pytest.raises(InvalidObservation):
        service.create_observation(
            _request(
                {"kind": "not_seen", "matcher": {"kind": "literal", "value": "panic"}},
                {"mode": "time_range", "start": start, "end": end},
            ),
            actor="ai:grant-test",
        )


def test_empty_cursor_range_condition_is_succeeded_with_false_condition():
    service = _service_with(_line(2, "unrelated"), _line(3, "also unrelated"))

    result = service.create_observation(
        _request(
            {"kind": "literal", "value": "missing"},
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False
    assert result["result"]["snippet"] == []


def test_cursor_range_retry_returns_its_operation_after_the_buffer_advances():
    service = _service_with(_line(2, "marker"))
    request = _request(
        {"kind": "literal", "value": "marker"},
        {"mode": "cursor_range", "start_seq": 2, "end_seq": 2},
    )
    first = service.create_observation(
        request, actor="ai:grant-test", client_request_id="cursor-idempotency-1",
    )
    service.module_service.lines = [_line(100, "new buffer")]

    retried = service.create_observation(
        request, actor="ai:grant-test", client_request_id="cursor-idempotency-1",
    )

    assert retried["operation_id"] == first["operation_id"]


@pytest.mark.parametrize(
    "window,lines",
    [
        ({"mode": "cursor_range", "start_seq": 3, "end_seq": 2}, [_line(2, "a"), _line(3, "b")]),
        ({"mode": "cursor_range", "start_seq": 2, "end_seq": 4}, [_line(2, "a"), _line(3, "b")]),
        ({"mode": "cursor_range", "start_seq": 9, "end_seq": 10}, [_line(10, "retained")]),
        ({"mode": "cursor_range", "start_seq": 1, "end_seq": 10002}, [_line(1, "a")]),
    ],
)
def test_cursor_range_rejects_reverse_out_of_range_trimmed_or_oversized_window(window, lines):
    service = _service_with(*lines)

    with pytest.raises(InvalidObservation):
        service.create_observation(
            _request({"kind": "literal", "value": "a"}, window),
            actor="ai:grant-test",
        )


def test_observation_rejects_any_caller_supplied_file_system_key():
    service = _service_with(_line(1, "boot"))

    with pytest.raises(InvalidObservation):
        service.create_observation(
            {
                **_request(
                    {"kind": "literal", "value": "marker"},
                    {"mode": "live", "start": "now", "timeout_seconds": 60},
                ),
                "root": "C:/caller-controlled",
            },
            actor="ai:grant-test",
        )


@pytest.mark.parametrize(
    "match,late_lines",
    [
        ({"kind": "literal", "value": "late marker"}, [_line(2, "late marker")]),
        ({"kind": "regex", "value": "late\\s+marker"}, [_line(2, "late marker")]),
        (
            {
                "kind": "sequence",
                "steps": [
                    {"kind": "literal", "value": "late begin"},
                    {"kind": "literal", "value": "late finish"},
                ],
                "max_interval_ms": 500,
            },
            [
                _line(2, "late begin", "2026-08-23T12:00:00.000+00:00"),
                _line(3, "late finish", "2026-08-23T12:00:00.100+00:00"),
            ],
        ),
        ({"kind": "loghook_rule", "rule_id": "common.join_onnet"}, [_line(2, "onnet cnt = 12")]),
    ],
)
def test_live_matchers_ignore_lines_first_observed_after_deadline(monkeypatch, match, late_lines):
    import workbench.ai_operations as operations

    service = _service_with(_line(1, "boot"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.0)
    operation = service.create_observation(
        _request(match, {"mode": "live", "start": "now", "timeout_seconds": 1}),
        actor="ai:grant-test",
    )
    service.module_service.lines.extend(late_lines)
    monkeypatch.setattr(operations.time, "monotonic", lambda: 102.0)

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "timed_out"
    assert result["result"]["condition_met"] is False
    assert result["result"]["log"]["match_lines"] == []


def test_not_seen_does_not_claim_absence_when_window_counterexample_is_first_polled_after_deadline(monkeypatch):
    import workbench.ai_operations as operations

    service = _service_with(_line(1, "boot"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.0)
    operation = service.create_observation(
        _request(
            {"kind": "not_seen", "matcher": {"kind": "literal", "value": "panic"}},
            {"mode": "live", "start": "now", "timeout_seconds": 1},
        ),
        actor="ai:grant-test",
    )
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.5)
    service.module_service.lines.append(_line(2, "panic"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 102.0)

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "timed_out"
    assert result["result"]["condition_met"] is False
    assert result["result"]["log"]["match_lines"] == []
    assert result["result"]["reason"] == "live_window_unverified"


def test_not_seen_live_counterexample_polled_before_deadline_ends_false_immediately(monkeypatch):
    import workbench.ai_operations as operations

    service = _service_with(_line(1, "boot"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.0)
    operation = service.create_observation(
        _request(
            {"kind": "not_seen", "matcher": {"kind": "literal", "value": "panic"}},
            {"mode": "live", "start": "now", "timeout_seconds": 1},
        ),
        actor="ai:grant-test",
    )
    service.module_service.lines.append(_line(2, "panic"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.5)

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False
    assert result["result"]["log"]["match_lines"] == [2]


def test_not_seen_closed_cursor_range_is_trusted_coverage_and_matches_absence():
    service = _service_with(_line(2, "normal"), _line(3, "still normal"))

    result = service.create_observation(
        _request(
            {"kind": "not_seen", "matcher": {"kind": "literal", "value": "panic"}},
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "matched"
    assert result["result"]["condition_met"] is True
    assert result["result"]["log"]["match_lines"] == []


def test_sequence_counts_two_non_overlapping_completed_sequences():
    service = _service_with(
        _line(2, "begin", "2026-08-23T12:00:00.000+00:00"),
        _line(3, "finish", "2026-08-23T12:00:00.100+00:00"),
        _line(4, "begin", "2026-08-23T12:00:01.000+00:00"),
        _line(5, "finish", "2026-08-23T12:00:01.100+00:00"),
    )

    result = service.create_observation(
        {
            **_request(
                {
                    "kind": "sequence",
                    "steps": [
                        {"kind": "literal", "value": "begin"},
                        {"kind": "literal", "value": "finish"},
                    ],
                    "max_interval_ms": 500,
                },
                {"mode": "cursor_range", "start_seq": 2, "end_seq": 5},
            ),
            "completion": {"match_count": 2},
        },
        actor="ai:grant-test",
    )

    assert result["state"] == "matched"
    assert result["result"]["log"]["match_lines"] == [2, 3, 4, 5]


def test_sequence_completion_count_does_not_reuse_an_overlapping_step_line():
    service = _service_with(
        _line(2, "begin", "2026-08-23T12:00:00.000+00:00"),
        _line(3, "finish begin", "2026-08-23T12:00:00.100+00:00"),
        _line(4, "finish", "2026-08-23T12:00:00.200+00:00"),
    )

    result = service.create_observation(
        {
            **_request(
                {
                    "kind": "sequence",
                    "steps": [
                        {"kind": "literal", "value": "begin"},
                        {"kind": "literal", "value": "finish"},
                    ],
                    "max_interval_ms": 500,
                },
                {"mode": "cursor_range", "start_seq": 2, "end_seq": 4},
            ),
            "completion": {"match_count": 2},
        },
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False


@pytest.mark.parametrize(
    "request_update",
    [
        {"context": []},
        {"context": {"before": "1"}},
        {"context": {"after": True}},
        {"context": {"before": -1}},
        {"context": {"after": 101}},
        {"completion": []},
        {"completion": {"match_count": "2"}},
        {"completion": {"match_count": True}},
        {"completion": {"match_count": 0}},
        {"completion": {"match_count": 101}},
    ],
)
def test_context_and_completion_reject_invalid_types_or_ranges(request_update):
    service = _service_with(_line(1, "boot"))
    request = {
        **_request(
            {"kind": "literal", "value": "marker"},
            {"mode": "live", "start": "now", "timeout_seconds": 60},
        ),
        **request_update,
    }

    with pytest.raises(InvalidObservation):
        service.create_observation(request, actor="ai:grant-test")


def test_live_sequence_with_incomplete_prefix_times_out_at_deadline(monkeypatch):
    import workbench.ai_operations as operations

    service = _service_with(_line(1, "boot"))
    monkeypatch.setattr(operations.time, "monotonic", lambda: 100.0)
    operation = service.create_observation(
        _request(
            {
                "kind": "sequence",
                "steps": [
                    {"kind": "literal", "value": "begin"},
                    {"kind": "literal", "value": "finish"},
                ],
                "max_interval_ms": 500,
            },
            {"mode": "live", "start": "now", "timeout_seconds": 1},
        ),
        actor="ai:grant-test",
    )
    service.module_service.lines.append(_line(2, "begin"))

    assert service.wait_operation(operation["operation_id"], timeout_seconds=0)["state"] == "waiting"
    monkeypatch.setattr(operations.time, "monotonic", lambda: 102.0)

    result = service.wait_operation(operation["operation_id"], timeout_seconds=0)

    assert result["state"] == "timed_out"
    assert result["result"]["condition_met"] is False


def test_cursor_sequence_rejects_a_trimmed_start_before_evaluating_steps():
    service = _service_with(_line(3, "finish"))

    with pytest.raises(InvalidObservation):
        service.create_observation(
            _request(
                {
                    "kind": "sequence",
                    "steps": [
                        {"kind": "literal", "value": "begin"},
                        {"kind": "literal", "value": "finish"},
                    ],
                    "max_interval_ms": 500,
                },
                {"mode": "cursor_range", "start_seq": 2, "end_seq": 3},
            ),
            actor="ai:grant-test",
        )


def test_regex_rejects_invalid_syntax_and_limits_scanned_text_to_4096_characters():
    service = _service_with(_line(2, "x" * 4096 + " marker"))

    with pytest.raises(InvalidObservation):
        service.create_observation(
            _request(
                {"kind": "regex", "value": "("},
                {"mode": "live", "start": "now", "timeout_seconds": 60},
            ),
            actor="ai:grant-test",
        )

    result = service.create_observation(
        _request(
            {"kind": "regex", "value": "marker"},
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 2},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert result["result"]["condition_met"] is False


def test_regex_timeout_is_treated_as_no_match(monkeypatch):
    import workbench.ai_operations as operations

    calls = []

    class TimeoutPattern:
        def search(self, text, *, timeout):
            calls.append((text, timeout))
            raise TimeoutError

    monkeypatch.setattr(operations.regex, "compile", lambda *args, **kwargs: TimeoutPattern())
    service = _service_with(_line(2, "candidate"))

    result = service.create_observation(
        _request(
            {"kind": "regex", "value": "candidate"},
            {"mode": "cursor_range", "start_seq": 2, "end_seq": 2},
        ),
        actor="ai:grant-test",
    )

    assert result["state"] == "succeeded"
    assert calls == [("candidate", 0.1)]
