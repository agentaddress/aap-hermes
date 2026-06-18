"""Tests for the SDK trust-list + verifier-key API Hermes wires into."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from aap.envelope import Envelope
from aap.keys import encode_b64url, generate_keypair
from aap.verifiers import (
    TRUSTED_VERIFIERS_ISSUER,
    TRUSTED_VERIFIERS_PAYLOAD_TYPE,
    TrustListCache,
    VerifierPubkeyCache,
    trusted_verifiers_supporting,
)


_TRUST_LIST_URL = "https://api.agentaddress.org/.well-known/aap-trusted-verifiers"
_ROOT_SEED, _ROOT_PUBLIC = generate_keypair()
_, _VERIFIER_PUBLIC = generate_keypair()
_VERIFIER_PUBLIC_B64 = encode_b64url(_VERIFIER_PUBLIC)

_TRUST_LIST_BODY = {
    "publisher": "agentaddressprotocol.org",
    "version": "2026-06-16",
    "verifiers": [
        {
            "domain": "verify.aap.org",
            "supported_identities": ["phone", "email"],
            "discovery_endpoint": "https://verify.aap.org/aap/discover",
            "verification_endpoint": "https://verify.aap.org/aap/verify",
            "pubkey_endpoint": "https://verify.aap.org/.well-known/aap-verifier-key",
            "public_key": _VERIFIER_PUBLIC_B64,
            "policy_url": "https://verify.aap.org/policy",
            "trust_score": "established",
        }
    ],
}


def _make_cache(tmp_path: Path) -> TrustListCache:
    return TrustListCache(
        cache_path=tmp_path / "aap-trusted-verifiers.json",
        overrides_path=tmp_path / "aap-trusted-verifiers-overrides.json",
        trust_list_public_key=_ROOT_PUBLIC,
        url=_TRUST_LIST_URL,
    )


def _trust_list_envelope_json(body: dict = _TRUST_LIST_BODY, *, seed: bytes = _ROOT_SEED) -> str:
    return Envelope(
        type="aap.envelope/v1",
        payload_type=TRUSTED_VERIFIERS_PAYLOAD_TYPE,
        payload=body,
        iss=TRUSTED_VERIFIERS_ISSUER,
        iat=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ).sign(seed).to_json()


def _write_overrides(home: Path, payload: dict) -> None:
    (home / "aap-trusted-verifiers-overrides.json").write_text(json.dumps(payload))


@respx.mock
@pytest.mark.asyncio
async def test_fetch_parse_and_cache_signed_list(tmp_path):
    route = respx.get(_TRUST_LIST_URL).mock(
        return_value=httpx.Response(200, text=_trust_list_envelope_json()),
    )
    cache = _make_cache(tmp_path)
    try:
        entries = await cache.get()
    finally:
        await cache.aclose()
    assert route.call_count == 1
    assert [e.domain for e in entries] == ["verify.aap.org"]
    data = json.loads((tmp_path / "aap-trusted-verifiers.json").read_text())
    assert "envelope_json" in data
    assert "fetched_at" in data


@respx.mock
@pytest.mark.asyncio
async def test_rejects_unsigned_trust_list_response(tmp_path):
    respx.get(_TRUST_LIST_URL).mock(
        return_value=httpx.Response(200, json=_TRUST_LIST_BODY),
    )
    cache = _make_cache(tmp_path)
    try:
        entries = await cache.get()
    finally:
        await cache.aclose()
    assert entries == []
    assert not (tmp_path / "aap-trusted-verifiers.json").exists()


@respx.mock
@pytest.mark.asyncio
async def test_local_override_requires_public_key(tmp_path):
    respx.get(_TRUST_LIST_URL).mock(
        return_value=httpx.Response(200, text=_trust_list_envelope_json()),
    )
    _write_overrides(
        tmp_path,
        {
            "add": [
                {
                    "domain": "extra.example",
                    "supported_identities": ["phone"],
                    "discovery_endpoint": "https://extra.example/aap/discover",
                    "verification_endpoint": "https://extra.example/aap/verify",
                    "pubkey_endpoint": "https://extra.example/.well-known/aap-verifier-key",
                    "public_key": _VERIFIER_PUBLIC_B64,
                }
            ],
        },
    )
    cache = _make_cache(tmp_path)
    try:
        entries = await cache.get()
    finally:
        await cache.aclose()
    assert {e.domain for e in entries} == {"verify.aap.org", "extra.example"}


@respx.mock
@pytest.mark.asyncio
async def test_trusted_verifiers_supporting_filters_by_identity_type(tmp_path):
    respx.get(_TRUST_LIST_URL).mock(
        return_value=httpx.Response(200, text=_trust_list_envelope_json()),
    )
    cache = _make_cache(tmp_path)
    try:
        entries = await cache.get()
    finally:
        await cache.aclose()
    assert [v.domain for v in trusted_verifiers_supporting(entries, "phone")] == [
        "verify.aap.org"
    ]
    assert trusted_verifiers_supporting(entries, "government-id") == []


@respx.mock
@pytest.mark.asyncio
async def test_verifier_pubkey_comes_from_signed_trust_list(tmp_path):
    pubkey_route = respx.get("https://verify.aap.org/.well-known/aap-verifier-key").mock(
        return_value=httpx.Response(200, json={"public_key": "bad"}),
    )
    respx.get(_TRUST_LIST_URL).mock(
        return_value=httpx.Response(200, text=_trust_list_envelope_json()),
    )
    tl_cache = _make_cache(tmp_path)
    pk_cache = VerifierPubkeyCache(cache_dir=tmp_path / "aap-verifier-keys")
    try:
        trust_list = await tl_cache.get()
        first = await pk_cache.get("verify.aap.org", trust_list)
        second = await pk_cache.get("verify.aap.org", trust_list)
    finally:
        await tl_cache.aclose()
        await pk_cache.aclose()
    assert first == _VERIFIER_PUBLIC
    assert second == _VERIFIER_PUBLIC
    assert pubkey_route.call_count == 0


@respx.mock
@pytest.mark.asyncio
async def test_verifier_pubkey_returns_none_for_untrusted_domain(tmp_path):
    respx.get(_TRUST_LIST_URL).mock(
        return_value=httpx.Response(200, text=_trust_list_envelope_json()),
    )
    tl_cache = _make_cache(tmp_path)
    pk_cache = VerifierPubkeyCache(cache_dir=tmp_path / "aap-verifier-keys")
    try:
        trust_list = await tl_cache.get()
        result = await pk_cache.get("untrusted.example", trust_list)
    finally:
        await tl_cache.aclose()
        await pk_cache.aclose()
    assert result is None
