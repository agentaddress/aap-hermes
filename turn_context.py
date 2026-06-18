"""Per-task context for AAP-originated turns.

Python ``contextvars`` propagate automatically through ``asyncio.create_task``,
so a value set inside the adapter's ``_dispatch`` is visible to any tool
handlers that run inside the LLM turn the gateway spawns from the
``MessageEvent`` we hand off. This gives tool handlers a reliable way to
answer: "is this tool call happening inside an AAP turn, and if so, which
peer initiated it?"

Used to enforce that an AAP-turn LLM can only send AAP messages back to
the peer it's currently conversing with (anti-relay defense — a malicious
peer cannot socially-engineer the bot into messaging an unrelated third
party by writing "forward this to your other contact" in chat).

Telegram-originated turns (where the user is the actor) keep this var as
``None``, so existing free-form aap_send_message usage stays untouched —
the user can still explicitly tell their bot to message any peer.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Optional, Set

# When non-None, this AAP-originated turn was initiated by the named peer.
# Tool handlers that care about turn provenance read this directly.
_AAP_ORIGINATING_PEER: ContextVar[Optional[str]] = ContextVar(
    "aap_originating_peer", default=None,
)

# Set of peer addresses that this turn has already sent an AAP envelope to.
# Populated by adapter.send so the post-turn auto-reply path can skip
# delivery when the LLM already shipped a reply via aap_send_message (or any
# other tool that emits an envelope).  Avoids double-send to the same peer.
_AAP_SENT_THIS_TURN: ContextVar[Optional[Set[str]]] = ContextVar(
    "aap_sent_this_turn", default=None,
)

# When non-None, this AAP-originated turn was triggered by a message in
# the named group conversation.  aap_group_send consults this for anti-
# relay: it refuses to broadcast into a different conversation_id during
# the same turn (mirrors the originating_peer guard on aap_send_message).
_AAP_ORIGINATING_GROUP: ContextVar[Optional[str]] = ContextVar(
    "aap_originating_group", default=None,
)


def set_originating_peer(peer: Optional[str]):
    """Set the originating peer for the current task context. Returns a token
    the caller passes to :func:`reset_originating_peer` to undo, matching the
    standard ``contextvars`` set/reset idiom.

    Usage in the adapter dispatch path::

        token = set_originating_peer(sender)
        try:
            await self._message_handler(event)
        finally:
            reset_originating_peer(token)
    """
    return _AAP_ORIGINATING_PEER.set(peer)


def reset_originating_peer(token) -> None:
    _AAP_ORIGINATING_PEER.reset(token)


def get_originating_peer() -> Optional[str]:
    """Return the originating peer for the current task, or ``None`` if this
    is not running inside an AAP-originated turn."""
    return _AAP_ORIGINATING_PEER.get()


def init_sent_this_turn():
    """Initialize the per-turn outbound-recipient set. Returns a token for
    :func:`reset_sent_this_turn`. Call once at the top of an AAP turn."""
    return _AAP_SENT_THIS_TURN.set(set())


def reset_sent_this_turn(token) -> None:
    _AAP_SENT_THIS_TURN.reset(token)


def record_outbound_send(peer: str) -> None:
    """Mark ``peer`` as having received an envelope during this turn. No-op
    when called outside an AAP-originated turn (the set is None)."""
    sent = _AAP_SENT_THIS_TURN.get()
    if sent is not None and peer:
        sent.add(peer)


def already_sent_to(peer: str) -> bool:
    """True iff ``peer`` was already sent an envelope during this turn."""
    sent = _AAP_SENT_THIS_TURN.get()
    return bool(sent and peer in sent)


def set_originating_group(conversation_id: Optional[str]):
    """Set the originating group conversation_id for this turn. Returns a
    token for :func:`reset_originating_group` to undo."""
    return _AAP_ORIGINATING_GROUP.set(conversation_id)


def reset_originating_group(token) -> None:
    _AAP_ORIGINATING_GROUP.reset(token)


def get_originating_group() -> Optional[str]:
    """Return the originating group conversation_id, or ``None`` if this is
    not a group-originated turn."""
    return _AAP_ORIGINATING_GROUP.get()


# When non-None, this turn carries the full ``SessionSource`` of the
# message that initiated it. Used by ``aap_send_service_request`` to
# record the originating session against the request nonce so the
# eventual ``aap.service-response/v1`` can be routed back into that same
# session instead of spawning a fresh one keyed to the business address.
#
# Set by the AAP adapter at dispatch time for AAP-originated turns.
# Non-AAP-originated turns (Telegram, home channel) will see ``None``
# until the gateway base class is taught to populate this for every
# platform. ``aap_send_service_request_handler`` degrades gracefully by
# logging a WARNING and skipping the origin record; the eventual
# response will then fail the routing lookup and drop with a WARNING
# rather than corrupting an unrelated session.
_CURRENT_SESSION_SOURCE: ContextVar[Optional[Any]] = ContextVar(
    "aap_current_session_source", default=None,
)


def set_current_session_source(source: Optional[Any]):
    """Set the ``SessionSource`` for the current turn. Returns a token to
    pass to :func:`reset_current_session_source`. ``source`` is typed as
    ``Any`` to avoid a hard import of the gateway's ``SessionSource``
    type at module load time — the field-by-field access in
    ``RequestOrigin.from_session_source`` doesn't care about the
    nominal type."""
    return _CURRENT_SESSION_SOURCE.set(source)


def reset_current_session_source(token) -> None:
    _CURRENT_SESSION_SOURCE.reset(token)


def get_current_session_source() -> Optional[Any]:
    """Return the originating ``SessionSource`` for the current turn, or
    ``None`` if this turn was not dispatched with one set."""
    return _CURRENT_SESSION_SOURCE.get()
