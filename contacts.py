"""Read-only contact source.

For v0.6.0 MVP, contacts are loaded from a flat JSON file at
$HERMES_HOME/aap-contacts.json. Future versions can layer in OS-native
sources (macOS Contacts.app, CardDAV, etc.).

Schema:
{
  "contacts": [
    {
      "id": "<stable identifier>",
      "display_name": "James Lane",
      "phones": ["+14154442222", ...],
      "emails": ["james@example.com", ...]
    },
    ...
  ]
}
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _contacts_path() -> Path:
    home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    return Path(home) / "aap-contacts.json"


_PHONE_STRIP = re.compile(r"[ \-()\.]")


def _normalize_phone(s: str) -> str:
    return _PHONE_STRIP.sub("", s)


def _normalize_email(s: str) -> str:
    return s.strip().lower()


@dataclass(frozen=True)
class Contact:
    id: str
    display_name: str
    phones: tuple[str, ...]
    emails: tuple[str, ...]


@dataclass
class ContactSource:
    contacts: list[Contact]

    @classmethod
    def load(cls) -> "ContactSource":
        path = _contacts_path()
        if not path.exists():
            return cls(contacts=[])
        try:
            data = json.loads(path.read_text())
        except Exception:
            logger.exception("Failed to load %s; treating as empty", path)
            return cls(contacts=[])
        return cls(
            contacts=[
                Contact(
                    id=c["id"],
                    display_name=c["display_name"],
                    phones=tuple(c.get("phones") or []),
                    emails=tuple(c.get("emails") or []),
                )
                for c in data.get("contacts") or []
            ]
        )

    def find_by_phone(self, phone: str) -> Optional[Contact]:
        target = _normalize_phone(phone)
        for c in self.contacts:
            for p in c.phones:
                if _normalize_phone(p) == target:
                    return c
        return None

    def find_by_email(self, email: str) -> Optional[Contact]:
        target = _normalize_email(email)
        for c in self.contacts:
            for e in c.emails:
                if _normalize_email(e) == target:
                    return c
        return None

    def get_by_id(self, contact_id: str) -> Optional[Contact]:
        for c in self.contacts:
            if c.id == contact_id:
                return c
        return None
