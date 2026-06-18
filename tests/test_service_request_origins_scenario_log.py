"""Verify ServiceRequestOriginIndex emits service-call lifecycle events."""

from __future__ import annotations

import json
from datetime import datetime, timezone


from aap_hermes import scenario_log
from aap_hermes.service_request_origins import (
    RequestOrigin,
    ServiceRequestOriginIndex,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _origin(
    *,
    target_address: str = "bookings^dinetable-test",
    group_conv_id: str = "group-root",
) -> RequestOrigin:
    return RequestOrigin(
        platform="example",
        chat_id="user-chat",
        chat_type="dm",
        user_id=None,
        user_name=None,
        thread_id=None,
        chat_name=None,
        target_address=target_address,
        group_conversation_id=group_conv_id,
        created_at=_iso_now(),
    )


def test_record_emits_raw_and_named_service_call_started(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "svc-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    index = ServiceRequestOriginIndex(tmp_path)
    ok = index.record("nonce-1", _origin())
    assert ok

    log_file = tmp_path / "hermes9.jsonl"
    assert log_file.exists()
    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]

    raw = [e for e in entries if e["kind"] == "service_request_sent"]
    named = [e for e in entries if e["kind"] == "service_call_started"]
    assert len(raw) == 1 and raw[0]["layer"] == "raw"
    assert len(named) == 1 and named[0]["layer"] == "named"

    for e in (raw[0], named[0]):
        assert e["data"]["nonce"] == "nonce-1"
        assert e["data"]["service_address"] == "bookings^dinetable-test"
        assert e["parent_conv_id"] == "group-root"


def test_record_no_emit_on_duplicate_nonce(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "svc-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    index = ServiceRequestOriginIndex(tmp_path)
    assert index.record("nonce-dup", _origin())
    assert not index.record("nonce-dup", _origin())

    entries = [
        json.loads(line)
        for line in (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    ]
    started_kinds = [
        e for e in entries if e["kind"] == "service_request_sent"
    ]
    # Only the first record() call should have emitted.
    assert len(started_kinds) == 1


def test_pop_if_emits_response_and_completed_on_match(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "svc-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    index = ServiceRequestOriginIndex(tmp_path)
    target = "bookings^dinetable-test"
    index.record("nonce-2", _origin(target_address=target))

    got = index.pop_if("nonce-2", expected_iss=target)
    assert got is not None

    entries = [
        json.loads(line)
        for line in (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    ]
    raw = [e for e in entries if e["kind"] == "service_response_received"]
    named = [e for e in entries if e["kind"] == "service_call_completed"]
    assert len(raw) == 1 and raw[0]["layer"] == "raw"
    assert len(named) == 1 and named[0]["layer"] == "named"
    for e in (raw[0], named[0]):
        assert e["data"]["nonce"] == "nonce-2"
        assert e["data"]["service_address"] == target
        assert e["parent_conv_id"] == "group-root"


def test_pop_if_no_emit_on_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "svc-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    index = ServiceRequestOriginIndex(tmp_path)
    index.record("nonce-3", _origin(target_address="legit^example"))

    # Wrong issuer — pop_if returns None and must NOT consume or emit.
    got = index.pop_if("nonce-3", expected_iss="forger^example")
    assert got is None

    entries = [
        json.loads(line)
        for line in (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    ]
    response_events = [
        e for e in entries
        if e["kind"] in ("service_response_received", "service_call_completed")
    ]
    assert response_events == []
