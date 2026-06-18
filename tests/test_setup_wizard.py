"""Tests for the BYOD domain-claim flow used by `hermes gateway setup`.

Covers the new ``_run_domain_claim_flow`` helper end-to-end against a
mocked relay. The interactive prompt-loop paths around it (in
``_setup_byod_path`` and ``_setup_hosted_path``) are exercised manually
via ``hermes gateway setup`` — they're thin glue over this helper plus
``_run_claim_flow``, which is unchanged.
"""

import httpx
import respx

from aap.envelope import Envelope
from aap.keys import encode_b64url, generate_keypair
from aap_hermes import _run_claim_flow, _run_domain_claim_flow, _submit_address_claim


class _Recorder:
    """Captures the print_*_fn callbacks the wizard passes in."""

    def __init__(self):
        self.info = []
        self.warning = []
        self.success = []


def _make_prompt(values):
    """Build a prompt_fn that returns the next value on each call.

    The wizard calls prompt_fn three times in the happy path: contact email,
    press-Enter-to-continue, and OTP code.
    """
    it = iter(values)
    return lambda *args, **kwargs: next(it)


def test_happy_path_returns_true_and_shows_token():
    rec = _Recorder()
    with respx.mock(base_url="https://relay.test") as mock:
        mock.post("/aap/domains/claim-start").mock(
            return_value=httpx.Response(
                200, json={"claim_token": "TOKENABC", "otp_id": "otp123"}
            )
        )
        mock.post("/aap/domains/claim-confirm").mock(
            return_value=httpx.Response(
                201, json={"account_id": 7, "domain": "example.com", "tier": "free"}
            )
        )
        ok = _run_domain_claim_flow(
            relay_url="https://relay.test",
            domain="example.com",
            prompt_fn=_make_prompt(["chris@example.com", "", "555111"]),
            print_info_fn=rec.info.append,
            print_warning_fn=rec.warning.append,
            print_success_fn=rec.success.append,
        )
    assert ok is True
    # Operator MUST see the token they need to host at .well-known.
    assert any("TOKENABC" in m for m in rec.info)
    # Success message names the domain.
    assert any("example.com" in m for m in rec.success)


def test_409_from_start_treated_as_success():
    """domain_already_claimed is fine — a new agent registers under the
    existing account. The wizard should not block."""
    rec = _Recorder()
    with respx.mock(base_url="https://relay.test") as mock:
        mock.post("/aap/domains/claim-start").mock(
            return_value=httpx.Response(409, json={"error": "domain_already_claimed"})
        )
        ok = _run_domain_claim_flow(
            relay_url="https://relay.test",
            domain="example.com",
            prompt_fn=_make_prompt(["chris@example.com"]),
            print_info_fn=rec.info.append,
            print_warning_fn=rec.warning.append,
            print_success_fn=rec.success.append,
        )
    assert ok is True
    assert any("already claimed" in m for m in rec.info)


def test_empty_contact_email_aborts():
    rec = _Recorder()
    ok = _run_domain_claim_flow(
        relay_url="https://relay.test",
        domain="example.com",
        prompt_fn=_make_prompt([""]),
        print_info_fn=rec.info.append,
        print_warning_fn=rec.warning.append,
        print_success_fn=rec.success.append,
    )
    assert ok is False
    assert any("required" in w.lower() for w in rec.warning)


def test_confirm_rejects_with_bad_otp():
    rec = _Recorder()
    with respx.mock(base_url="https://relay.test") as mock:
        mock.post("/aap/domains/claim-start").mock(
            return_value=httpx.Response(200, json={"claim_token": "T", "otp_id": "o"})
        )
        mock.post("/aap/domains/claim-confirm").mock(
            return_value=httpx.Response(401, json={"error": "invalid_otp"})
        )
        ok = _run_domain_claim_flow(
            relay_url="https://relay.test",
            domain="example.com",
            prompt_fn=_make_prompt(["chris@example.com", "", "wrong-otp"]),
            print_info_fn=rec.info.append,
            print_warning_fn=rec.warning.append,
            print_success_fn=rec.success.append,
        )
    assert ok is False
    assert any("claim-confirm failed" in w for w in rec.warning)


def test_start_network_error_aborts_cleanly():
    rec = _Recorder()
    with respx.mock(base_url="https://relay.test") as mock:
        mock.post("/aap/domains/claim-start").mock(
            side_effect=httpx.ConnectError("relay unreachable")
        )
        ok = _run_domain_claim_flow(
            relay_url="https://relay.test",
            domain="example.com",
            prompt_fn=_make_prompt(["chris@example.com"]),
            print_info_fn=rec.info.append,
            print_warning_fn=rec.warning.append,
            print_success_fn=rec.success.append,
        )
    assert ok is False
    assert any("Could not reach relay" in w for w in rec.warning)


def test_missing_claim_token_in_response_aborts():
    rec = _Recorder()
    with respx.mock(base_url="https://relay.test") as mock:
        mock.post("/aap/domains/claim-start").mock(
            return_value=httpx.Response(200, json={"otp_id": "o"})  # no claim_token
        )
        ok = _run_domain_claim_flow(
            relay_url="https://relay.test",
            domain="example.com",
            prompt_fn=_make_prompt(["chris@example.com"]),
            print_info_fn=rec.info.append,
            print_warning_fn=rec.warning.append,
            print_success_fn=rec.success.append,
        )
    assert ok is False
    assert any("claim_token" in w for w in rec.warning)


def test_address_claim_publishes_persisted_encryption_key():
    seed, public = generate_keypair()
    public_key_b64 = encode_b64url(public)
    encryption_public_key_b64 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    with respx.mock(base_url="https://relay.test") as mock:
        route = mock.post("/aap/addresses/claim").mock(
            return_value=httpx.Response(201, json={"address": "alice^example.com"})
        )
        ok, message = _submit_address_claim(
            relay_url="https://relay.test",
            seed=seed,
            public_key_b64=public_key_b64,
            encryption_public_key_b64=encryption_public_key_b64,
            localpart="alice",
            domain="example.com",
            attestation_envelope_dict={"type": "aap.envelope/v1"},
        )

    assert ok is True
    assert message == "alice^example.com"

    claim_env = Envelope.from_json(route.calls.last.request.content.decode())
    card_env = Envelope.from_dict(claim_env.payload["agent_card_envelope"])
    assert card_env.payload["encryption_key"] == encryption_public_key_b64


def test_recovery_claim_flow_generates_and_persists_encryption_keys(monkeypatch, tmp_path):
    rec = _Recorder()
    submitted = {}
    persisted = {}

    def fake_drive_email_verification(**kwargs):
        return {"type": "aap.envelope/v1", "payload": {"email": "alice@example.com"}}

    def fake_submit_address_rotate(**kwargs):
        submitted.update(kwargs)
        return True, "alice^example.com"

    def fake_persist_identity(private_seed, public_key, encryption_private_key, encryption_public_key, address, home):
        persisted.update(
            {
                "private_seed": private_seed,
                "public_key": public_key,
                "encryption_private_key": encryption_private_key,
                "encryption_public_key": encryption_public_key,
                "address": address,
                "home": home,
            }
        )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("aap_hermes._drive_email_verification", fake_drive_email_verification)
    monkeypatch.setattr("aap_hermes._submit_address_rotate", fake_submit_address_rotate)
    monkeypatch.setattr("aap_hermes._persist_identity", fake_persist_identity)

    result = _run_claim_flow(
        relay_url="https://relay.test",
        verifier_url="https://verify.test",
        domain="example.com",
        localpart="alice",
        base_localpart="alice",
        recovery_mode=True,
        prompt_fn=_make_prompt(["alice@example.com"]),
        prompt_yes_no_fn=lambda *args, **kwargs: False,
        print_info_fn=rec.info.append,
        print_warning_fn=rec.warning.append,
        print_success_fn=rec.success.append,
    )

    assert result == "claimed"
    assert submitted["encryption_public_key_b64"]
    assert submitted["encryption_public_key_b64"] == encode_b64url(
        persisted["encryption_public_key"]
    )
    assert persisted["encryption_private_key"]
    assert persisted["address"] == "alice^example.com"
    assert persisted["home"] == tmp_path
