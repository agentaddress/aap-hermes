"""Tests for the local attestation store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aap.envelope import Envelope
from aap.keys import generate_keypair
from aap.payloads import VerificationAttestation

from aap.stores.attestations import AttestationStore


VERIFIER_SEED, VERIFIER_PUBLIC = generate_keypair()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_attestation_envelope(
    *,
    subject_address: str = "chris^agentaddress.org",
    identity_type: str = "phone",
    identity_value: str = "+14155551111",
    verifier_domain: str = "verify.aap.org",
    verified_at: datetime | None = None,
    expires_at: datetime | None = None,
    nonce: str = "test-nonce-1",
) -> str:
    """Build a signed VerificationAttestation envelope for tests."""
    verified_at = verified_at or _now()
    expires_at = expires_at or (verified_at + timedelta(days=365))
    payload = VerificationAttestation(
        subject_address=subject_address,
        identity={"type": identity_type, "value": identity_value},
        challenge_method="sms-otp",
        verified_at=_rfc3339(verified_at),
        expires_at=_rfc3339(expires_at),
        verifier=verifier_domain,
        nonce=nonce,
    )
    env = Envelope(
        type="aap.envelope/v1",
        payload_type=VerificationAttestation.PAYLOAD_TYPE,
        payload=payload.to_dict(),
        iss=verifier_domain,
        iat=_rfc3339(verified_at),
    ).sign(VERIFIER_SEED)
    return env.to_json()


def _record(store: AttestationStore, envelope_json: str):
    return store.record(envelope_json, verifier_public_key=VERIFIER_PUBLIC)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_record_and_round_trip(hermes_home):
    store = AttestationStore.load(hermes_home)
    env_json = _build_attestation_envelope()
    _record(store, env_json)

    reloaded = AttestationStore.load(hermes_home)
    held = reloaded.held_for("phone")
    assert len(held) == 1
    assert held[0].identifier_value == "+14155551111"
    assert held[0].verifier == "verify.aap.org"
    assert held[0].attestation_envelope_json == env_json


def test_held_for_filters_by_identity_type(hermes_home):
    store = AttestationStore.load(hermes_home)
    _record(store, _build_attestation_envelope(identity_type="phone"))
    _record(
        store,
        _build_attestation_envelope(
            identity_type="email", identity_value="chris@example.com", nonce="e1"
        ),
    )
    assert len(store.held_for("phone")) == 1
    assert len(store.held_for("email")) == 1
    assert store.held_for("government-id") == []


def test_matching_returns_attestation_satisfying_constraints(hermes_home):
    store = AttestationStore.load(hermes_home)
    _record(store, _build_attestation_envelope())

    match = store.matching(
        identity_type="phone",
        verifiers_oneof=["verify.aap.org", "twilio.com"],
        max_age_days=365,
    )
    assert match is not None
    assert match.identifier_value == "+14155551111"


def test_matching_rejects_when_verifier_not_in_list(hermes_home):
    store = AttestationStore.load(hermes_home)
    _record(store, _build_attestation_envelope())

    match = store.matching(
        identity_type="phone",
        verifiers_oneof=["different.example"],
        max_age_days=365,
    )
    assert match is None


def test_matching_rejects_expired_attestation(hermes_home):
    store = AttestationStore.load(hermes_home)
    past = _now() - timedelta(days=400)
    expired_exp = _now() - timedelta(days=1)
    _record(
        store,
        _build_attestation_envelope(verified_at=past, expires_at=expired_exp)
    )
    match = store.matching(
        identity_type="phone", verifiers_oneof=["verify.aap.org"], max_age_days=365
    )
    assert match is None


def test_matching_rejects_attestation_older_than_max_age(hermes_home):
    store = AttestationStore.load(hermes_home)
    old = _now() - timedelta(days=400)
    future_exp = _now() + timedelta(days=200)
    _record(
        store,
        _build_attestation_envelope(verified_at=old, expires_at=future_exp)
    )
    match = store.matching(
        identity_type="phone",
        verifiers_oneof=["verify.aap.org"],
        max_age_days=90,
    )
    assert match is None


def test_remove_expired_drops_expired_only(hermes_home):
    store = AttestationStore.load(hermes_home)
    past = _now() - timedelta(days=400)
    expired_exp = _now() - timedelta(days=1)
    _record(
        store,
        _build_attestation_envelope(
            verified_at=past, expires_at=expired_exp, nonce="exp-1"
        ),
    )
    _record(store, _build_attestation_envelope(nonce="active-1"))
    removed = store.remove_expired()
    assert removed == 1
    held = store.held_for("phone")
    assert len(held) == 1
    assert held[0].verified_at != _rfc3339(past)


def test_record_rejects_envelope_with_wrong_payload_type(hermes_home):
    """Refuses to store a non-attestation envelope (defensive)."""
    seed, _pub = generate_keypair()
    env = Envelope(
        type="aap.envelope/v1",
        payload_type="aap.message/v1",
        payload={"text": "hi"},
        iss="verify.aap.org",
        iat="2026-05-22T00:00:00Z",
    ).sign(seed)
    store = AttestationStore.load(hermes_home)
    with pytest.raises(ValueError):
        _record(store, env.to_json())


def test_load_returns_empty_when_file_missing(hermes_home):
    store = AttestationStore.load(hermes_home)
    assert store.held_for("phone") == []
    assert store.held_for("email") == []
