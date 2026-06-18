"""Tests for the identity-binding (TOFU) store."""

import pytest

from aap.stores.identity_bindings import IdentityBindingStore


@pytest.fixture
def hermes_home(tmp_path):
    return tmp_path


def test_empty_store(hermes_home):
    store = IdentityBindingStore.load(hermes_home)
    assert store.binding_for("foo^x.com") is None


def test_bind_and_lookup(hermes_home):
    store = IdentityBindingStore.load(hermes_home)
    store.bind(
        peer_address="james-bot^james-bots.example",
        contact_id="james-lane",
        matched_identifier={"type": "phone", "value": "+14154442222"},
    )
    assert (hermes_home / "aap-identity-bindings.json").exists()

    reloaded = IdentityBindingStore.load(hermes_home)
    binding = reloaded.binding_for("james-bot^james-bots.example")
    assert binding is not None
    assert binding.contact_id == "james-lane"
    assert binding.matched_identifier["value"] == "+14154442222"


def test_unbind(hermes_home):
    store = IdentityBindingStore.load(hermes_home)
    store.bind(
        peer_address="x^y.com",
        contact_id="alice",
        matched_identifier={"type": "email", "value": "alice@x.com"},
    )
    assert store.binding_for("x^y.com") is not None
    store.unbind("x^y.com")
    assert store.binding_for("x^y.com") is None


def test_list_bindings_for_contact(hermes_home):
    store = IdentityBindingStore.load(hermes_home)
    store.bind(
        peer_address="bot1^example.com",
        contact_id="alice",
        matched_identifier={"type": "phone", "value": "+1"},
    )
    store.bind(
        peer_address="bot2^example.com",
        contact_id="alice",
        matched_identifier={"type": "email", "value": "a@x"},
    )
    addrs = store.addresses_bound_to("alice")
    assert set(addrs) == {"bot1^example.com", "bot2^example.com"}
