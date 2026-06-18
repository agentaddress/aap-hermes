"""Tests for /aap inspect transcript helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _write_session_fixture(home: Path) -> None:
    sessions_dir = home / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "sessions.json").write_text(json.dumps({
        "agent:main:telegram:dm:123": {
            "session_id": "home-session",
            "updated_at": "2026-05-29T20:54:32",
        },
        "agent:main:aap:dm:aap-group:conv-abc": {
            "session_id": "group-session",
            "updated_at": "2026-05-29T20:55:32",
        },
        "agent:main:aap:dm:bookings^example.com": {
            "session_id": "peer-session",
            "updated_at": "2026-05-29T20:56:32",
        },
    }))

    db = sqlite3.connect(home / "state.db")
    try:
        db.execute(
            """
            create table messages (
                id integer primary key autoincrement,
                session_id text,
                role text,
                content text,
                tool_call_id text,
                tool_calls text,
                tool_name text,
                timestamp real
            )
            """
        )
        db.executemany(
            """
            insert into messages
                (session_id, role, content, tool_calls, tool_name, timestamp)
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                ("home-session", "user", "8pm Tuesday", "", "", 1000),
                (
                    "group-session",
                    "assistant",
                    "",
                    json.dumps([
                        {
                            "function": {
                                "name": "aap_group_send",
                                "arguments": '{"text":"Chris can do 8pm Tuesday"}',
                            }
                        }
                    ]),
                    "",
                    1001,
                ),
                ("peer-session", "tool", '{"status":"confirmed"}', "", "book-table", 1002),
            ],
        )
        db.commit()
    finally:
        db.close()


def test_inspect_lists_local_channel_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_session_fixture(tmp_path)

    from aap_hermes.commands import _inspect_cmd

    out = _inspect_cmd([])

    assert "Home channels: 1" in out
    assert "AAP groups: 1" in out
    assert "AAP peers/services: 1" in out
    assert "conv-abc" in out
    assert "bookings^example.com" in out


def test_inspect_group_shows_hidden_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_session_fixture(tmp_path)

    from aap_hermes.commands import _inspect_cmd

    out = _inspect_cmd(["group", "conv-abc"])

    assert "AAP group conv-abc" in out
    assert "group-session" in out
    assert "aap_group_send" in out
    assert "Chris can do 8pm Tuesday" in out


def test_inspect_peer_and_home_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_session_fixture(tmp_path)

    from aap_hermes.commands import _inspect_cmd

    peer = _inspect_cmd(["peer", "bookings^example.com"])
    home = _inspect_cmd(["home"])

    assert "AAP peer/service bookings^example.com" in peer
    assert "book-table" in peer
    assert "confirmed" in peer
    assert "Home channel" in home
    assert "8pm Tuesday" in home
