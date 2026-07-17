"""Unit tests for the per-peer last-inbound-thread_id store.

Backs the cross-platform reply-threading fallback: when a reply to an AAP
peer is produced from a turn that has no AAP thread of its own (e.g. the human
answers via Telegram), aap_send_message echoes the peer's most recent inbound
thread_id so the reply threads back into the originating conversation.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_record_then_read_roundtrips(tmp_hermes_home):
    from aap_hermes import peer_threads

    peer_threads.record_inbound_thread("mary^example.com", "T-1")
    assert peer_threads.last_inbound_thread("mary^example.com") == "T-1"


def test_unknown_peer_returns_none(tmp_hermes_home):
    from aap_hermes import peer_threads

    assert peer_threads.last_inbound_thread("nobody^example.com") is None


def test_threads_are_isolated_per_peer(tmp_hermes_home):
    """peerA->Ta and peerB->Tb never cross — the two-address reply case."""
    from aap_hermes import peer_threads

    peer_threads.record_inbound_thread("a^example.com", "Ta")
    peer_threads.record_inbound_thread("b^example.com", "Tb")

    assert peer_threads.last_inbound_thread("a^example.com") == "Ta"
    assert peer_threads.last_inbound_thread("b^example.com") == "Tb"


def test_most_recent_overwrites_for_same_peer(tmp_hermes_home):
    from aap_hermes import peer_threads

    peer_threads.record_inbound_thread("mary^example.com", "T-old")
    peer_threads.record_inbound_thread("mary^example.com", "T-new")
    assert peer_threads.last_inbound_thread("mary^example.com") == "T-new"


def test_none_thread_id_is_not_recorded(tmp_hermes_home):
    """A threadless inbound must not clobber a previously recorded thread."""
    from aap_hermes import peer_threads

    peer_threads.record_inbound_thread("mary^example.com", "T-1")
    peer_threads.record_inbound_thread("mary^example.com", None)
    assert peer_threads.last_inbound_thread("mary^example.com") == "T-1"


def test_persists_across_process(tmp_hermes_home, monkeypatch):
    """State survives a fresh import (written to disk, not just in-memory)."""
    from aap_hermes import peer_threads

    peer_threads.record_inbound_thread("mary^example.com", "T-persist")

    # Re-resolve the path from disk directly to prove it was written.
    import json
    from pathlib import Path
    data = json.loads((tmp_hermes_home / "aap-peer-threads.json").read_text())
    assert data["threads"]["mary^example.com"] == "T-persist"
