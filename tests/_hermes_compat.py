"""Minimal Hermes shapes for testing aap-hermes without a real Hermes install.

These mirror the parts of Hermes's API we depend on. Kept narrow on purpose —
if Hermes changes its API, this file changes too. The aap-hermes adapter
imports these in TESTS only; in production it imports from real Hermes.

Field names MUST match real Hermes (chat_id not chat, user_id not user, etc.)
so that tests catch field-name regressions instead of letting them slip into
production where they manifest as runtime kwarg errors in the gateway log.

Manual e2e against a real Hermes install is still the source of truth for
whether the production wiring works.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class MessageType(str, Enum):
    TEXT = "TEXT"
    COMMAND = "COMMAND"


@dataclass
class SessionSource:
    """Mirrors real Hermes's SessionSource field shape. Only the fields
    aap-hermes actually constructs are declared here; add more as needed."""
    platform: Any  # real Hermes accepts a Platform enum; our shim accepts anything
    chat_id: str
    chat_name: Optional[str] = None
    chat_type: str = "dm"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    thread_id: Optional[str] = None


@dataclass
class MessageEvent:
    text: str
    source: SessionSource
    message_type: MessageType = MessageType.TEXT
    raw_message: Any = None
    message_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now())
    internal: bool = False

    def is_command(self) -> bool:
        return self.text.startswith("/")


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PlatformConfig:
    name: str
    enabled: bool = True


@dataclass
class Platform:
    """Real Hermes's Platform is an Enum exposing ``.value``. The shim
    mirrors that interface so tests can assert against ``platform.value``."""
    name: str

    @property
    def value(self) -> str:
        return self.name


class BasePlatformAdapter(ABC):
    """Minimal stub of Hermes's BasePlatformAdapter."""

    def __init__(self, config: PlatformConfig, platform: Platform) -> None:
        self.config = config
        self.platform = platform
        self._message_handler: Optional[Callable[[MessageEvent], Awaitable[None]]] = None
        self._running = False

    def set_message_handler(self, handler: Callable[[MessageEvent], Awaitable[None]]) -> None:
        """Inject the gateway's message dispatcher. Real Hermes does this differently;
        the test mock exposes it explicitly so tests can assert calls."""
        self._message_handler = handler

    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult: ...

    @abstractmethod
    async def get_chat_info(self, chat_id: str) -> dict:
        """Real Hermes's BasePlatformAdapter declares this abstract.

        Mirroring it here ensures the test compat shim catches missing
        implementations the same way real Hermes does at instantiation.
        """
        ...
