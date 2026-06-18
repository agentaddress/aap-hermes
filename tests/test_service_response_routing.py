"""Integration tests for _handle_service_response routing fix.

Covers the per-nonce origin store routing the async service_response
into the originating user/group session rather than spawning a fresh
session keyed to the business address.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aap.envelope import Envelope
from aap.keys import generate_keypair
from aap.payloads import ServiceResponse, ServiceResponseStatus

from aap_hermes.service_request_origins import RequestOrigin

@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def adapter_fixture(tmp_hermes_home):
    """Minimal adapter with stubs in place of network bits + a captured
    message handler so we can assert what the gateway would receive."""
    from aap_hermes.adapter import AAPPlatformAdapter
    from aap_hermes._hermes_base import Platform
    from aap.identity import IdentityFile

    seed, public = generate_keypair()
    identity = IdentityFile(
        private_seed=seed,
        public_key=public,
        address="chris^example.com",
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
    ad.client.relay_url = "https://relay.example.com"
    # Capture what the message handler would have been called with.
    ad._message_handler = AsyncMock(return_value="")
    ad.send = AsyncMock()
    return ad


def _service_response_envelope(*, biz_seed, biz_address, nonce, status, payload=None):
    resp = ServiceResponse(
        request_nonce=nonce,
        nonce=f"response-{nonce}",
        service_id="book-table",
        status=status,
        payload=payload or {},
        denial_reason=None if status != ServiceResponseStatus.DENIED else "verification_required",
    )
    return Envelope(
        type="aap.envelope/v1",
        payload_type=ServiceResponse.PAYLOAD_TYPE,
        payload=resp.to_dict(),
        iss=biz_address,
        iat="2026-06-05T11:22:53Z",
    ).sign(biz_seed)


def _record_origin(adapter, *, nonce, target_address, group=None):
    origin = RequestOrigin(
        platform="aap",
        chat_id=group or "telegram-1234",
        chat_type="group" if group else "dm",
        user_id="user-ian",
        user_name="Ian",
        thread_id=None,
        chat_name="Group Dinner" if group else None,
        target_address=target_address,
        group_conversation_id=group,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    adapter.stores.service_request_origins.record(nonce, origin)
    return origin


# -- happy path: routed into the recorded SessionSource ---------------------


@pytest.mark.asyncio
async def test_service_response_dispatches_with_recorded_session_source(
    adapter_fixture, monkeypatch,
):
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    biz_seed, _ = generate_keypair()
    biz_address = "bookings^dinetable-test.fly.dev"
    origin = _record_origin(
        adapter_fixture,
        nonce="req-42",
        target_address=biz_address,
        group="aap-group:conv-dinner",
    )

    env = _service_response_envelope(
        biz_seed=biz_seed,
        biz_address=biz_address,
        nonce="req-42",
        status=ServiceResponseStatus.CONFIRMED,
        payload={"slot": "Fri 7pm"},
    )

    await adapter_fixture._handle_service_response(env)

    adapter_fixture._message_handler.assert_awaited_once()
    event = adapter_fixture._message_handler.await_args.args[0]
    # Critical: the dispatched event's SessionSource is the ORIGINATING
    # session, not a fresh DM keyed to the business address.
    assert event.source.chat_id == origin.chat_id
    assert event.source.chat_type == "group"
    assert event.source.user_id == "user-ian"
    # And the post-turn auto-reply to the business sender is suppressed.
    adapter_fixture.send.assert_not_called()


# -- sniping: a forged response with mismatched iss doesn't consume record --


@pytest.mark.asyncio
async def test_mismatched_iss_drops_and_leaves_record(
    adapter_fixture, monkeypatch,
):
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    legit_seed, _ = generate_keypair()
    legit_biz = "bookings^dinetable-test.fly.dev"
    attacker_seed, _ = generate_keypair()
    attacker_addr = "attacker^evil.example"

    _record_origin(
        adapter_fixture,
        nonce="req-9",
        target_address=legit_biz,
        group="aap-group:conv-dinner",
    )

    forged = _service_response_envelope(
        biz_seed=attacker_seed,
        biz_address=attacker_addr,
        nonce="req-9",
        status=ServiceResponseStatus.DENIED,
    )

    await adapter_fixture._handle_service_response(forged)

    # Forged response: no dispatch, no auto-send.
    adapter_fixture._message_handler.assert_not_awaited()
    adapter_fixture.send.assert_not_called()

    # And the origin record must still be there for the real response.
    real = _service_response_envelope(
        biz_seed=legit_seed,
        biz_address=legit_biz,
        nonce="req-9",
        status=ServiceResponseStatus.CONFIRMED,
    )
    await adapter_fixture._handle_service_response(real)
    adapter_fixture._message_handler.assert_awaited_once()


# -- unknown nonce: no dispatch ---------------------------------------------


@pytest.mark.asyncio
async def test_unknown_nonce_drops_with_no_dispatch(
    adapter_fixture, monkeypatch,
):
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    biz_seed, _ = generate_keypair()
    env = _service_response_envelope(
        biz_seed=biz_seed,
        biz_address="someone^example.com",
        nonce="never-recorded",
        status=ServiceResponseStatus.CONFIRMED,
    )
    await adapter_fixture._handle_service_response(env)
    adapter_fixture._message_handler.assert_not_awaited()
    adapter_fixture.send.assert_not_called()


# -- post-turn auto-reply policy --------------------------------------------


@pytest.mark.asyncio
async def test_final_llm_text_is_dropped_not_sent_to_business(
    adapter_fixture, monkeypatch, caplog,
):
    """When the LLM returns a non-empty final assistant text, that text
    must NOT be auto-sent to the business sender. The routed session is a
    user/group session — final text should go through send_message /
    aap_group_send explicitly."""
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    biz_seed, _ = generate_keypair()
    biz_address = "bookings^dinetable-test.fly.dev"
    _record_origin(
        adapter_fixture,
        nonce="req-77",
        target_address=biz_address,
        group=None,
    )

    # LLM returns a final assistant text (not [NO_REPLY], not empty).
    adapter_fixture._message_handler = AsyncMock(return_value="Got it, thanks!")

    env = _service_response_envelope(
        biz_seed=biz_seed,
        biz_address=biz_address,
        nonce="req-77",
        status=ServiceResponseStatus.CONFIRMED,
    )

    import logging
    with caplog.at_level(logging.INFO):
        await adapter_fixture._handle_service_response(env)

    # The LLM ran...
    adapter_fixture._message_handler.assert_awaited_once()
    # ...but its final text did NOT go to the business via auto-reply.
    adapter_fixture.send.assert_not_called()
    assert "dropping non-tool final text" in caplog.text


# -- group_conversation_id flows through to the prompt fragment -------------


@pytest.mark.asyncio
async def test_group_context_note_uses_recorded_group_id(
    adapter_fixture, monkeypatch,
):
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    biz_seed, _ = generate_keypair()
    biz_address = "bookings^dinetable-test.fly.dev"
    _record_origin(
        adapter_fixture,
        nonce="req-g",
        target_address=biz_address,
        group="aap-group:conv-dinner",
    )

    env = _service_response_envelope(
        biz_seed=biz_seed,
        biz_address=biz_address,
        nonce="req-g",
        status=ServiceResponseStatus.CONFIRMED,
    )
    await adapter_fixture._handle_service_response(env)

    event = adapter_fixture._message_handler.await_args.args[0]
    assert "GROUP UPDATE REQUIRED" in event.text
    assert "aap-group:conv-dinner" in event.text
