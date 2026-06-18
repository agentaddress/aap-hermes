"""Integration tests for AAP_INGEST_DECOUPLED dispatch behavior.

Verifies that, when the flag is on, inbound chat envelopes route through
``ingest.persist_user_message`` + ``ReasoningScheduler.signal`` and skip
the legacy ``_message_handler`` invocation path.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aap.keys import generate_keypair
from aap.relationships import RelationshipRecord


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
    address = "chris^example.com"
    identity = IdentityFile(private_seed=seed, public_key=public, address=address)
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


def _sign_chat_envelope(seed, address, text="hi"):
    from aap.envelope import Envelope
    return Envelope(
        type="aap.envelope/v1",
        payload_type="aap.message/v1",
        payload={"text": text},
        iss=address,
        iat="2026-06-10T12:00:00Z",
    ).sign(seed)


@pytest.mark.asyncio
async def test_decoupled_dispatch_persists_and_signals(
    adapter_fixture, monkeypatch
):
    """Flag on: ingest writes user_msg + scheduler.signal fires; legacy
    handler is NOT invoked from the dispatch body."""
    monkeypatch.setenv("AAP_INGEST_DECOUPLED", "1")
    monkeypatch.setattr(
        "aap_hermes.adapter.mirror_to_home_channels", MagicMock(),
    )

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "mary^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    legacy_handler = AsyncMock()
    adapter_fixture._message_handler = legacy_handler

    # Stub the scheduler with a signal mock.
    scheduler = MagicMock()
    scheduler.signal = AsyncMock()
    adapter_fixture._reasoning_scheduler = scheduler

    # Stub persist_user_message to observe the call.
    persist = MagicMock(return_value="sess-abc")
    monkeypatch.setattr("aap_hermes.ingest.persist_user_message", persist)

    env = _sign_chat_envelope(friend_seed, friend_addr, text="Saturday?")
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 7, "body": env.to_json()})

    persist.assert_called_once()
    args, kwargs = persist.call_args
    # First positional is the SessionSource for the AAP session.
    source = args[0]
    assert source.chat_id == friend_addr
    # Second positional is the inbound text (with trust preamble + agent label).
    body_text = args[1]
    assert "Saturday?" in body_text
    assert kwargs.get("message_id") == "7"

    scheduler.signal.assert_awaited_once()
    sig_args, sig_kwargs = scheduler.signal.call_args
    assert sig_args[0] == friend_addr
    origin = sig_kwargs["origin"]
    assert origin["sender"] == friend_addr
    assert origin["is_group"] is False

    legacy_handler.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_dispatch_when_flag_off(adapter_fixture, monkeypatch):
    """Flag off: legacy ``_message_handler`` path runs as before; ingest is
    not invoked."""
    monkeypatch.delenv("AAP_INGEST_DECOUPLED", raising=False)
    monkeypatch.setattr(
        "aap_hermes.adapter.mirror_to_home_channels", MagicMock(),
    )

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "mary^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    handler = AsyncMock(return_value="ok")
    adapter_fixture._message_handler = handler

    persist = MagicMock()
    monkeypatch.setattr("aap_hermes.ingest.persist_user_message", persist)
    # send() is what auto-reply calls; stub it so we don't hit AAP client.
    adapter_fixture.send = AsyncMock(return_value=MagicMock())

    env = _sign_chat_envelope(friend_seed, friend_addr, text="hi")
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 8, "body": env.to_json()})

    handler.assert_awaited_once()
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_group_home_reply_uses_decoupled_scheduler(
    adapter_fixture, monkeypatch, tmp_path
):
    """Bridged home replies should use durable ingest + scheduler, not a
    direct gateway turn that can be interrupted by peer traffic."""
    from aap_hermes import scenario_log

    monkeypatch.setenv("AAP_INGEST_DECOUPLED", "1")
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "home-reply-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes11")
    scenario_log._reset_for_tests()

    scheduler = MagicMock()
    scheduler.signal = AsyncMock()
    adapter_fixture._reasoning_scheduler = scheduler

    persist = MagicMock(return_value="sess-group")
    monkeypatch.setattr("aap_hermes.ingest.persist_user_message", persist)

    assert adapter_fixture.enqueue_group_home_reply(
        conversation_id="conv-123",
        group_label="Dinner Planning",
        text="Wednesday 8pm works for me",
    ) is True

    persist.assert_called_once()
    source, body = persist.call_args.args
    assert source.chat_id == "aap-group:conv-123"
    assert source.user_id == "aap-group:conv-123"
    assert "Home-channel reply from my user" in body
    assert "Wednesday 8pm works for me" in body

    await asyncio.sleep(0)
    scheduler.signal.assert_awaited_once()
    sig_args, sig_kwargs = scheduler.signal.call_args
    assert sig_args[0] == "aap-group:conv-123"
    assert sig_args[1].chat_id == "aap-group:conv-123"
    assert sig_kwargs["origin"] == {
        "sender": adapter_fixture.identity.address,
        "conv_id": "conv-123",
        "is_group": True,
        "home_reply": True,
    }

    entries = [
        json.loads(line)
        for line in (tmp_path / "hermes11.jsonl").read_text().splitlines()
    ]
    user_inputs = [e for e in entries if e["kind"] == "user_input"]
    assert len(user_inputs) == 1
    assert user_inputs[0]["layer"] == "named"
    assert user_inputs[0]["conv_id"] == "conv-123"
    assert "Wednesday 8pm works for me" in user_inputs[0]["data"]["text"]

    persisted = [e for e in entries if e["kind"] == "ingest_persisted"]
    assert len(persisted) == 1
    assert persisted[0]["data"]["session_id"] == "sess-group"
    assert persisted[0]["data"]["source"] == "home_reply_bridge"


def test_group_home_reply_refuses_without_scheduler(adapter_fixture, monkeypatch):
    monkeypatch.setenv("AAP_INGEST_DECOUPLED", "1")
    adapter_fixture._reasoning_scheduler = None
    persist = MagicMock()
    monkeypatch.setattr("aap_hermes.ingest.persist_user_message", persist)

    assert adapter_fixture.enqueue_group_home_reply(
        conversation_id="conv-123",
        group_label="Dinner Planning",
        text="Wednesday 8pm works for me",
    ) is False

    persist.assert_not_called()
