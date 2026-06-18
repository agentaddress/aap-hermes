"""Tests for group-envelope builders."""

from aap.keys import generate_keypair

from aap.group_flow import (
    build_group_invitation_envelope,
    build_group_membership_update_envelope,
    build_group_leave_envelope,
)


def test_group_invitation_envelope_signed_and_decodable():
    seed, public = generate_keypair()
    env = build_group_invitation_envelope(
        convener_seed=seed,
        convener_address="chris^example.com",
        conversation_id="dinner-abc",
        purpose="Plan dinner",
        members=[
            "chris^example.com",
            "james^example.com",
            "sarah^example.com",
        ],
    )
    assert env.payload_type == "aap.group-invitation/v1"
    assert env.iss == "chris^example.com"
    assert env.verify(public)
    assert env.payload["conversation_id"] == "dinner-abc"
    assert env.payload["convener"] == "chris^example.com"
    assert len(env.payload["members"]) == 3


def test_group_membership_update_envelope():
    seed, public = generate_keypair()
    env = build_group_membership_update_envelope(
        convener_seed=seed,
        convener_address="chris^example.com",
        conversation_id="dinner-abc",
        members=[
            "chris^example.com",
            "james^example.com",
            "alice^example.com",
        ],
        added=["alice^example.com"],
        removed=[],
        convener_changed_from=None,
    )
    assert env.payload_type == "aap.group-membership-update/v1"
    assert env.verify(public)
    assert env.payload["added"] == ["alice^example.com"]


def test_group_leave_envelope():
    seed, public = generate_keypair()
    env = build_group_leave_envelope(
        leaver_seed=seed,
        leaver_address="james^example.com",
        conversation_id="dinner-abc",
        reason="Got busy",
    )
    assert env.payload_type == "aap.group-leave/v1"
    assert env.verify(public)
    assert env.payload["reason"] == "Got busy"
