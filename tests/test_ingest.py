"""Unit tests for aap_hermes.ingest.persist_user_message."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from aap_hermes import ingest


@dataclass
class _FakeSource:
    chat_id: str = "peer^example.org"


@dataclass
class _FakeEntry:
    session_id: str


@pytest.fixture
def fake_runner(monkeypatch):
    """Install a fake gateway runner with session_store + _session_db mocks."""
    session_store = MagicMock()
    session_store.get_or_create_session.return_value = _FakeEntry(
        session_id="20260610_120000_abcd1234",
    )
    session_db = MagicMock()
    session_db.append_message.return_value = 42

    runner = MagicMock()
    runner.session_store = session_store
    runner._session_db = session_db

    monkeypatch.setattr(ingest, "_get_runner", lambda: runner)
    return runner


def test_persist_user_message_writes_to_db(fake_runner):
    sid = ingest.persist_user_message(
        _FakeSource(),
        "hello peer",
        message_id="env-7",
    )

    assert sid == "20260610_120000_abcd1234"
    fake_runner.session_store.get_or_create_session.assert_called_once()
    fake_runner._session_db.append_message.assert_called_once_with(
        "20260610_120000_abcd1234",
        role="user",
        content="hello peer",
        platform_message_id="env-7",
        observed=False,
    )


def test_persist_user_message_no_runner(monkeypatch):
    monkeypatch.setattr(ingest, "_get_runner", lambda: None)

    result = ingest.persist_user_message(_FakeSource(), "hello")

    assert result is None


def test_persist_user_message_runner_missing_attrs(monkeypatch):
    """If runner exists but session_store/_session_db are missing, we no-op."""
    runner = MagicMock(spec=["unrelated"])
    monkeypatch.setattr(ingest, "_get_runner", lambda: runner)

    result = ingest.persist_user_message(_FakeSource(), "hello")

    assert result is None


def test_persist_user_message_swallows_get_or_create_failure(monkeypatch, caplog):
    runner = MagicMock()
    runner.session_store.get_or_create_session.side_effect = RuntimeError("boom")
    runner._session_db.append_message = MagicMock()
    monkeypatch.setattr(ingest, "_get_runner", lambda: runner)

    with caplog.at_level("ERROR", logger=ingest.logger.name):
        result = ingest.persist_user_message(_FakeSource(), "hello")

    assert result is None
    runner._session_db.append_message.assert_not_called()
    assert any("get_or_create_session failed" in r.message for r in caplog.records)


def test_persist_user_message_swallows_append_failure(monkeypatch, caplog):
    runner = MagicMock()
    runner.session_store.get_or_create_session.return_value = _FakeEntry(
        session_id="sess-1",
    )
    runner._session_db.append_message.side_effect = RuntimeError("disk full")
    monkeypatch.setattr(ingest, "_get_runner", lambda: runner)

    with caplog.at_level("ERROR", logger=ingest.logger.name):
        result = ingest.persist_user_message(_FakeSource(), "hello")

    assert result is None
    assert any("append_message failed" in r.message for r in caplog.records)


def test_persist_user_message_passes_observed_flag(fake_runner):
    ingest.persist_user_message(_FakeSource(), "ack", observed=True)
    _, kwargs = fake_runner._session_db.append_message.call_args
    assert kwargs["observed"] is True
