"""Tests for host policy: token lifetime and renewal decisions."""

from aap.host_policy import (
    is_high_risk_scope,
    token_lifetime_days,
    should_auto_renew,
)


def test_wildcard_scope_is_high_risk():
    assert is_high_risk_scope("*") is True


def test_namespaced_scopes_not_high_risk_without_catalog():
    """v0.7.0: without catalog lookup, only wildcard is high-risk by default.
    Catalog-aware callers should consult the publisher's ``risk`` field."""
    assert is_high_risk_scope("dentabook.ai/read-appointments") is False
    assert is_high_risk_scope("dentabook.ai/book-cleaning") is False


def test_lifetime_low_risk_scopes_default_30_days():
    assert token_lifetime_days(["foo.example/read-bar"], requested=90) == 30


def test_lifetime_wildcard_clamped_to_7_days():
    assert token_lifetime_days(["*"], requested=365) == 7


def test_lifetime_honors_requested_when_smaller():
    assert token_lifetime_days(["foo.example/read-bar"], requested=5) == 5


def test_lifetime_mixed_with_wildcard_uses_minimum():
    """If any scope is high-risk (wildcard), the whole token gets the
    high-risk lifetime cap."""
    assert token_lifetime_days(["foo.example/read-bar", "*"], requested=90) == 7


def test_should_auto_renew_low_risk_silent():
    assert should_auto_renew(
        scopes=["foo.example/read-bar"],
        peer_currently_approved=True,
        scope_expansion=False,
    ) is True


def test_should_auto_renew_high_risk_always_re_prompt():
    assert should_auto_renew(
        scopes=["*"],
        peer_currently_approved=True,
        scope_expansion=False,
    ) is False


def test_should_auto_renew_scope_expansion_re_prompts():
    assert should_auto_renew(
        scopes=["foo.example/read-bar"],
        peer_currently_approved=True,
        scope_expansion=True,
    ) is False


def test_should_auto_renew_unapproved_peer():
    assert should_auto_renew(
        scopes=["foo.example/read-bar"],
        peer_currently_approved=False,
        scope_expansion=False,
    ) is False
