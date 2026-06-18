"""Tests for the scenario_log module."""

from __future__ import annotations

import json


from aap_hermes import scenario_log


def test_log_is_noop_when_env_unset(monkeypatch, tmp_path):
    """With HERMES_SCENARIO_LOG_DIR unset, log() must not write anything."""
    monkeypatch.delenv("HERMES_SCENARIO_LOG_DIR", raising=False)
    scenario_log._reset_for_tests()

    # No directory created, no exception raised.
    scenario_log.log("tool_call", data={"name": "noop"})

    # tmp_path should remain empty — nothing was written anywhere obvious.
    assert list(tmp_path.iterdir()) == []


def test_log_writes_one_jsonl_line_per_call(monkeypatch, tmp_path):
    """With env set, each log() call appends exactly one JSON line."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "test-run-1")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    scenario_log.log("tool_call", data={"name": "aap_send_message"})
    scenario_log.log(
        "user_view",
        layer="named",
        audience="user",
        conv_id="conv-abc",
        data={"text": "hello"},
    )

    log_file = tmp_path / "hermes9.jsonl"
    assert log_file.exists()

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["kind"] == "tool_call"
    assert first["layer"] == "raw"
    assert first["audience"] == "internal"
    assert first["agent"] == "hermes9"
    assert first["run_id"] == "test-run-1"
    assert first["data"] == {"name": "aap_send_message"}
    assert first["conv_id"] is None
    assert "ts" in first and first["ts"].endswith("Z")
    assert "ts_mono_ns" in first and isinstance(first["ts_mono_ns"], int)

    second = json.loads(lines[1])
    assert second["kind"] == "user_view"
    assert second["layer"] == "named"
    assert second["audience"] == "user"
    assert second["conv_id"] == "conv-abc"
    assert second["data"] == {"text": "hello"}


def test_log_stamps_turn_id_and_conv_id_from_contextvars(monkeypatch, tmp_path):
    """turn_id, conv_id, parent_conv_id come from contextvars when not passed."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "test-run-2")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    token_turn = scenario_log.set_turn_id("t-7f3a")
    token_conv = scenario_log.set_conv_id("conv-abc")
    try:
        scenario_log.log("aap_outbound", data={"peer": "b^example"})
    finally:
        scenario_log.reset_turn_id(token_turn)
        scenario_log.reset_conv_id(token_conv)

    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    entry = json.loads(lines[0])
    assert entry["turn_id"] == "t-7f3a"
    assert entry["conv_id"] == "conv-abc"

    # After reset, contextvars are gone.
    scenario_log.log("aap_outbound", data={"peer": "c^example"})
    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    second = json.loads(lines[1])
    assert second["turn_id"] is None
    assert second["conv_id"] is None


def test_explicit_conv_id_overrides_contextvar(monkeypatch, tmp_path):
    """An explicit conv_id kwarg wins over the contextvar."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "test-run-3")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    token = scenario_log.set_conv_id("ctx-conv")
    try:
        scenario_log.log("tool_call", conv_id="explicit-conv", data={})
    finally:
        scenario_log.reset_conv_id(token)

    entry = json.loads((tmp_path / "hermes9.jsonl").read_text().strip().splitlines()[0])
    assert entry["conv_id"] == "explicit-conv"
