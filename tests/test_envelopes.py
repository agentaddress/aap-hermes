"""Tests for chat envelope wrap/unwrap."""

import json

import pytest

from aap.envelope import Envelope
from aap.keys import generate_keypair

from aap.messages import (
    CHAT_PAYLOAD_TYPE,
    UnsupportedPayloadType,
    build_chat_envelope,
    unwrap_chat_envelope,
    wrap_routing_envelope,
)


def test_build_chat_envelope_signs_correctly():
    seed, public = generate_keypair()
    env = build_chat_envelope(
        seed=seed,
        sender_address="chris^agentaddress.org",
        text="Hi James!",
        iat="2026-05-20T12:00:00Z",
    )
    assert env.payload_type == CHAT_PAYLOAD_TYPE
    assert env.payload == {"text": "Hi James!"}
    assert env.iss == "chris^agentaddress.org"
    assert env.verify(public) is True


def test_wrap_routing_envelope_shape():
    seed, _ = generate_keypair()
    inner = build_chat_envelope(
        seed=seed,
        sender_address="chris^agentaddress.org",
        text="hi",
        iat="2026-05-20T12:00:00Z",
    )
    wire = wrap_routing_envelope(to="james^hermes.example", inner=inner)
    data = json.loads(wire)
    assert data["type"] == "aap.routing-envelope/v1"
    assert data["to"] == "james^hermes.example"
    assert data["envelope"]["payload_type"] == CHAT_PAYLOAD_TYPE
    assert data["envelope"]["sig"] == inner.sig


def test_unwrap_chat_envelope_returns_text():
    seed, _ = generate_keypair()
    env = build_chat_envelope(
        seed=seed,
        sender_address="james^hermes.example",
        text="Hello back",
        iat="2026-05-20T12:00:00Z",
    )
    text, thread_id = unwrap_chat_envelope(env)
    assert text == "Hello back"
    assert thread_id is None


def test_unwrap_chat_envelope_rejects_wrong_payload_type():
    seed, _ = generate_keypair()
    env = Envelope(
        type="aap.envelope/v1",
        payload_type="aap.something-else/v1",
        payload={},
        iss="james^hermes.example",
        iat="2026-05-20T12:00:00Z",
    ).sign(seed)
    with pytest.raises(UnsupportedPayloadType, match="something-else"):
        unwrap_chat_envelope(env)


def test_unwrap_chat_envelope_rejects_missing_text():
    seed, _ = generate_keypair()
    env = Envelope(
        type="aap.envelope/v1",
        payload_type=CHAT_PAYLOAD_TYPE,
        payload={"not_text": "oops"},
        iss="james^hermes.example",
        iat="2026-05-20T12:00:00Z",
    ).sign(seed)
    with pytest.raises(ValueError, match="text"):
        unwrap_chat_envelope(env)


def test_build_chat_envelope_without_thread_id_omits_field():
    """When thread_id is not provided, the payload must NOT include the key."""
    from aap.keys import generate_keypair
    from aap.messages import build_chat_envelope

    seed, _ = generate_keypair()
    env = build_chat_envelope(
        seed=seed,
        sender_address="a^b",
        text="hello",
        iat="2026-05-21T12:00:00Z",
    )
    assert "thread_id" not in env.payload
    assert env.payload["text"] == "hello"


def test_build_chat_envelope_with_thread_id_includes_field():
    """When thread_id is provided, it must appear in the payload."""
    from aap.keys import generate_keypair
    from aap.messages import build_chat_envelope

    seed, _ = generate_keypair()
    env = build_chat_envelope(
        seed=seed,
        sender_address="a^b",
        text="hello",
        iat="2026-05-21T12:00:00Z",
        thread_id="dinner-plan-2026",
    )
    assert env.payload["thread_id"] == "dinner-plan-2026"
    assert env.payload["text"] == "hello"


def test_unwrap_chat_envelope_returns_text_and_thread_id_tuple():
    """unwrap returns (text, thread_id_or_None)."""
    from aap.keys import generate_keypair
    from aap.messages import build_chat_envelope, unwrap_chat_envelope

    seed, _ = generate_keypair()
    env_with = build_chat_envelope(
        seed=seed, sender_address="a", text="t1",
        iat="2026-05-21T12:00:00Z", thread_id="my-thread",
    )
    env_without = build_chat_envelope(
        seed=seed, sender_address="a", text="t2",
        iat="2026-05-21T12:00:00Z",
    )

    text, tid = unwrap_chat_envelope(env_with)
    assert text == "t1"
    assert tid == "my-thread"

    text, tid = unwrap_chat_envelope(env_without)
    assert text == "t2"
    assert tid is None


def test_unwrap_chat_envelope_rejects_non_string_thread_id():
    """If thread_id is present but not a string, unwrap raises ValueError."""
    import pytest
    from aap.envelope import Envelope
    from aap.keys import generate_keypair
    from aap.messages import unwrap_chat_envelope

    seed, _ = generate_keypair()
    # Build manually so we can stuff a non-string thread_id
    env = Envelope(
        type="aap.envelope/v1",
        payload_type="aap.message/v1",
        payload={"text": "hi", "thread_id": 42},  # int, not str
        iss="a^b",
        iat="2026-05-21T12:00:00Z",
    ).sign(seed)

    with pytest.raises(ValueError, match="thread_id"):
        unwrap_chat_envelope(env)


# ── v0.8.0 group-conversation fields on chat envelopes ─────────────────────


def test_build_chat_envelope_with_conversation_fields():
    """When conversation_id + conversation_members are provided, they must
    appear on the envelope (not in payload) and be covered by the
    signature (set before .sign())."""
    seed, public = generate_keypair()
    members = [
        "chris^example.com",
        "james^example.com",
        "sarah^example.com",
    ]
    env = build_chat_envelope(
        seed=seed,
        sender_address="chris^example.com",
        text="dinner at 7?",
        iat="2026-05-22T12:00:00Z",
        conversation_id="dinner-abc",
        conversation_members=members,
    )
    assert env.payload == {"text": "dinner at 7?"}
    assert env.conversation_id == "dinner-abc"
    assert env.conversation_members == members
    # Signature must verify (i.e. conversation fields were set before sign)
    assert env.verify(public) is True


def test_build_chat_envelope_without_conversation_fields_is_1to1():
    seed, _ = generate_keypair()
    env = build_chat_envelope(
        seed=seed,
        sender_address="chris^example.com",
        text="hi",
        iat="2026-05-22T12:00:00Z",
    )
    assert env.conversation_id is None
    assert env.conversation_members is None
