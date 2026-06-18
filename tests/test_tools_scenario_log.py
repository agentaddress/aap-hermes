"""Verify tools.py emits aap_outbound events at send sites."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aap_hermes import scenario_log


@pytest.mark.asyncio
async def test_aap_send_message_handler_emits_aap_outbound(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "tools-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    # Import inside the test so monkeypatch env is in place before any
    # module-load side effects.
    from aap_hermes.tools import aap_send_message_handler

    # Stub the relationship store to allow the send (treat the peer as a friend).
    from aap_hermes import tools as tools_mod

    class _FakeRelStore:
        def any_relationship_with(self, addr):
            return MagicMock()  # truthy — friend exists

    class _FakeOutboundContacts:
        def record(self, *args, **kwargs):
            pass

    fake_stores = MagicMock()
    fake_stores.relationships = _FakeRelStore()
    fake_stores.outbound_contacts = _FakeOutboundContacts()
    monkeypatch.setattr(tools_mod, "_get_stores", lambda: fake_stores)

    # Stub the mirror functions so they don't try to reach the gateway.
    monkeypatch.setattr(tools_mod, "mirror_to_home_channels", lambda **kw: None)
    monkeypatch.setattr(tools_mod, "mirror_outbound_to_aap_session",
                        lambda **kw: None)

    fake_client = MagicMock()
    fake_client.send_envelope = AsyncMock(return_value="envelope-id-123")

    fake_identity = MagicMock()
    fake_identity.address = "me^example"

    result = await aap_send_message_handler(
        client=fake_client,
        identity=fake_identity,
        catalog_cache=None,
        to="b^example",
        text="hi",
    )

    assert result["status"] == "sent"

    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    outbounds = [
        json.loads(line) for line in lines
        if json.loads(line)["kind"] == "aap_outbound"
    ]
    assert len(outbounds) == 1, f"expected 1 aap_outbound, got: {outbounds}"
    e = outbounds[0]
    assert e["layer"] == "raw"
    assert e["data"]["peer"] == "b^example"
    assert e["data"]["text"] == "hi"
    assert e["data"]["envelope_type"] == "aap.chat-message/v1"
    assert e["data"]["envelope_id"] == "envelope-id-123"


@pytest.mark.asyncio
async def test_aap_send_message_handler_no_emit_on_send_failure(
    monkeypatch, tmp_path
):
    """If client.send_envelope raises, no aap_outbound event is emitted."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "tools-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    from aap_hermes.tools import aap_send_message_handler
    from aap_hermes import tools as tools_mod
    from aap_hermes.tools import AAPClientError

    fake_stores = MagicMock()
    fake_stores.relationships.any_relationship_with = lambda addr: MagicMock()
    monkeypatch.setattr(tools_mod, "_get_stores", lambda: fake_stores)

    fake_client = MagicMock()
    fake_client.send_envelope = AsyncMock(
        side_effect=AAPClientError("network down")
    )

    result = await aap_send_message_handler(
        client=fake_client,
        identity=MagicMock(address="me^example"),
        catalog_cache=None,
        to="b^example",
        text="hi",
    )

    assert result["status"] == "error"

    log_file = tmp_path / "hermes9.jsonl"
    if log_file.exists():
        outbounds = [
            json.loads(line) for line in log_file.read_text().strip().splitlines()
            if json.loads(line)["kind"] == "aap_outbound"
        ]
        assert outbounds == []
