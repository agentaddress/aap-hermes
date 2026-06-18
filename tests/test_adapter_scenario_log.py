"""Verify adapter._dispatch emits aap_inbound and sets contextvars."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aap.envelope import Envelope
from aap.keys import generate_keypair

from aap_hermes import scenario_log


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
    ad.client.relay_url = "https://relay.example.com"
    return ad


def _sign_envelope(*, seed, address, payload_type, payload, conversation_id=None):
    return Envelope(
        type="aap.envelope/v1",
        payload_type=payload_type,
        payload=payload,
        iss=address,
        iat="2026-05-26T12:00:00Z",
        conversation_id=conversation_id,
    ).sign(seed)


@pytest.mark.asyncio
async def test_dispatch_emits_aap_inbound_with_turn_and_conv(
    adapter_fixture, monkeypatch, tmp_path
):
    """A verified inbound envelope produces a tagged aap_inbound event."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "adapter-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    # Build a real signed envelope from a known peer; pre-populate the
    # adapter's peer_keys so signature verification short-circuits to
    # our test key (skipping the network resolve path).
    sender_seed, sender_pub = generate_keypair()
    sender_addr = "peer^example.com"
    env = _sign_envelope(
        seed=sender_seed,
        address=sender_addr,
        payload_type="aap.group-invitation/v1",
        conversation_id="conv-xyz",
        payload={
            "purpose": "test",
            "members": [sender_addr, adapter_fixture.identity.address],
            "convener": sender_addr,
        },
    )
    adapter_fixture.peer_keys[sender_addr] = sender_pub

    # Stub the downstream handler so we isolate the pre-branch emit.
    adapter_fixture._handle_group_invitation = AsyncMock(return_value=None)

    await adapter_fixture._dispatch({"id": 1, "body": env.to_json()})

    log_file = tmp_path / "hermes9.jsonl"
    assert log_file.exists(), "scenario log file was not created"

    entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    inbounds = [e for e in entries if e["kind"] == "aap_inbound"]
    assert len(inbounds) == 1, f"expected 1 aap_inbound, got: {inbounds}"

    e = inbounds[0]
    assert e["layer"] == "raw"
    assert e["data"]["envelope_type"] == "aap.group-invitation/v1"
    assert e["data"]["from"] == sender_addr
    assert e["conv_id"] == "conv-xyz"
    assert e["turn_id"] is not None
    assert e["turn_id"].startswith("t-")


@pytest.mark.asyncio
async def test_dispatch_no_emit_when_signature_fails(
    adapter_fixture, monkeypatch, tmp_path
):
    """A bad-signature envelope is dropped without emitting aap_inbound."""
    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "adapter-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    scenario_log._reset_for_tests()

    # Sign with one key, install a DIFFERENT key as the trusted pubkey.
    real_seed, real_pub = generate_keypair()
    wrong_seed, wrong_pub = generate_keypair()
    sender_addr = "peer^example.com"
    env = _sign_envelope(
        seed=real_seed,
        address=sender_addr,
        payload_type="aap.group-invitation/v1",
        conversation_id="conv-abc",
        payload={
            "purpose": "test",
            "members": [sender_addr],
            "convener": sender_addr,
        },
    )
    adapter_fixture.peer_keys[sender_addr] = wrong_pub  # signature won't verify

    await adapter_fixture._dispatch({"id": 99, "body": env.to_json()})

    log_file = tmp_path / "hermes9.jsonl"
    if log_file.exists():
        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        inbounds = [e for e in entries if e["kind"] == "aap_inbound"]
        assert inbounds == [], f"unexpected inbounds on failed verification: {inbounds}"
