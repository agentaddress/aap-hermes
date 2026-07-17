"""Integration tests for v0.6 adapter dispatch handlers.

Drives ``_handle_*`` methods directly with synthesized signed envelopes
and asserts on the side effects: relationship store updates, pending
proposal store updates, mirrored prompts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aap.envelope import Envelope
from aap.keys import generate_keypair
from aap.payloads import (
    AgentCard,
    RelationshipAccept,
    RelationshipDecline,
    RelationshipProposal,
    RelationshipRevoke,
    ServiceFollowup,
    ServiceFollowupGrant,
    ServiceRequest,
    ServiceResponse,
)


@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def adapter_fixture(tmp_hermes_home):
    """Construct a minimal adapter with stubs in place of network bits."""
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
    ad.client.close = AsyncMock(return_value=None)
    ad._new_client = MagicMock(return_value=ad.client)
    return ad


def _sign_envelope(*, seed, address, payload_type, payload):
    return Envelope(
        type="aap.envelope/v1",
        payload_type=payload_type,
        payload=payload,
        iss=address,
        iat="2026-05-26T12:00:00Z",
    ).sign(seed)


def _agent_card_envelope(*, seed, address, public_key_bytes, kind="personal"):
    from aap.keys import encode_b64url
    card = AgentCard(
        address=address,
        did=f"did:web:{address.split('^', 1)[1]}#agent",
        public_key=encode_b64url(public_key_bytes),
        endpoints=[{"type": "didcomm", "uri": "https://relay.example.com"}],
        kind=kind,
    )
    return _sign_envelope(
        seed=seed,
        address=address,
        payload_type=AgentCard.PAYLOAD_TYPE,
        payload=card.to_dict(),
    )


# -- service_request → denied (personal agent can't serve) -----------------


@pytest.mark.asyncio
async def test_handle_service_request_returns_denied(adapter_fixture):
    seed, _ = generate_keypair()
    req = ServiceRequest(
        service_id="book-table",
        payload={"name": "John", "party_size": 2},
        nonce="req-1",
    )
    env = _sign_envelope(
        seed=seed,
        address="requester^example.com",
        payload_type=ServiceRequest.PAYLOAD_TYPE,
        payload=req.to_dict(),
    )
    await adapter_fixture._handle_service_request(env)

    adapter_fixture.client.send_envelope_raw.assert_called_once()
    call = adapter_fixture.client.send_envelope_raw.call_args
    assert call.kwargs["to"] == "requester^example.com"
    sent_env = Envelope.from_json(call.kwargs["envelope_json"])
    assert sent_env.payload_type == ServiceResponse.PAYLOAD_TYPE
    assert sent_env.payload["status"] == "denied"
    assert sent_env.payload["denial_reason"] == "not_a_business"


# -- relationship_proposal → pending + USER REQUIRED prompt ----------------


@pytest.mark.asyncio
async def test_handle_relationship_proposal_admin_carries_loud_warning(
    adapter_fixture, monkeypatch
):
    """Admin proposals must show the user a STRONG warning explaining
    that admin grants full tool-call access and should only be approved
    when both agents share an owner."""
    proposer_seed, proposer_pub = generate_keypair()
    proposer_addr = "hermes2^example.com"
    card_env = _agent_card_envelope(
        seed=proposer_seed, address=proposer_addr, public_key_bytes=proposer_pub,
    )

    proposal = RelationshipProposal(
        relationship_type="admin",
        proposer_card_envelope=card_env.to_json(),
        nonce="prop-admin-1",
    )
    env = _sign_envelope(
        seed=proposer_seed,
        address=proposer_addr,
        payload_type=RelationshipProposal.PAYLOAD_TYPE,
        payload=proposal.to_dict(),
    )

    mirror_mock = MagicMock()
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", mirror_mock)
    await adapter_fixture._handle_relationship_proposal(env)

    mirror_mock.assert_called_once()
    prompt_text = mirror_mock.call_args.kwargs["text"]
    assert "ADMIN RELATIONSHIP PROPOSAL" in prompt_text
    assert "WARNING" in prompt_text
    assert "FULL TOOL-CALL ACCESS" in prompt_text
    assert "CONTROL BOTH AGENTS" in prompt_text
    assert proposer_addr in prompt_text


@pytest.mark.asyncio
async def test_handle_relationship_proposal_team_warning_softer(
    adapter_fixture, monkeypatch
):
    """Team proposals get a softer warning (less power than admin) and
    surface the resource label so the user knows the scope."""
    proposer_seed, proposer_pub = generate_keypair()
    proposer_addr = "dev^example.com"
    card_env = _agent_card_envelope(
        seed=proposer_seed, address=proposer_addr, public_key_bytes=proposer_pub,
    )

    proposal = RelationshipProposal(
        relationship_type="team",
        proposer_card_envelope=card_env.to_json(),
        nonce="prop-team-1",
        resource="github.com/acme/widgets",
    )
    env = _sign_envelope(
        seed=proposer_seed,
        address=proposer_addr,
        payload_type=RelationshipProposal.PAYLOAD_TYPE,
        payload=proposal.to_dict(),
    )

    mirror_mock = MagicMock()
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", mirror_mock)
    await adapter_fixture._handle_relationship_proposal(env)

    prompt_text = mirror_mock.call_args.kwargs["text"]
    assert "Team relationship" in prompt_text
    assert "github.com/acme/widgets" in prompt_text
    # The admin-only severe banner must NOT appear for team.
    assert "ADMIN RELATIONSHIP PROPOSAL" not in prompt_text


@pytest.mark.asyncio
async def test_handle_relationship_proposal_parks_pending(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    """An inbound proposal is recorded as pending-inbound; the user can
    then resolve via /aap friend-accept <nonce> or bare 'approve'."""
    from aap.stores.pending_proposals import PendingProposalStore

    proposer_seed, proposer_pub = generate_keypair()
    proposer_addr = "mary^example.com"
    card_env = _agent_card_envelope(
        seed=proposer_seed, address=proposer_addr, public_key_bytes=proposer_pub,
    )

    proposal = RelationshipProposal(
        relationship_type="friend",
        proposer_card_envelope=card_env.to_json(),
        nonce="prop-1",
    )
    env = _sign_envelope(
        seed=proposer_seed,
        address=proposer_addr,
        payload_type=RelationshipProposal.PAYLOAD_TYPE,
        payload=proposal.to_dict(),
    )

    # Patch mirror_to_home_channels so we don't try to write to a real channel.
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    await adapter_fixture._handle_relationship_proposal(env)

    pending = PendingProposalStore.load(tmp_hermes_home)
    row = pending.get_inbound("prop-1")
    assert row is not None
    assert row.proposer_address == proposer_addr
    assert row.relationship_type == "friend"


# -- relationship_accept → persists RelationshipRecord ----------------------


@pytest.mark.asyncio
async def test_handle_relationship_accept_creates_record(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    from aap.stores.pending_proposals import PendingProposalStore
    from aap.relationships import RelationshipStore

    # First: simulate that WE earlier sent an outbound proposal to Mary
    # (i.e., we're the proposer). Record it as pending-outbound.
    # Write directly to the adapter's in-memory store so the handler sees it.
    from aap.relationships import build_relationship_proposal_envelope

    accepter_seed, accepter_public = generate_keypair()
    proposal = build_relationship_proposal_envelope(
        seed=adapter_fixture.identity.private_seed,
        sender_address=adapter_fixture.identity.address,
        relationship_type="friend",
        proposer_card_envelope_json=_agent_card_envelope(
            seed=adapter_fixture.identity.private_seed,
            address=adapter_fixture.identity.address,
            public_key_bytes=adapter_fixture.identity.public_key,
        ).to_json(),
        nonce="prop-1",
        iat="2026-05-26T12:00:00Z",
    )
    adapter_fixture.stores.pending_proposals.record_outbound(
        nonce="prop-1",
        peer_address="mary^example.com",
        relationship_type="friend",
        resource=None,
        proposal_envelope_json=proposal.to_json(),
    )

    accept = RelationshipAccept(
        proposal_nonce="prop-1",
        accepter_card_envelope=_agent_card_envelope(
            seed=accepter_seed,
            address="mary^example.com",
            public_key_bytes=accepter_public,
        ).to_json(),
    )
    env = _sign_envelope(
        seed=accepter_seed,
        address="mary^example.com",
        payload_type=RelationshipAccept.PAYLOAD_TYPE,
        payload=accept.to_dict(),
    )

    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())
    await adapter_fixture._handle_relationship_accept(env, accepter_public)

    store = RelationshipStore.load(tmp_hermes_home)
    assert store.has_friend("mary^example.com")
    # Pending should be cleared.
    assert PendingProposalStore.load(tmp_hermes_home).take_outbound("prop-1") is None


# -- relationship_decline → clears pending, no record -----------------------


@pytest.mark.asyncio
async def test_handle_relationship_decline_clears_pending(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    from aap.stores.pending_proposals import PendingProposalStore
    from aap.relationships import RelationshipStore

    # Write directly to the adapter's in-memory store so the handler sees it.
    adapter_fixture.stores.pending_proposals.record_outbound(
        nonce="prop-2",
        peer_address="bob^example.com",
        relationship_type="friend",
        resource=None,
        proposal_envelope_json="{}",
    )

    decliner_seed, _ = generate_keypair()
    decline = RelationshipDecline(proposal_nonce="prop-2", reason="don't know you")
    env = _sign_envelope(
        seed=decliner_seed,
        address="bob^example.com",
        payload_type=RelationshipDecline.PAYLOAD_TYPE,
        payload=decline.to_dict(),
    )

    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())
    await adapter_fixture._handle_relationship_decline(env)

    assert not RelationshipStore.load(tmp_hermes_home).has_friend("bob^example.com")
    assert PendingProposalStore.load(tmp_hermes_home).take_outbound("prop-2") is None


# -- relationship_revoke → deletes existing record --------------------------


@pytest.mark.asyncio
async def test_handle_relationship_revoke_deletes_record(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    from aap.relationships import RelationshipRecord, RelationshipStore

    # Seed a friend relationship to revoke.
    # Write directly to the adapter's in-memory store so the handler sees it.
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))
    assert RelationshipStore.load(tmp_hermes_home).has_friend("mary^example.com")

    revoker_seed, revoker_public = generate_keypair()
    revoke = RelationshipRevoke(relationship_type="friend", nonce="revoke-1")
    env = _sign_envelope(
        seed=revoker_seed,
        address="mary^example.com",
        payload_type=RelationshipRevoke.PAYLOAD_TYPE,
        payload=revoke.to_dict(),
    )

    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())
    await adapter_fixture._handle_relationship_revoke(env, revoker_public)

    assert not RelationshipStore.load(tmp_hermes_home).has_friend("mary^example.com")


# -- service_followup_grant → persisted as 'received' -----------------------


@pytest.mark.asyncio
async def test_handle_followup_grant_persisted(adapter_fixture, tmp_hermes_home):
    from aap.service_followups import FollowupGrantStore

    customer_seed, customer_public = generate_keypair()
    grant = ServiceFollowupGrant(
        service_id="routine-cleaning",
        cadence_iso="P6M",
        outreach_window_before="P1M",
        valid_until="2027-05-26T00:00:00Z",
        nonce="grant-1",
    )
    env = _sign_envelope(
        seed=customer_seed,
        address="patient^example.com",
        payload_type=ServiceFollowupGrant.PAYLOAD_TYPE,
        payload=grant.to_dict(),
    )

    await adapter_fixture._handle_service_followup_grant(env, customer_public)

    store = FollowupGrantStore.load(tmp_hermes_home)
    row = store.find_received(
        customer_address="patient^example.com",
        service_id="routine-cleaning",
    )
    assert row is not None
    assert row.cadence_iso == "P6M"


# -- service_followup → validated against issued-grant store ---------------


@pytest.mark.asyncio
async def test_handle_followup_without_matching_grant_dropped(
    adapter_fixture, monkeypatch
):
    """Inbound followup referencing an unknown grant nonce is silently dropped."""
    biz_seed, _ = generate_keypair()
    fu = ServiceFollowup(
        service_id="routine-cleaning",
        grant_nonce="unknown-grant",
        message="Time for your cleaning!",
        nonce="fu-1",
    )
    env = _sign_envelope(
        seed=biz_seed,
        address="reception^drsmith.example",
        payload_type=ServiceFollowup.PAYLOAD_TYPE,
        payload=fu.to_dict(),
    )

    mirror_mock = MagicMock()
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", mirror_mock)
    await adapter_fixture._handle_service_followup(env)
    # No user-facing prompt because the grant nonce didn't resolve.
    mirror_mock.assert_not_called()


# -- chat gate: drop strangers, accept friends ------------------------------


def _make_chat_envelope(seed, address, text="hi"):
    return _sign_envelope(
        seed=seed,
        address=address,
        payload_type="aap.message/v1",
        payload={"text": text},
    )


@pytest.mark.asyncio
async def test_chat_from_stranger_dropped(adapter_fixture, monkeypatch):
    """No relationship → adapter drops the envelope without dispatching to LLM."""
    handler_mock = AsyncMock()
    adapter_fixture._message_handler = handler_mock
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    stranger_seed, stranger_pub = generate_keypair()
    env = _make_chat_envelope(stranger_seed, "stranger^example.com")
    # Bypass peer-key lookup: pre-seed the adapter's cache.
    adapter_fixture.peer_keys["stranger^example.com"] = stranger_pub

    await adapter_fixture._dispatch({"id": 1, "body": env.to_json()})

    # No dispatch to the LLM.
    handler_mock.assert_not_called()


@pytest.mark.asyncio
async def test_chat_from_friend_dispatched(adapter_fixture, tmp_hermes_home, monkeypatch):
    """Friend relationship → adapter passes the message through to the LLM."""
    from aap.relationships import RelationshipRecord

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "mary^example.com"

    # Write directly to the adapter's in-memory store so the dispatch gate sees it.
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    handler_mock = AsyncMock()
    adapter_fixture._message_handler = handler_mock
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    env = _make_chat_envelope(friend_seed, friend_addr, text="Saturday?")
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 2, "body": env.to_json()})

    handler_mock.assert_called_once()
    event = handler_mock.call_args.args[0]
    # The trust preamble names the relationship type; original text follows.
    assert "friend" in event.text.lower()
    assert "Saturday?" in event.text


@pytest.mark.asyncio
async def test_chat_from_recent_outbound_contact_accepted(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    """If we contacted a peer recently (e.g., a business we asked something),
    their chat reply is accepted within the reply window. This restores the
    business-reply path without opening a stranger-spam vector."""

    biz_seed, biz_pub = generate_keypair()
    biz_addr = "reception^frankies.example"

    # Simulate that we sent something to this business a moment ago.
    # Write directly to the adapter's in-memory store so the dispatch gate sees it.
    adapter_fixture.stores.outbound_contacts.record(biz_addr)

    handler_mock = AsyncMock()
    adapter_fixture._message_handler = handler_mock
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    env = _make_chat_envelope(biz_seed, biz_addr, text="Got your booking — confirmed for 7pm")
    adapter_fixture.peer_keys[biz_addr] = biz_pub

    await adapter_fixture._dispatch({"id": 3, "body": env.to_json()})

    handler_mock.assert_called_once()
    event = handler_mock.call_args.args[0]
    assert "24h reply window" in event.text
    assert "Got your booking" in event.text


@pytest.mark.asyncio
async def test_chat_from_fake_business_with_catalog_but_no_outbound_dropped(
    adapter_fixture, monkeypatch
):
    """Critical anti-spam: a peer who publishes a catalog and tries to
    initiate contact with us must STILL be dropped. The accept signal
    is OUR outbound contact, not the peer's claim to be a business."""
    fake_seed, fake_pub = generate_keypair()
    fake_addr = "reception^evil-fake-restaurant.example"

    # No outbound contact recorded. No relationship. Even if this peer
    # publishes a catalog at /.well-known/aap-services, the chat gate
    # must not consult the catalog — it must drop.

    handler_mock = AsyncMock()
    adapter_fixture._message_handler = handler_mock
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    env = _make_chat_envelope(fake_seed, fake_addr, text="Hi! Special offer just for you!")
    adapter_fixture.peer_keys[fake_addr] = fake_pub

    await adapter_fixture._dispatch({"id": 4, "body": env.to_json()})

    handler_mock.assert_not_called()


# -- no-reply detector + dispatch loop-breaker -----------------------------


def test_is_no_reply_explicit_sentinel():
    from aap_hermes.adapter import _is_no_reply

    assert _is_no_reply("[NO_REPLY]")
    assert _is_no_reply("  [no_reply]  ")
    assert _is_no_reply("[No-Reply]")
    assert _is_no_reply("[no reply]")


def test_is_no_reply_parenthetical_meta():
    from aap_hermes.adapter import _is_no_reply

    assert _is_no_reply("(No reply — closing acknowledgment.)")
    assert _is_no_reply("(Thread idle — no reply.)")
    assert _is_no_reply("(No reply.)")
    assert _is_no_reply("(No reply — not a substantive message.)")
    assert _is_no_reply("(Closing — thread is idle, awaiting user.)")


def test_is_no_reply_substantive_text_passes():
    from aap_hermes.adapter import _is_no_reply

    assert not _is_no_reply("Yes, let's migrate to Firebase.")
    assert not _is_no_reply("(See attached document.)")  # paren but no meta phrase
    assert not _is_no_reply("Sounds good. [NO_REPLY] is just a placeholder mention.")


def test_is_no_reply_empty_and_whitespace():
    from aap_hermes.adapter import _is_no_reply

    assert _is_no_reply("")
    assert _is_no_reply("   \n\t  ")
    assert _is_no_reply(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_dispatch_skips_send_on_no_reply_sentinel(adapter_fixture, tmp_hermes_home, monkeypatch):
    """LLM's [NO_REPLY] response must NOT produce an outbound envelope."""
    from aap.relationships import RelationshipRecord

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "closer^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    # Handler returns the sentinel — adapter must NOT call self.send.
    adapter_fixture._message_handler = AsyncMock(return_value="[NO_REPLY]")
    adapter_fixture.send = AsyncMock()
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    env = _make_chat_envelope(friend_seed, friend_addr, text="ok thanks")
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 5, "body": env.to_json()})

    adapter_fixture.send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_skips_send_on_parenthetical_no_reply(adapter_fixture, tmp_hermes_home, monkeypatch):
    """Parenthetical-only meta-comments must not be shipped — closes the
    runaway-acknowledgment loop seen in the wild."""
    from aap.relationships import RelationshipRecord

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "looper^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    adapter_fixture._message_handler = AsyncMock(
        return_value="(No reply — closing acknowledgment.)"
    )
    adapter_fixture.send = AsyncMock()
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    env = _make_chat_envelope(friend_seed, friend_addr, text="ok thanks!")
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 6, "body": env.to_json()})

    adapter_fixture.send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_auto_sends_substantive_reply(adapter_fixture, tmp_hermes_home, monkeypatch):
    """The original v0.6 bug: substantive LLM responses must be shipped
    back to the peer.  Regression guard for the silent-reply bug."""
    from aap.relationships import RelationshipRecord

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "talker^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    adapter_fixture._message_handler = AsyncMock(
        return_value="Sure — Saturday works for me."
    )
    adapter_fixture.send = AsyncMock()
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    env = _make_chat_envelope(friend_seed, friend_addr, text="Saturday?")
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 7, "body": env.to_json()})

    adapter_fixture.send.assert_called_once()
    kwargs = adapter_fixture.send.call_args.kwargs
    assert kwargs["chat_id"] == friend_addr
    assert "Saturday works" in kwargs["content"]


@pytest.mark.asyncio
async def test_dispatch_auto_reply_echoes_inbound_thread_id(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    """A 1:1 auto-reply must echo the inbound ``thread_id`` so the peer can
    route the reply back into the originating conversation. Regression guard
    for the thread_id=None reply bug."""
    from datetime import datetime, timezone
    from aap.relationships import RelationshipRecord
    from aap.messages import build_chat_envelope

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "talker^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    thread_id = "eb933f79-7160-4ebd-8c40-ac4995d899e0"
    adapter_fixture._message_handler = AsyncMock(return_value="chicken nuggets")
    # Exercise the REAL adapter.send so it reaches client.send_envelope.
    adapter_fixture.client.send_envelope = AsyncMock(return_value=1)
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    fresh_iat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    env = build_chat_envelope(
        seed=friend_seed,
        sender_address=friend_addr,
        text="What's for dinner?",
        iat=fresh_iat,
        thread_id=thread_id,
    )
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 8, "body": env.to_json()})

    adapter_fixture.client.send_envelope.assert_called_once()
    kwargs = adapter_fixture.client.send_envelope.call_args.kwargs
    assert kwargs["to"] == friend_addr
    assert kwargs.get("thread_id") == thread_id


@pytest.mark.asyncio
async def test_dispatch_records_inbound_thread_for_peer(
    adapter_fixture, tmp_hermes_home, monkeypatch
):
    """An authorized 1:1 inbound records the sender's thread_id so a later
    cross-platform reply (e.g. via Telegram) can thread back to it."""
    from datetime import datetime, timezone
    from aap.relationships import RelationshipRecord
    from aap.messages import build_chat_envelope
    from aap_hermes import peer_threads

    friend_seed, friend_pub = generate_keypair()
    friend_addr = "talker^example.com"
    adapter_fixture.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=friend_addr,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    thread_id = "1f0e0a6c-6d0f-4a2b-9c3d-aa11bb22cc33"
    adapter_fixture._message_handler = AsyncMock(return_value="[NO_REPLY]")
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    fresh_iat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    env = build_chat_envelope(
        seed=friend_seed,
        sender_address=friend_addr,
        text="What's for dinner?",
        iat=fresh_iat,
        thread_id=thread_id,
    )
    adapter_fixture.peer_keys[friend_addr] = friend_pub

    await adapter_fixture._dispatch({"id": 9, "body": env.to_json()})

    assert peer_threads.last_inbound_thread(friend_addr) == thread_id
