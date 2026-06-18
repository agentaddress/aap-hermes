"""Unit tests for aap_hermes.anti_relay session-sender predicate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from aap_hermes import anti_relay


@dataclass
class _FakeOrigin:
    chat_id: str


@dataclass
class _FakeEntry:
    session_id: str
    origin: Any


@pytest.fixture
def runner_with_messages(monkeypatch):
    """Runner whose session_db returns a fixed list of message dicts."""

    def _make(rows):
        runner = MagicMock()
        runner._session_db.get_messages.return_value = rows
        monkeypatch.setattr(anti_relay, "_get_runner", lambda: runner)
        return runner

    return _make


def test_peers_extracts_agent_prefix(runner_with_messages):
    runner_with_messages([
        {"role": "user", "content": "[Agent alice^x.org]: hello"},
        {"role": "user", "content": "[Agent bob^x.org]: ack"},
        {"role": "assistant", "content": "noted"},
    ])
    peers = anti_relay.peers_who_have_messaged_session("sess-1")
    assert peers == {"alice^x.org", "bob^x.org"}


def test_peers_ignores_non_user_rows(runner_with_messages):
    runner_with_messages([
        {"role": "assistant", "content": "[Agent fake^x.org]: noted"},
        {"role": "tool", "content": "[Agent fake^x.org]: tool out"},
        {"role": "user", "content": "[Agent real^x.org]: hi"},
    ])
    assert anti_relay.peers_who_have_messaged_session("sess-1") == {
        "real^x.org",
    }


def test_peers_ignores_unlabelled_user_rows(runner_with_messages):
    runner_with_messages([
        {"role": "user", "content": "plain user input from telegram"},
        {"role": "user", "content": "[Agent bob^x.org]: hi"},
    ])
    assert anti_relay.peers_who_have_messaged_session("sess-1") == {
        "bob^x.org",
    }


def test_peers_handles_non_str_content(runner_with_messages):
    runner_with_messages([
        {"role": "user", "content": [{"type": "text", "text": "weird"}]},
        {"role": "user", "content": "[Agent bob^x.org]: hi"},
    ])
    assert anti_relay.peers_who_have_messaged_session("sess-1") == {
        "bob^x.org",
    }


def test_peers_returns_empty_when_no_runner(monkeypatch):
    monkeypatch.setattr(anti_relay, "_get_runner", lambda: None)
    assert anti_relay.peers_who_have_messaged_session("sess-1") == set()


def test_peers_returns_empty_when_db_raises(monkeypatch, caplog):
    runner = MagicMock()
    runner._session_db.get_messages.side_effect = RuntimeError("disk")
    monkeypatch.setattr(anti_relay, "_get_runner", lambda: runner)
    with caplog.at_level("ERROR"):
        assert anti_relay.peers_who_have_messaged_session("sess-1") == set()


def test_is_peer_allowed(runner_with_messages):
    runner_with_messages([
        {"role": "user", "content": "[Agent alice^x.org]: hi"},
    ])
    assert anti_relay.is_peer_allowed_in_session(
        "sess-1", "alice^x.org",
    )
    assert not anti_relay.is_peer_allowed_in_session(
        "sess-1", "eve^x.org",
    )


def test_resolve_session_id_for_chat(monkeypatch):
    runner = MagicMock()
    runner.session_store._entries = {
        "k1": _FakeEntry(
            session_id="sess-1",
            origin=_FakeOrigin(chat_id="aap-group:c-1"),
        ),
        "k2": _FakeEntry(
            session_id="sess-2",
            origin=_FakeOrigin(chat_id="peer^x"),
        ),
    }
    monkeypatch.setattr(anti_relay, "_get_runner", lambda: runner)
    assert anti_relay.resolve_session_id_for_chat("aap-group:c-1") == "sess-1"
    assert anti_relay.resolve_session_id_for_chat("peer^x") == "sess-2"
    assert anti_relay.resolve_session_id_for_chat("nope") is None


def test_resolve_session_id_no_runner(monkeypatch):
    monkeypatch.setattr(anti_relay, "_get_runner", lambda: None)
    assert anti_relay.resolve_session_id_for_chat("any") is None
