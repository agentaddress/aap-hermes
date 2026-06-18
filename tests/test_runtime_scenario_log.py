"""Verify tool wrappers in _runtime.py emit tool_call/tool_result events."""

from __future__ import annotations

import json

import pytest

from aap_hermes import scenario_log
from aap_hermes._runtime import group_list_tool_wrapper


@pytest.mark.asyncio
async def test_group_list_wrapper_emits_tool_call_and_result(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "rt-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    # group_list is the simplest wrapper — synchronous, no peer call, no IO.
    await group_list_tool_wrapper({})

    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    entries = [json.loads(line) for line in lines]
    kinds = [e["kind"] for e in entries]

    assert "tool_call" in kinds
    assert "tool_result" in kinds

    call_entry = next(e for e in entries if e["kind"] == "tool_call")
    assert call_entry["layer"] == "raw"
    assert call_entry["data"]["name"] == "aap_group_list"
    assert call_entry["data"]["args"] == {}

    result_entry = next(e for e in entries if e["kind"] == "tool_result")
    assert result_entry["layer"] == "raw"
    assert result_entry["data"]["name"] == "aap_group_list"
    # Result shape can vary — just confirm the field exists.
    assert "result" in result_entry["data"]

    # Order matters: tool_call comes before tool_result.
    assert kinds.index("tool_call") < kinds.index("tool_result")
