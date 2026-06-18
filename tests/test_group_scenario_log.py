"""Verify named group-state events fire from adapter handlers + runtime."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aap.keys import generate_keypair

from aap_hermes import scenario_log


# Reuse the dispatch test fixtures inline to avoid cross-file fixture imports.
@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def adapter_fixture(tmp_hermes_home):
    from aap_hermes.adapter import AAPPlatformAdapter
    from aap_hermes._hermes_base import Platform
    from aap.identity import IdentityFile

    seed, public = generate_keypair()
    identity = IdentityFile(
        private_seed=seed, public_key=public,
        address="me^example.com",
    )
    config = MagicMock()
    config.extra = {}
    ad = AAPPlatformAdapter(
        config=config,
        platform=Platform("aap"),
        relay_url="https://relay.example.com",
        identity=identity,
    )
    ad.client = MagicMock()
    ad.client.send_envelope_raw = AsyncMock(return_value=1)
    return ad


@pytest.mark.asyncio
async def test_handle_group_invitation_emits_named_invitation_received(
    adapter_fixture, monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "group-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes10")
    scenario_log._reset_for_tests()

    # Stub the relationship gate to pass.
    adapter_fixture.stores.relationships.any_relationship_with = (
        lambda addr: MagicMock()
    )
    adapter_fixture.stores.conversations.record = MagicMock()

    # Stub the mirror so the test doesn't try to reach the gateway.
    import aap_hermes.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "mirror_to_home_channels",
                        lambda **kw: None)
    # Stub _resolve_peer_label
    adapter_fixture._resolve_peer_label = AsyncMock(return_value="Agent A")

    # Build a synthetic invitation payload — feed directly to the handler
    # to bypass signature verification.
    from aap.payloads import GroupInvitation
    invite = GroupInvitation(
        conversation_id="conv-test-123",
        purpose="dinner planning",
        members=["a^example.com", "b^example.com"],
        convener="a^example.com",
        nonce="test-nonce-abc",
    )
    envelope = MagicMock()
    envelope.iss = "a^example.com"
    envelope.payload = invite.to_dict()

    await adapter_fixture._handle_group_invitation(envelope, {})

    lines = (tmp_path / "hermes10.jsonl").read_text().strip().splitlines()
    named = [
        json.loads(line) for line in lines
        if json.loads(line)["layer"] == "named"
        and json.loads(line)["kind"] == "invitation_received"
    ]
    assert len(named) == 1, f"got: {named}"
    e = named[0]
    assert e["conv_id"] == "conv-test-123"
    assert e["data"]["convener"] == "a^example.com"
    assert "members" in e["data"]
    assert e["data"]["purpose"] == "dinner planning"


@pytest.mark.asyncio
async def test_handle_group_membership_update_emits_named_event(
    adapter_fixture, monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "group-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes11")
    scenario_log._reset_for_tests()

    import aap_hermes.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "mirror_to_home_channels",
                        lambda **kw: None)

    # Pre-record a conversation so the handler doesn't drop it.
    from aap.conversations import Conversation
    conv = Conversation(
        conversation_id="conv-upd-456",
        purpose="project",
        members=["a^example.com", "b^example.com"],
        convener="a^example.com",
        accepted_at="2026-01-01T00:00:00Z",
        last_message_at=None,
        name="Project",
        goal="finish it",
    )
    adapter_fixture.stores.conversations.record(conv)

    from aap.payloads import GroupMembershipUpdate
    update = GroupMembershipUpdate(
        conversation_id="conv-upd-456",
        members=["a^example.com", "b^example.com", "c^example.com"],
        convener="a^example.com",
        added=["c^example.com"],
        removed=[],
        convener_changed_from=None,
        nonce="nonce-upd",
    )
    envelope = MagicMock()
    envelope.iss = "a^example.com"
    envelope.payload = update.to_dict()

    await adapter_fixture._handle_group_membership_update(envelope)

    lines = (tmp_path / "hermes11.jsonl").read_text().strip().splitlines()
    named = [
        json.loads(line) for line in lines
        if json.loads(line)["layer"] == "named"
        and json.loads(line)["kind"] == "group_membership_updated"
    ]
    assert len(named) == 1, f"got: {named}"
    e = named[0]
    assert e["conv_id"] == "conv-upd-456"
    assert e["data"]["added"] == ["c^example.com"]
    assert e["data"]["removed"] == []
    assert e["data"]["convener"] == "a^example.com"


@pytest.mark.asyncio
async def test_handle_group_leave_emits_named_participant_left(
    adapter_fixture, monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "group-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes12")
    scenario_log._reset_for_tests()

    import aap_hermes.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "mirror_to_home_channels",
                        lambda **kw: None)

    from aap.conversations import Conversation
    conv = Conversation(
        conversation_id="conv-leave-789",
        purpose="chat",
        members=["a^example.com", "b^example.com"],
        convener="a^example.com",
        accepted_at="2026-01-01T00:00:00Z",
        last_message_at=None,
        name="Chat",
        goal="",
    )
    adapter_fixture.stores.conversations.record(conv)

    from aap.payloads import GroupLeave
    leave = GroupLeave(
        conversation_id="conv-leave-789",
        nonce="leave-1",
        reason="stepping out",
    )
    envelope = MagicMock()
    envelope.iss = "b^example.com"
    envelope.payload = leave.to_dict()

    await adapter_fixture._handle_group_leave(envelope)

    lines = (tmp_path / "hermes12.jsonl").read_text().strip().splitlines()
    named = [
        json.loads(line) for line in lines
        if json.loads(line)["layer"] == "named"
        and json.loads(line)["kind"] == "participant_left"
    ]
    assert len(named) == 1, f"got: {named}"
    e = named[0]
    assert e["conv_id"] == "conv-leave-789"
    assert e["data"]["participant"] == "b^example.com"
    assert e["data"]["reason"] == "stepping out"


@pytest.mark.asyncio
async def test_handle_group_complete_emits_named_group_completed(
    adapter_fixture, monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "group-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes13")
    scenario_log._reset_for_tests()

    import aap_hermes.adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "mirror_to_home_channels",
                        lambda **kw: None)

    from aap.conversations import Conversation
    conv = Conversation(
        conversation_id="conv-done-abc",
        purpose="plan dinner",
        members=["a^example.com", "b^example.com"],
        convener="a^example.com",
        accepted_at="2026-01-01T00:00:00Z",
        last_message_at=None,
        name="Dinner",
        goal="agree on a restaurant",
    )
    adapter_fixture.stores.conversations.record(conv)

    from aap.payloads import GroupComplete
    complete = GroupComplete(
        conversation_id="conv-done-abc",
        outcome="agreed on Bella Italia, Friday 7pm",
        nonce="nonce-done",
    )
    envelope = MagicMock()
    envelope.iss = "a^example.com"
    envelope.payload = complete.to_dict()

    await adapter_fixture._handle_group_complete(envelope)

    lines = (tmp_path / "hermes13.jsonl").read_text().strip().splitlines()
    named = [
        json.loads(line) for line in lines
        if json.loads(line)["layer"] == "named"
        and json.loads(line)["kind"] == "group_completed"
    ]
    assert len(named) == 1, f"got: {named}"
    e = named[0]
    assert e["conv_id"] == "conv-done-abc"
    assert e["data"]["outcome"] == "agreed on Bella Italia, Friday 7pm"


@pytest.mark.asyncio
async def test_group_start_tool_wrapper_emits_named_group_started(
    monkeypatch, tmp_path
):
    """When aap_group_start succeeds, the wrapper emits a named group_started."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "group-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    # Stub the actual handler to return a success shape.
    import aap_hermes._runtime as runtime_mod
    async def _fake_handler(*args, **kwargs):
        return {"status": "started", "conversation_id": "conv-NEW",
                "members": ["a^example.com", "b^example.com"]}
    monkeypatch.setattr(runtime_mod, "aap_group_start_handler", _fake_handler)

    # Stub _resolve_runtime to yield fake client/identity
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_resolve():
        yield (MagicMock(), MagicMock(), MagicMock(), True)

    monkeypatch.setattr(runtime_mod, "_resolve_runtime", _fake_resolve)

    await runtime_mod.group_start_tool_wrapper(
        {"members": ["a^example.com", "b^example.com"],
         "purpose": "dinner",
         "name": "Dinner Crew",
         "goal": "find a time"}
    )

    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    named = [
        json.loads(line) for line in lines
        if json.loads(line)["layer"] == "named"
        and json.loads(line)["kind"] == "group_started"
    ]
    assert len(named) == 1, f"got named={named}"
    e = named[0]
    assert e["conv_id"] == "conv-NEW"
    assert e["data"]["members"] == ["a^example.com", "b^example.com"]
    assert e["data"]["purpose"] == "dinner"
