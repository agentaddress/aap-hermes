"""Tests for AAP_* env-driven settings."""

import pytest

from aap_hermes.config import DEFAULT_TRUST_LIST_PUBLIC_KEY_B64, Settings, build_address


_TRUST_ROOT = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_defaults():
    s = Settings(AAP_LOCALPART="chris", AAP_TRUST_LIST_PUBLIC_KEY_B64=_TRUST_ROOT)
    assert s.AAP_LOCALPART == "chris"
    assert s.AAP_INSTANCE_DOMAIN == "agentaddress.org"
    assert s.AAP_RELAY_URL == "https://api.agentaddress.org"
    assert s.AAP_TRUST_LIST_PUBLIC_KEY_B64 == _TRUST_ROOT
    assert s.AAP_PRIVATE_SEED_B64 is None
    assert s.AAP_HTTP_TIMEOUT_SECONDS == 35


def test_hosted_trust_root_default(monkeypatch):
    monkeypatch.delenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", raising=False)
    s = Settings(AAP_LOCALPART="chris")
    assert s.AAP_TRUST_LIST_PUBLIC_KEY_B64 == DEFAULT_TRUST_LIST_PUBLIC_KEY_B64


def test_localpart_required():
    """Missing AAP_LOCALPART should error."""
    with pytest.raises(ValueError):
        Settings()


def test_address_construction():
    s = Settings(
        AAP_LOCALPART="chris",
        AAP_INSTANCE_DOMAIN="custom.dev",
        AAP_TRUST_LIST_PUBLIC_KEY_B64=_TRUST_ROOT,
    )
    assert build_address(s) == "chris^custom.dev"


def test_env_override_for_seed(monkeypatch):
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", _TRUST_ROOT)
    monkeypatch.setenv("AAP_PRIVATE_SEED_B64", "abc123")
    s = Settings()
    assert s.AAP_PRIVATE_SEED_B64 == "abc123"
