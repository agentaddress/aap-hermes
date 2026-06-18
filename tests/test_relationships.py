"""Tests for the relationships module — record store + handshake envelope builders."""

from __future__ import annotations

import pytest

from aap.envelope import Envelope
from aap.keys import encode_b64url, generate_keypair
from aap.payloads import (
    AgentCard,
    RelationshipAccept,
    RelationshipDecline,
    RelationshipProposal,
    RelationshipRevoke,
)

from aap.relationships import (
    RelationshipRecord,
    RelationshipStore,
    build_relationship_accept_envelope,
    build_relationship_decline_envelope,
    build_relationship_proposal_envelope,
    build_relationship_revoke_envelope,
)


@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _friend_record(peer: str = "mary^example.com") -> RelationshipRecord:
    return RelationshipRecord(
        relationship_type="friend",
        peer_address=peer,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json='{"a":1}',
        accept_envelope_json='{"b":2}',
    )


def _team_record(
    peer: str = "dev^example.com",
    resource: str = "github.com/acme/widgets",
) -> RelationshipRecord:
    return RelationshipRecord(
        relationship_type="team",
        peer_address=peer,
        resource=resource,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json='{}',
        accept_envelope_json='{}',
    )


def _agent_card_envelope_json(seed: bytes, public_key: bytes, address: str) -> str:
    domain = address.split("^", 1)[1]
    card = AgentCard(
        address=address,
        did=f"did:web:{domain}#agent",
        public_key=encode_b64url(public_key),
        endpoints=[{"type": "didcomm", "uri": f"https://{domain}"}],
    )
    return Envelope(
        type="aap.envelope/v1",
        payload_type=AgentCard.PAYLOAD_TYPE,
        payload=card.to_dict(),
        iss=address,
        iat="2026-05-26T12:00:00Z",
    ).sign(seed).to_json()


def _establish(
    store: RelationshipStore,
    *,
    self_address: str = "john^example.com",
    peer_address: str = "mary^example.com",
    relationship_type: str = "friend",
    resource: str | None = None,
    iat: str = "2026-05-26T12:00:00Z",
):
    self_seed, self_public = generate_keypair()
    peer_seed, peer_public = generate_keypair()
    proposal = build_relationship_proposal_envelope(
        seed=peer_seed,
        sender_address=peer_address,
        relationship_type=relationship_type,
        proposer_card_envelope_json=_agent_card_envelope_json(
            peer_seed, peer_public, peer_address
        ),
        resource=resource,
        nonce=f"proposal-{peer_address}-{relationship_type}-{resource or 'none'}-{iat}",
        iat=iat,
    )
    accept = build_relationship_accept_envelope(
        seed=self_seed,
        sender_address=self_address,
        proposal_nonce=RelationshipProposal.from_dict(proposal.payload).nonce,
        accepter_card_envelope_json=_agent_card_envelope_json(
            self_seed, self_public, self_address
        ),
        iat=iat,
    )
    record = store.establish(
        self_address=self_address,
        peer_address=peer_address,
        proposal_envelope_json=proposal.to_json(),
        accept_envelope_json=accept.to_json(),
        proposer_public_key=peer_public,
        accepter_public_key=self_public,
    )
    return record, {
        "self_seed": self_seed,
        "self_public": self_public,
        "peer_seed": peer_seed,
        "peer_public": peer_public,
        "self_address": self_address,
        "peer_address": peer_address,
    }


# -- store CRUD -------------------------------------------------------------


def test_empty_store_when_no_file(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    assert store.list_all() == []


def test_add_friend_persists(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _establish(store)
    reloaded = RelationshipStore.load(tmp_hermes_home)
    records = reloaded.list_all()
    assert len(records) == 1
    assert records[0].peer_address == "mary^example.com"
    assert records[0].relationship_type == "friend"


def test_has_friend_query(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _establish(store)
    assert store.has_friend("mary^example.com")
    assert not store.has_friend("bob^example.com")


def test_admin_and_team_distinguished_from_friend(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _establish(store)
    assert not store.has_admin("mary^example.com")
    assert not store.has_team("mary^example.com", "anything")


def test_team_requires_resource_match(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _establish(
        store,
        peer_address="dev^example.com",
        relationship_type="team",
        resource="github.com/acme/widgets",
    )
    assert store.has_team("dev^example.com", "github.com/acme/widgets")
    assert not store.has_team("dev^example.com", "github.com/other/repo")


def test_team_without_resource_raises(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    with pytest.raises(ValueError, match="team proposal requires"):
        _establish(
            store,
            peer_address="x^example.com",
            relationship_type="team",
            resource=None,
        )


def test_revoke_removes_record(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _, keys = _establish(store)
    revoke = build_relationship_revoke_envelope(
        seed=keys["peer_seed"],
        sender_address=keys["peer_address"],
        relationship_type="friend",
        nonce="revoke-friend-1",
    )
    assert store.revoke(
        self_address=keys["self_address"],
        peer_address=keys["peer_address"],
        revoke_envelope_json=revoke.to_json(),
        revoker_public_key=keys["peer_public"],
    )
    assert not store.has_friend("mary^example.com")
    # Idempotent — second revoke returns False.
    revoke2 = build_relationship_revoke_envelope(
        seed=keys["peer_seed"],
        sender_address=keys["peer_address"],
        relationship_type="friend",
        nonce="revoke-friend-2",
    )
    assert not store.revoke(
        self_address=keys["self_address"],
        peer_address=keys["peer_address"],
        revoke_envelope_json=revoke2.to_json(),
        revoker_public_key=keys["peer_public"],
    )


def test_revoke_team_requires_resource_match(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _, keys = _establish(
        store,
        peer_address="dev^example.com",
        relationship_type="team",
        resource="github.com/acme/widgets",
    )
    # Wrong resource doesn't match.
    wrong = build_relationship_revoke_envelope(
        seed=keys["peer_seed"],
        sender_address=keys["peer_address"],
        relationship_type="team",
        resource="github.com/other/repo",
        nonce="revoke-team-wrong",
    )
    assert not store.revoke(
        self_address=keys["self_address"],
        peer_address=keys["peer_address"],
        revoke_envelope_json=wrong.to_json(),
        revoker_public_key=keys["peer_public"],
    )
    # Right resource does.
    right = build_relationship_revoke_envelope(
        seed=keys["peer_seed"],
        sender_address=keys["peer_address"],
        relationship_type="team",
        resource="github.com/acme/widgets",
        nonce="revoke-team-right",
    )
    assert store.revoke(
        self_address=keys["self_address"],
        peer_address=keys["peer_address"],
        revoke_envelope_json=right.to_json(),
        revoker_public_key=keys["peer_public"],
    )


def test_add_replaces_existing_same_kind(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _establish(store)
    # Establish another friend record for the same peer — should replace, not duplicate.
    _establish(store, iat="2026-05-27T12:00:00Z")
    records = [r for r in store.list_all() if r.peer_address == "mary^example.com"]
    assert len(records) == 1
    assert records[0].established_at == "2026-05-27T12:00:00Z"


def test_distinct_team_resources_coexist_for_same_peer(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    _establish(
        store,
        peer_address="dev^example.com",
        relationship_type="team",
        resource="github.com/acme/widgets",
    )
    _establish(
        store,
        peer_address="dev^example.com",
        relationship_type="team",
        resource="github.com/acme/gadgets",
    )
    teams = [r for r in store.list_all() if r.relationship_type == "team"]
    assert len(teams) == 2


def test_invalid_relationship_type_rejected(tmp_hermes_home):
    store = RelationshipStore.load(tmp_hermes_home)
    with pytest.raises(ValueError, match="invalid relationship_type"):
        _establish(
            store,
            peer_address="x^example.com",
            relationship_type="frenemies",
        )


# -- envelope builders ------------------------------------------------------


def test_proposal_envelope_signs_and_verifies():
    seed, pub = generate_keypair()
    env = build_relationship_proposal_envelope(
        seed=seed,
        sender_address="mary^example.com",
        relationship_type="friend",
        proposer_card_envelope_json='{"card": "mary-card"}',
    )
    assert env.payload_type == RelationshipProposal.PAYLOAD_TYPE
    assert env.payload["relationship_type"] == "friend"
    assert env.payload["nonce"]
    assert env.verify(pub)


def test_proposal_team_requires_resource():
    seed, _ = generate_keypair()
    with pytest.raises(ValueError, match="team proposal requires"):
        build_relationship_proposal_envelope(
            seed=seed,
            sender_address="x^example.com",
            relationship_type="team",
            proposer_card_envelope_json='{}',
        )


def test_proposal_invalid_type_rejected():
    seed, _ = generate_keypair()
    with pytest.raises(ValueError, match="invalid relationship_type"):
        build_relationship_proposal_envelope(
            seed=seed,
            sender_address="x^example.com",
            relationship_type="frenemies",
            proposer_card_envelope_json='{}',
        )


def test_accept_envelope_signs_and_verifies():
    seed, pub = generate_keypair()
    env = build_relationship_accept_envelope(
        seed=seed,
        sender_address="john^example.com",
        proposal_nonce="prop-1",
        accepter_card_envelope_json='{"card": "john-card"}',
    )
    assert env.payload_type == RelationshipAccept.PAYLOAD_TYPE
    assert env.payload["proposal_nonce"] == "prop-1"
    assert env.verify(pub)


def test_decline_envelope_signs_and_verifies():
    seed, pub = generate_keypair()
    env = build_relationship_decline_envelope(
        seed=seed,
        sender_address="john^example.com",
        proposal_nonce="prop-1",
        reason="don't know you",
    )
    assert env.payload_type == RelationshipDecline.PAYLOAD_TYPE
    assert env.payload["reason"] == "don't know you"
    assert env.verify(pub)


def test_revoke_envelope_signs_and_verifies():
    seed, pub = generate_keypair()
    env = build_relationship_revoke_envelope(
        seed=seed,
        sender_address="john^example.com",
        relationship_type="friend",
    )
    assert env.payload_type == RelationshipRevoke.PAYLOAD_TYPE
    assert env.payload["relationship_type"] == "friend"
    assert env.verify(pub)


def test_revoke_invalid_type_rejected():
    seed, _ = generate_keypair()
    with pytest.raises(ValueError, match="invalid relationship_type"):
        build_relationship_revoke_envelope(
            seed=seed,
            sender_address="x^example.com",
            relationship_type="frenemies",
        )
