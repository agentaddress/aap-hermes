"""Phase 2 reasoning scheduler: per-session debounced LLM turns.

The ingest path (``ingest.persist_user_message``) writes inbound AAP user
messages to the session DB synchronously, then signals this scheduler. The
scheduler runs one LLM turn per session on demand and debounces clustered
input so a burst of peer envelopes coalesces into a single turn that reads
the full batch from the DB.

The reasoning loop never receives message content directly — it passes the
gateway's message handler a "tick" event whose only role is to identify the
session. The actual content comes from ``conv_history`` loaded from the
session DB at turn start.

Design and rationale: ``docs/superpowers/plans/2026-06-10-ingest-vs-reason-redesign.md``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


MessageHandler = Callable[[Any], Awaitable[Optional[str]]]
TickFactory = Callable[["_ReasoningSession"], Any]


@dataclass
class _ReasoningSession:
    """Per-session state held by ``ReasoningScheduler``."""
    session_key: str
    source: Any
    has_input: asyncio.Event = field(default_factory=asyncio.Event)
    last_origin: Dict[str, Any] = field(default_factory=dict)
    task: Optional[asyncio.Task] = None


class ReasoningScheduler:
    """Owns one asyncio.Task per session for debounced LLM dispatch.

    The adapter signals new input via :meth:`signal`; the scheduler clears
    the flag, calls ``message_handler`` with a tick event, and loops. If
    ``signal`` is called again while a turn is in flight, the next loop
    iteration fires immediately — clustered ingest converges to at most one
    pending turn per session.
    """

    def __init__(
        self,
        message_handler: MessageHandler,
        tick_factory: TickFactory,
    ):
        self._message_handler = message_handler
        self._tick_factory = tick_factory
        self._sessions: Dict[str, _ReasoningSession] = {}
        self._closing = False

    async def signal(
        self,
        session_key: str,
        source: Any,
        *,
        origin: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark ``session_key`` as having new input. Idempotent within a turn."""
        if self._closing:
            return

        rsess = self._sessions.get(session_key)
        if rsess is None:
            rsess = _ReasoningSession(session_key=session_key, source=source)
            self._sessions[session_key] = rsess
            rsess.task = asyncio.create_task(
                self._run_session_loop(rsess),
                name=f"aap-reasoning:{session_key}",
            )

        if origin:
            rsess.last_origin = dict(origin)
        # Update source to the latest — for AAP this is stable per session,
        # but defensive in case a future change passes a per-envelope source.
        rsess.source = source
        rsess.has_input.set()

    async def close(self) -> None:
        """Cancel all session tasks. Safe to call multiple times."""
        self._closing = True
        tasks = [s.task for s in self._sessions.values() if s.task is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._sessions.clear()

    async def _run_session_loop(self, rsess: _ReasoningSession) -> None:
        """Wait → turn → loop. Survives handler exceptions."""
        while not self._closing:
            try:
                await rsess.has_input.wait()
            except asyncio.CancelledError:
                return
            if self._closing:
                return
            rsess.has_input.clear()

            tick_event = self._tick_factory(rsess)
            try:
                await self._message_handler(tick_event)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(
                    "Reasoning turn raised for session_key=%s",
                    rsess.session_key,
                )
            # Loop continues: if ``signal`` was called during the turn,
            # ``has_input`` is set again and we fire immediately.
