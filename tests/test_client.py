"""Tests for the AAP relay HTTP client."""

import json

import httpx
import pytest
import respx

from aap.envelope import Envelope
from aap.encryption import EncryptedEnvelope, decrypt_envelope, generate_encryption_keypair
from aap.jcs import canonicalize
from aap.keys import (
    decode_b64url,
    encode_b64url,
    generate_keypair,
    verify as ed25519_verify,
)
from aap.payloads import AgentCard

from aap.client import AAPClient


@pytest.fixture
def client_fixture():
    seed, public = generate_keypair()
    client = AAPClient(
        relay_url="https://relay.test",
        seed=seed,
        public_key=public,
        address="chris^agentaddress.org",
        timeout_seconds=5,
    )
    return client, seed, public


def _decrypt_sent_message(sent: dict, recipient_private_key: bytes) -> Envelope:
    return decrypt_envelope(
        EncryptedEnvelope.from_dict(sent["envelope"]),
        recipient_private_key=recipient_private_key,
        recipient_address=sent["to"],
    )


@respx.mock
@pytest.mark.asyncio
async def test_register_posts_signed_agent_card(client_fixture):
    client, seed, public = client_fixture
    route = respx.post("https://relay.test/aap/agents/register").mock(
        return_value=httpx.Response(200, json={
            "address": "chris^agentaddress.org",
            "first_seen": True,
        })
    )

    result = await client.register()

    assert route.called
    assert result == {"address": "chris^agentaddress.org", "first_seen": True}

    sent = json.loads(route.calls.last.request.content.decode())
    env = Envelope.from_dict(sent)
    assert env.payload_type == AgentCard.PAYLOAD_TYPE
    assert env.verify(public) is True
    card = AgentCard.from_dict(env.payload)
    assert card.address == "chris^agentaddress.org"
    assert card.public_key == encode_b64url(public)


@respx.mock
@pytest.mark.asyncio
async def test_send_envelope_posts_routing_envelope(client_fixture):
    client, seed, _ = client_fixture
    recipient_private, recipient_public = generate_encryption_keypair()
    route = respx.post("https://relay.test/aap/inbox").mock(
        return_value=httpx.Response(202, json={"id": 42, "queued_at": "2026-05-20T12:00:00Z"}),
    )

    envelope_id = await client.send_envelope(
        to="james^hermes.example",
        text="Hi James",
        recipient_encryption_key=recipient_public,
    )
    assert envelope_id == 42

    sent = json.loads(route.calls.last.request.content.decode())
    assert sent["type"] == "aap.routing-envelope/v1"
    assert sent["to"] == "james^hermes.example"
    inner = _decrypt_sent_message(sent, recipient_private)
    assert inner.payload_type == "aap.message/v1"
    assert inner.payload["text"] == "Hi James"


@respx.mock
@pytest.mark.asyncio
async def test_send_envelope_includes_thread_id_when_provided(client_fixture):
    """When thread_id is passed, the routing envelope's inner payload contains it."""
    client, _, _ = client_fixture
    recipient_private, recipient_public = generate_encryption_keypair()
    route = respx.post("https://relay.test/aap/inbox").mock(
        return_value=httpx.Response(202, json={"id": 100, "queued_at": "2026-05-21T12:00:00Z"}),
    )

    await client.send_envelope(
        to="james^hermes.example",
        text="hi",
        thread_id="my-thread",
        recipient_encryption_key=recipient_public,
    )

    sent = json.loads(route.calls.last.request.content.decode())
    inner = _decrypt_sent_message(sent, recipient_private)
    assert inner.payload["text"] == "hi"
    assert inner.payload["thread_id"] == "my-thread"


@respx.mock
@pytest.mark.asyncio
async def test_send_envelope_omits_thread_id_when_absent(client_fixture):
    """When thread_id is not passed, the payload must not include the field."""
    client, _, _ = client_fixture
    recipient_private, recipient_public = generate_encryption_keypair()
    route = respx.post("https://relay.test/aap/inbox").mock(
        return_value=httpx.Response(202, json={"id": 101, "queued_at": "2026-05-21T12:00:00Z"}),
    )

    await client.send_envelope(
        to="james^hermes.example",
        text="hi",
        recipient_encryption_key=recipient_public,
    )

    sent = json.loads(route.calls.last.request.content.decode())
    inner = _decrypt_sent_message(sent, recipient_private)
    assert "thread_id" not in inner.payload


@respx.mock
@pytest.mark.asyncio
async def test_poll_inbox_constructs_correct_aap_sig_header(client_fixture):
    client, seed, public = client_fixture
    route = respx.get("https://relay.test/aap/inbox").mock(
        return_value=httpx.Response(200, json={"envelopes": []})
    )

    await client.poll_inbox(wait=0)

    assert route.called
    request = route.calls.last.request
    auth = request.headers.get("Authorization")
    assert auth is not None and auth.startswith("AAP-Sig ")
    sig_b64 = auth.removeprefix("AAP-Sig ").strip()
    ts = request.headers["X-AAP-Sig-Ts"]
    nonce = request.headers["X-AAP-Sig-Nonce"]

    # Verify the signature against the client's public key
    canonical = canonicalize({
        "address": "chris^agentaddress.org",
        "method": "GET",
        "nonce": nonce,
        "path": "/aap/inbox",
        "ts": ts,
    })
    assert ed25519_verify(public, canonical, decode_b64url(sig_b64))


@respx.mock
@pytest.mark.asyncio
async def test_poll_inbox_returns_envelopes(client_fixture):
    client, _, _ = client_fixture
    respx.get("https://relay.test/aap/inbox").mock(
        return_value=httpx.Response(200, json={
            "envelopes": [
                {"id": 1, "body": "{\"v\":1}", "sender": "james^hermes.example"},
            ],
        }),
    )

    envelopes = await client.poll_inbox(wait=0)
    assert len(envelopes) == 1
    assert envelopes[0]["id"] == 1
    assert envelopes[0]["sender"] == "james^hermes.example"


@respx.mock
@pytest.mark.asyncio
async def test_register_raises_on_409(client_fixture):
    client, _, _ = client_fixture
    respx.post("https://relay.test/aap/agents/register").mock(
        return_value=httpx.Response(409, json={"detail": "key change"})
    )
    from aap.client import KeyChangeRejected
    with pytest.raises(KeyChangeRejected):
        await client.register()


@respx.mock
@pytest.mark.asyncio
async def test_resolve_peer_returns_pubkey_on_success(client_fixture):
    """resolve_peer fetches the self-signed AgentCard and returns the Ed25519 pubkey."""
    client, _, _ = client_fixture
    peer_seed, peer_public = generate_keypair()
    peer_address = "alice^example.dev"
    card = AgentCard(
        address=peer_address,
        did="did:web:example.dev#agent",
        public_key=encode_b64url(peer_public),
        endpoints=[],
    )
    envelope = Envelope(
        type="aap.envelope/v1",
        payload_type=AgentCard.PAYLOAD_TYPE,
        payload=card.to_dict(),
        iss=peer_address,
        iat="2026-05-21T12:00:00Z",
    ).sign(peer_seed)

    respx.post("https://example.dev/.well-known/aap-resolve").mock(
        return_value=httpx.Response(200, content=envelope.to_json()),
    )

    result = await client.resolve_peer(peer_address)
    assert result == peer_public


@respx.mock
@pytest.mark.asyncio
async def test_resolve_peer_raises_on_404(client_fixture):
    from aap.client import AAPClientError
    client, _, _ = client_fixture
    respx.post("https://example.dev/.well-known/aap-resolve").mock(
        return_value=httpx.Response(404, json={"detail": "not hosted here"}),
    )
    with pytest.raises(AAPClientError, match="not hosted at example.dev"):
        await client.resolve_peer("alice^example.dev")


@respx.mock
@pytest.mark.asyncio
async def test_resolve_peer_raises_on_mismatched_address(client_fixture):
    """If the resolved AgentCard's address doesn't match what we asked for, error out."""
    from aap.client import AAPClientError
    client, _, _ = client_fixture
    peer_seed, peer_public = generate_keypair()
    # Card claims to be 'bob' but we asked for 'alice'
    card = AgentCard(
        address="bob^example.dev",
        did="did:web:example.dev#agent",
        public_key=encode_b64url(peer_public),
        endpoints=[],
    )
    envelope = Envelope(
        type="aap.envelope/v1",
        payload_type=AgentCard.PAYLOAD_TYPE,
        payload=card.to_dict(),
        iss=card.address,
        iat="2026-05-21T12:00:00Z",
    ).sign(peer_seed)
    respx.post("https://example.dev/.well-known/aap-resolve").mock(
        return_value=httpx.Response(200, content=envelope.to_json()),
    )
    with pytest.raises(AAPClientError, match="address.*!= requested"):
        await client.resolve_peer("alice^example.dev")
