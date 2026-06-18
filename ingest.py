"""Phase 1 ingest: write inbound AAP user messages to the session DB.

The AAP adapter persists inbound envelopes directly to SQLite before any LLM
turn runs. The reasoning scheduler (phase 2) then triggers an LLM turn with
a no-content "tick" event; the turn loads conversation history from the DB
and sees the just-written user row.

This module is the seam between the AAP adapter and Hermes's session store
APIs (``gateway.session.SessionStore`` and ``hermes_state.SessionDB``). It
exists so the dispatch hot path stays free of hermes-core import gymnastics
and so the write can be unit-tested without standing up a gateway.

The design and rationale live in
``docs/superpowers/plans/2026-06-10-ingest-vs-reason-redesign.md``.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def persist_user_message(
    source,
    content: str,
    *,
    message_id: Optional[str] = None,
    observed: bool = False,
) -> Optional[str]:
    """Resolve the session for ``source`` and append a user-role row.

    Returns the resolved ``session_id`` on success, ``None`` if the gateway
    runner isn't reachable (e.g. tests without a live gateway) or the write
    failed. Failures are logged; we do not raise — losing the durable write
    is bad, but propagating an exception into the dispatch loop is worse.
    """
    runner = _get_runner()
    if runner is None:
        logger.debug(
            "persist_user_message skipped: gateway runner unavailable "
            "(chat_id=%s)",
            getattr(source, "chat_id", "?"),
        )
        return None

    session_store = getattr(runner, "session_store", None)
    session_db = getattr(runner, "_session_db", None)
    if session_store is None or session_db is None:
        logger.warning(
            "persist_user_message skipped: runner missing session_store "
            "or _session_db (chat_id=%s)",
            getattr(source, "chat_id", "?"),
        )
        return None

    try:
        entry = session_store.get_or_create_session(source)
    except Exception:
        logger.exception(
            "persist_user_message: get_or_create_session failed "
            "(chat_id=%s)",
            getattr(source, "chat_id", "?"),
        )
        return None

    session_id = entry.session_id
    try:
        session_db.append_message(
            session_id,
            role="user",
            content=content,
            platform_message_id=message_id,
            observed=observed,
        )
    except Exception:
        logger.exception(
            "persist_user_message: append_message failed (session_id=%s)",
            session_id,
        )
        return None

    return session_id


def _get_runner():
    """Return the live gateway runner, or ``None`` if unavailable.

    Wrapped so tests can monkey-patch this single seam.
    """
    try:
        from gateway.run import _gateway_runner_ref  # type: ignore
    except ImportError:
        return None
    try:
        return _gateway_runner_ref()
    except Exception:
        return None
