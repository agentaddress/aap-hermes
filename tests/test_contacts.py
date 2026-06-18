"""Tests for the file-based contact source."""

import json
from pathlib import Path

import pytest

from aap_hermes.contacts import ContactSource


@pytest.fixture
def contacts_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path / "aap-contacts.json"


def _seed(contacts_path: Path, payload: dict):
    contacts_path.parent.mkdir(parents=True, exist_ok=True)
    contacts_path.write_text(json.dumps(payload))


def test_empty_when_file_missing(contacts_path):
    source = ContactSource.load()
    assert source.find_by_phone("+14154442222") is None
    assert source.find_by_email("james@example.com") is None


def test_find_by_phone(contacts_path):
    _seed(contacts_path, {
        "contacts": [
            {
                "id": "james-lane",
                "display_name": "James Lane",
                "phones": ["+14154442222"],
                "emails": ["james@example.com"],
            },
        ]
    })
    source = ContactSource.load()
    match = source.find_by_phone("+14154442222")
    assert match is not None
    assert match.display_name == "James Lane"
    assert match.id == "james-lane"


def test_find_by_email(contacts_path):
    _seed(contacts_path, {
        "contacts": [
            {
                "id": "james-lane",
                "display_name": "James Lane",
                "phones": [],
                "emails": ["james@example.com"],
            },
        ]
    })
    source = ContactSource.load()
    match = source.find_by_email("james@example.com")
    assert match is not None
    assert match.display_name == "James Lane"


def test_normalises_phone_for_comparison(contacts_path):
    _seed(contacts_path, {
        "contacts": [
            {
                "id": "james-lane",
                "display_name": "James Lane",
                "phones": ["+1 (415) 444-2222"],
                "emails": [],
            },
        ]
    })
    source = ContactSource.load()
    assert source.find_by_phone("+14154442222") is not None


def test_email_case_insensitive(contacts_path):
    _seed(contacts_path, {
        "contacts": [
            {
                "id": "x",
                "display_name": "X",
                "phones": [],
                "emails": ["James@Example.COM"],
            },
        ]
    })
    source = ContactSource.load()
    assert source.find_by_email("james@example.com") is not None


def test_get_by_id(contacts_path):
    _seed(contacts_path, {
        "contacts": [
            {"id": "x", "display_name": "X", "phones": [], "emails": []},
        ]
    })
    source = ContactSource.load()
    assert source.get_by_id("x").display_name == "X"
    assert source.get_by_id("nope") is None
