"""Phase 5: per-session anti-relay predicate.

A peer sending into our agent can ask us to deliver a message to a third
party. We refuse that — peers must not use us as a megaphone. The original
defense was a contextvar (``originating_peer``) that captured the sender
of THIS inbound and the tool checked ``to == originating_peer``.

Under the new ingest/reasoning split, a single LLM turn may reason over
multiple inbounds from different peers. There is no single "originating
peer" anymore. Replace the contextvar check with a session-scoped predicate:

    aap_send_message(to=X) is allowed iff X has sent at least one envelope
    into the session that triggered this turn.

In a group session, this means "anyone in the group who has actually spoken
into this conversation." In a 1:1, it's strictly "the other party."

Cross-peer outreach (e.g. "Mallory mentioned Bob is stuck, let me reach
out") is legitimate but goes through a separate ``aap_initiate_conversation``
tool whose checks are explicit (relationship requirement, rate limit).
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Set

logger = logging.getLogger(__name__)


_AGENT_PREFIX_RE = re.compile(r"^\[Agent\s+([^\]]+)\]:")


def peers_who_have_messaged_session(session_id: str) -> Set[str]:
    """Return the set of agent addresses that have authored user-role rows
    in ``session_id``.

    Implementation: scans user-role messages in the session DB and parses
    the ``[Agent <addr>]: ...`` label that the AAP adapter prepends to the
    inbound text in ``_dispatch``. Falls back to an empty set on any error
    rather than raising — the caller treats "unknown" as "not allowed" via
    :func:`is_peer_allowed_in_session`.
    """
    runner = _get_runner()
    if runner is None:
        return set()
    session_db = getattr(runner, "_session_db", None)
    if session_db is None:
        return set()
    try:
        rows = session_db.get_messages(session_id)
    except Exception:
        logger.exception(
            "anti_relay.peers_who_have_messaged_session: get_messages failed "
            "(session_id=%s)",
            session_id,
        )
        return set()

    senders: Set[str] = set()
    for row in rows:
        if row.get("role") != "user":
            continue
        content = row.get("content")
        if not isinstance(content, str):
            continue
        m = _AGENT_PREFIX_RE.match(content)
        if m:
            senders.add(m.group(1).strip())
    return senders


def is_peer_allowed_in_session(session_id: str, peer: str) -> bool:
    """True iff ``peer`` has previously sent into ``session_id``."""
    return peer in peers_who_have_messaged_session(session_id)


def resolve_session_id_for_chat(session_chat_id: str) -> Optional[str]:
    """Resolve an AAP chat_id (the ``aap-group:<conv>`` or peer-address key)
    to the SQLite ``session_id``. Returns ``None`` if no session exists.

    Used by tools that read ``current_session_source`` and need the DB
    session_id to run the sender-predicate check.
    """
    runner = _get_runner()
    if runner is None:
        return None
    session_store = getattr(runner, "session_store", None)
    if session_store is None:
        return None
    entries = getattr(session_store, "_entries", None)
    if not entries:
        return None
    for entry in entries.values():
        origin = getattr(entry, "origin", None)
        if origin is None:
            continue
        if getattr(origin, "chat_id", None) == session_chat_id:
            return entry.session_id
    return None


def _get_runner():
    try:
        from gateway.run import _gateway_runner_ref  # type: ignore
    except ImportError:
        return None
    try:
        return _gateway_runner_ref()
    except Exception:
        return None
