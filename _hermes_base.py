"""Resolve which BasePlatformAdapter to subclass.

Production: real Hermes installed (`gateway.platforms.base`).
Tests: `tests._hermes_compat` substituted via sys.modules.
"""

from __future__ import annotations

try:
    from gateway.platforms.base import (  # type: ignore[import-not-found]
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        Platform,
        PlatformConfig,
        SendResult,
        SessionSource,
    )
except ImportError:
    from tests._hermes_compat import (  # type: ignore[import-not-found]
        BasePlatformAdapter,
        MessageEvent,
        MessageType,
        Platform,
        PlatformConfig,
        SendResult,
        SessionSource,
    )


__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "MessageType",
    "Platform",
    "PlatformConfig",
    "SendResult",
    "SessionSource",
]
