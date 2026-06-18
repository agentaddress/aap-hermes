"""Per-agent scenario logging. No-op unless HERMES_SCENARIO_LOG_DIR is set.

Emits one JSON object per line to ``{HERMES_SCENARIO_LOG_DIR}/{profile}.jsonl``
at every seam where data crosses a boundary in the AAP plugin. The harness
(``test-scenarios/test-mode.sh``) exports the env var when starting test
gateways. Production gateways leave it unset, making every call here a pass.

The schema and design rationale live in
``docs/superpowers/specs/2026-06-09-scenario-logging-design.md``.
"""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


_log_dir: Optional[Path] = None
_run_id: Optional[str] = None
_profile: Optional[str] = None
_file_path: Optional[Path] = None


_TURN_ID: ContextVar[Optional[str]] = ContextVar("scenario_log_turn_id", default=None)
_CONV_ID: ContextVar[Optional[str]] = ContextVar("scenario_log_conv_id", default=None)
_PARENT_CONV_ID: ContextVar[Optional[str]] = ContextVar(
    "scenario_log_parent_conv_id", default=None,
)


def set_turn_id(turn_id: Optional[str]):
    return _TURN_ID.set(turn_id)


def reset_turn_id(token) -> None:
    _TURN_ID.reset(token)


def set_conv_id(conv_id: Optional[str]):
    return _CONV_ID.set(conv_id)


def reset_conv_id(token) -> None:
    _CONV_ID.reset(token)


def set_parent_conv_id(parent_conv_id: Optional[str]):
    return _PARENT_CONV_ID.set(parent_conv_id)


def reset_parent_conv_id(token) -> None:
    _PARENT_CONV_ID.reset(token)


def _initialize() -> None:
    """Resolve env-derived state once. Idempotent; cheap re-check on every call."""
    global _log_dir, _run_id, _profile, _file_path

    log_dir_env = os.environ.get("HERMES_SCENARIO_LOG_DIR")
    if not log_dir_env:
        _log_dir = None
        _file_path = None
        return

    new_dir = Path(log_dir_env)
    new_profile = os.environ.get("HERMES_PROFILE") or "unknown"
    new_run_id = os.environ.get("HERMES_SCENARIO_RUN_ID") or "ad-hoc"

    if _log_dir == new_dir and _profile == new_profile and _run_id == new_run_id:
        return

    _log_dir = new_dir
    _profile = new_profile
    _run_id = new_run_id
    _log_dir.mkdir(parents=True, exist_ok=True)
    _file_path = _log_dir / f"{_profile}.jsonl"


def _reset_for_tests() -> None:
    """Clear cached state. Used by the test suite to swap env vars."""
    global _log_dir, _run_id, _profile, _file_path
    _log_dir = None
    _run_id = None
    _profile = None
    _file_path = None


def log(
    kind: str,
    *,
    layer: str = "raw",
    audience: str = "internal",
    conv_id: Optional[str] = None,
    parent_conv_id: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
) -> None:
    """Emit one scenario-log event. No-op when HERMES_SCENARIO_LOG_DIR is unset."""
    _initialize()
    if _file_path is None:
        return

    entry = {
        "ts": _now_iso(),
        "ts_mono_ns": time.monotonic_ns(),
        "run_id": _run_id,
        "agent": _profile,
        "turn_id": _TURN_ID.get(),
        "layer": layer,
        "kind": kind,
        "audience": audience,
        "conv_id": conv_id if conv_id is not None else _CONV_ID.get(),
        "parent_conv_id": (
            parent_conv_id if parent_conv_id is not None else _PARENT_CONV_ID.get()
        ),
        "data": data or {},
    }

    with _file_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")))
        fh.write("\n")


def _now_iso() -> str:
    """UTC ISO-8601 with millisecond precision and trailing 'Z'."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
