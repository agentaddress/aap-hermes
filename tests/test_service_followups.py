"""Tests for the service_followups module — grant store + envelope builders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aap.keys import generate_keypair
from aap.payloads import ServiceFollowup, ServiceFollowupGrant

from aap.service_followups import (
    FollowupGrantStore,
    build_followup_envelope,
    build_followup_grant_envelope,
    parse_iso_duration,
)


@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# -- ISO 8601 duration parser ----------------------------------------------


def test_parse_iso_duration_months():
    assert parse_iso_duration("P6M") == timedelta(days=180)


def test_parse_iso_duration_years():
    assert parse_iso_duration("P1Y") == timedelta(days=365)


def test_parse_iso_duration_mixed():
    # 1 month + 2 days = 32 days
    assert parse_iso_duration("P1M2D") == timedelta(days=32)


def test_parse_iso_duration_time_component():
    assert parse_iso_duration("PT2H30M") == timedelta(hours=2, minutes=30)


def test_parse_iso_duration_rejects_garbage():
    with pytest.raises(ValueError):
        parse_iso_duration("six-months")


def test_parse_iso_duration_rejects_bare_p():
    with pytest.raises(ValueError):
        parse_iso_duration("P")


# -- envelope builders ------------------------------------------------------


def test_build_grant_envelope_signs_and_verifies():
    seed, pub = generate_keypair()
    env = build_followup_grant_envelope(
        seed=seed,
        sender_address="john^example.com",
        service_id="routine-cleaning",
        cadence_iso="P6M",
        outreach_window_before="P1M",
        valid_until="2027-05-26T00:00:00Z",
    )
    assert env.payload_type == ServiceFollowupGrant.PAYLOAD_TYPE
    assert env.payload["cadence_iso"] == "P6M"
    assert env.verify(pub)


def test_build_followup_envelope_signs_and_verifies():
    seed, pub = generate_keypair()
    env = build_followup_envelope(
        seed=seed,
        sender_address="reception^drsmith.example",
        service_id="routine-cleaning",
        grant_nonce="grant-1",
        message="Time for your next cleaning!",
        suggested_slots=["2026-11-10T09:00:00Z"],
    )
    assert env.payload_type == ServiceFollowup.PAYLOAD_TYPE
    assert env.payload["grant_nonce"] == "grant-1"
    assert env.verify(pub)


# -- FollowupGrantStore -----------------------------------------------------


def _make_grant_envelope(
    *,
    sender: str = "john^example.com",
    service_id: str = "routine-cleaning",
    cadence: str = "P6M",
    window: str = "P1M",
    valid_until: str = "2027-05-26T00:00:00Z",
    iat: str | None = None,
):
    seed, public = generate_keypair()
    env = build_followup_grant_envelope(
        seed=seed,
        sender_address=sender,
        service_id=service_id,
        cadence_iso=cadence,
        outreach_window_before=window,
        valid_until=valid_until,
        iat=iat,
    )
    return env, public


def test_record_issued_and_find(tmp_hermes_home):
    store = FollowupGrantStore.load(tmp_hermes_home)
    env, public = _make_grant_envelope(sender="reception^drsmith.example")
    row = store.record_issued(
        business_address="reception^drsmith.example",
        grant_envelope_json=env.to_json(),
        business_public_key=public,
    )
    assert row.direction == "issued"
    assert row.service_id == "routine-cleaning"

    reloaded = FollowupGrantStore.load(tmp_hermes_home)
    found = reloaded.find_issued(
        business_address="reception^drsmith.example",
        service_id="routine-cleaning",
    )
    assert found is not None
    assert found.cadence_iso == "P6M"


def test_record_received_and_find(tmp_hermes_home):
    store = FollowupGrantStore.load(tmp_hermes_home)
    env, public = _make_grant_envelope(sender="john^example.com")
    store.record_received(
        customer_address="john^example.com",
        grant_envelope_json=env.to_json(),
        customer_public_key=public,
    )
    found = store.find_received(
        customer_address="john^example.com",
        service_id="routine-cleaning",
    )
    assert found is not None


def test_record_rejects_non_grant_envelope(tmp_hermes_home):
    """A regular chat envelope is NOT a grant — store rejects it."""
    from aap.messages import build_chat_envelope
    seed, public = generate_keypair()
    chat_env = build_chat_envelope(
        seed=seed,
        sender_address="reception^drsmith.example",
        text="hi",
        iat="2026-05-26T12:00:00Z",
    )
    store = FollowupGrantStore.load(tmp_hermes_home)
    with pytest.raises(ValueError, match="expected .* envelope"):
        store.record_issued(
            business_address="reception^drsmith.example",
            grant_envelope_json=chat_env.to_json(),
            business_public_key=public,
        )


def test_issued_and_received_for_same_peer_coexist(tmp_hermes_home):
    """A user can simultaneously have issued a grant to a business AND
    received one from that same address (if they're somehow both customer
    and business to each other). Storage keys on (direction, counterparty,
    service_id) to allow this."""
    store = FollowupGrantStore.load(tmp_hermes_home)
    issued_env, issued_public = _make_grant_envelope(sender="peer^example.com")
    received_env, received_public = _make_grant_envelope(
        sender="peer^example.com",
        iat="2026-05-26T12:01:00Z",
    )
    store.record_issued(
        business_address="peer^example.com",
        grant_envelope_json=issued_env.to_json(),
        business_public_key=issued_public,
    )
    store.record_received(
        customer_address="peer^example.com",
        grant_envelope_json=received_env.to_json(),
        customer_public_key=received_public,
    )
    assert store.find_issued(
        business_address="peer^example.com", service_id="routine-cleaning"
    ) is not None
    assert store.find_received(
        customer_address="peer^example.com", service_id="routine-cleaning"
    ) is not None


def test_re_recording_same_service_replaces(tmp_hermes_home):
    """If a customer re-grants the same service, the new grant replaces the old."""
    store = FollowupGrantStore.load(tmp_hermes_home)
    env_a, public_a = _make_grant_envelope(
        sender="reception^drsmith.example", iat="2026-05-26T12:00:00Z"
    )
    env_b, public_b = _make_grant_envelope(
        sender="reception^drsmith.example", iat="2026-06-01T12:00:00Z"
    )
    store.record_issued(
        business_address="reception^drsmith.example",
        grant_envelope_json=env_a.to_json(),
        business_public_key=public_a,
    )
    store.record_issued(
        business_address="reception^drsmith.example",
        grant_envelope_json=env_b.to_json(),
        business_public_key=public_b,
    )
    rows = [
        r for r in store.rows
        if r.counterparty == "reception^drsmith.example"
        and r.service_id == "routine-cleaning"
    ]
    assert len(rows) == 1
    assert rows[0].issued_at == "2026-06-01T12:00:00Z"


def test_stamp_used_records_timestamp(tmp_hermes_home):
    store = FollowupGrantStore.load(tmp_hermes_home)
    env, public = _make_grant_envelope(sender="reception^drsmith.example")
    row = store.record_issued(
        business_address="reception^drsmith.example",
        grant_envelope_json=env.to_json(),
        business_public_key=public,
    )
    assert row.last_used_at is None
    assert store.stamp_used(row.nonce)
    updated = store.find_issued_by_nonce(row.nonce)
    assert updated.last_used_at is not None


def test_revoke_removes_row(tmp_hermes_home):
    store = FollowupGrantStore.load(tmp_hermes_home)
    env, public = _make_grant_envelope(sender="reception^drsmith.example")
    store.record_issued(
        business_address="reception^drsmith.example",
        grant_envelope_json=env.to_json(),
        business_public_key=public,
    )
    assert store.revoke(
        counterparty="reception^drsmith.example",
        service_id="routine-cleaning",
        direction="issued",
    )
    assert store.find_issued(
        business_address="reception^drsmith.example",
        service_id="routine-cleaning",
    ) is None


# -- outreach window logic --------------------------------------------------


def test_outreach_window_blocked_too_early(tmp_hermes_home):
    """A 6mo cadence with a 1mo outreach window means the business can only
    reach out between 5mo and 6mo+ after the last use. Anything earlier
    fails the window check."""
    env, public = _make_grant_envelope(sender="john^example.com")
    store = FollowupGrantStore.load(tmp_hermes_home)
    row = store.record_received(
        customer_address="john^example.com",
        grant_envelope_json=env.to_json(),
        customer_public_key=public,
    )
    # 30 days in, 6mo cadence, 1mo window → far too early
    assert not row.is_within_outreach_window(
        now=datetime.now(timezone.utc) + timedelta(days=30)
    )


def test_outreach_window_allowed_inside_window(tmp_hermes_home):
    """A grant issued 5+ months ago is now inside the outreach window."""
    env, public = _make_grant_envelope(sender="john^example.com")
    store = FollowupGrantStore.load(tmp_hermes_home)
    row = store.record_received(
        customer_address="john^example.com",
        grant_envelope_json=env.to_json(),
        customer_public_key=public,
    )
    assert row.is_within_outreach_window(
        now=datetime.now(timezone.utc) + timedelta(days=160)
    )


def test_grant_lifetime_expiry(tmp_hermes_home):
    """A grant past its valid_until is no longer in lifetime."""
    env, public = _make_grant_envelope(
        sender="john^example.com", valid_until="2020-01-01T00:00:00Z"
    )
    store = FollowupGrantStore.load(tmp_hermes_home)
    row = store.record_received(
        customer_address="john^example.com",
        grant_envelope_json=env.to_json(),
        customer_public_key=public,
    )
    assert not row.is_within_lifetime()
