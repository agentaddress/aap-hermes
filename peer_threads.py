"""Per-peer last-inbound-thread_id store.

The AAP ``thread_id`` on a reply lets the peer route it back into the
originating conversation. The natural in-session auto-reply echoes it from the
turn's ``SessionSource`` (see ``turn_context.reply_thread_id_for``). But when a
reply is produced from a turn that has *no* AAP thread of its own — e.g. the
human answers a mirrored AAP message from Telegram, and the LLM ships the reply
via ``aap_send_message`` — that turn's source is the home platform, not the
peer, so it carries no thread.

This store bridges that gap: each authorized 1:1 inbound records the sender's
thread_id here, keyed by peer address, and the outbound reply path falls back
to it. It relies on the "one active thread per AAP address at a time" property
— replies are disambiguated by *which peer* (the ``to`` address), and each peer
has a single live thread, so the most-recent value is unambiguous. Concurrent
threads from the *same* peer are out of scope.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _store_path() -> Path:
    home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    return Path(home) / "aap-peer-threads.json"


def _load() -> dict[str, str]:
    path = _store_path()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    threads = data.get("threads")
    return dict(threads) if isinstance(threads, dict) else {}


def record_inbound_thread(peer: str, thread_id: Optional[str]) -> None:
    """Remember ``thread_id`` as ``peer``'s most recent inbound thread.

    A ``None``/empty ``thread_id`` is ignored so a threadless inbound never
    clobbers a previously recorded thread.
    """
    if not thread_id:
        return
    threads = _load()
    if threads.get(peer) == thread_id:
        return
    threads[peer] = thread_id
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"threads": threads}, indent=2))
    except OSError:
        logger.exception("Could not persist peer thread for %s", peer)


def last_inbound_thread(peer: str) -> Optional[str]:
    """Return ``peer``'s most recent inbound thread_id, or ``None``."""
    return _load().get(peer)
