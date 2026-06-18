"""Per-nonce origin index for outbound service requests.

When ``tools.aap_send_service_request_handler`` sends a request, it
records the originating ``SessionSource`` here keyed by ``request_nonce``
so that the eventual inbound ``aap.service-response/v1`` can be routed
back into the originating user/group session instead of spawning a fresh
session keyed to the business agent's address.

See ``docs/design/2026-06-05-service-response-session-routing.md``.

This store deliberately lives in aap-hermes (not aap-python): the record
holds a gateway-level ``SessionSource``, not a protocol type, so it has
no business sitting in the SDK. The older
``aap.stores.service_request_groups.ServiceRequestGroupIndex`` is
superseded by this module; remove from the SDK in a follow-up release
once nothing imports it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import scenario_log

logger = logging.getLogger(__name__)


# Records older than this are pruned on every load. Bounds the store's
# growth when services never reply. 7 days is generous for a confirmation
# turnaround; anything older is pathological.
DEFAULT_TTL = timedelta(days=7)


@dataclass(frozen=True)
class RequestOrigin:
    """The originating session for an outbound service request, plus the
    AAP peer we sent the request to (for the impersonation check at
    response time).

    Fields mirror ``SessionSource`` but ``platform`` is normalized to a
    string at record time. ``Platform`` is a gateway type that may not be
    a plain ``str`` (see ``tests/_hermes_compat.py``), and a blind
    ``asdict`` would leak the object shape.
    """

    platform: str
    chat_id: str
    chat_type: str
    user_id: Optional[str]
    user_name: Optional[str]
    thread_id: Optional[str]
    chat_name: Optional[str]
    target_address: str
    group_conversation_id: Optional[str]
    created_at: str  # ISO8601 UTC, e.g. "2026-06-05T11:22:55Z"

    @classmethod
    def from_session_source(
        cls,
        source,
        *,
        target_address: str,
        group_conversation_id: Optional[str] = None,
    ) -> "RequestOrigin":
        """Build a ``RequestOrigin`` from a gateway ``SessionSource``.

        ``platform`` is normalized to a string — the gateway's
        ``Platform`` is a class/enum-like object whose dataclass
        serialization shape isn't stable across versions, so we pin to
        ``str(...)`` of its ``.value`` attribute when present, or its
        ``str()`` otherwise.
        """
        platform = getattr(source, "platform", None)
        platform_str = (
            getattr(platform, "value", None) or str(platform) if platform is not None else ""
        )
        return cls(
            platform=str(platform_str),
            chat_id=getattr(source, "chat_id", "") or "",
            chat_type=getattr(source, "chat_type", "dm") or "dm",
            user_id=getattr(source, "user_id", None),
            user_name=getattr(source, "user_name", None),
            thread_id=getattr(source, "thread_id", None),
            chat_name=getattr(source, "chat_name", None),
            target_address=target_address,
            group_conversation_id=group_conversation_id,
            created_at=_utcnow_iso(),
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> Optional[datetime]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class ServiceRequestOriginIndex:
    """Persists ``nonce -> RequestOrigin`` so that when a service response
    arrives asynchronously, ``_handle_service_response`` knows which
    session to dispatch the event into.

    Storage is a single JSON file under ``base_dir``. Writes are atomic
    via ``tmp + os.replace``. Single-writer assumed (no in-process
    locking).

    ``record(nonce, origin)`` REFUSES to overwrite an existing nonce — a
    duplicate is a bug we want to surface, not silently cross-route a
    future response.

    ``pop_if(nonce, expected_iss)`` atomically validates the expected
    issuer matches the recorded ``target_address`` before consuming the
    record. A mismatch leaves the record in place so a forged response
    can't snipe the legitimate one.
    """

    def __init__(self, base_dir: Path, ttl: timedelta = DEFAULT_TTL) -> None:
        self._path = Path(base_dir) / "aap-service-request-origins.json"
        self._ttl = ttl

    def _load(self) -> dict[str, dict]:
        try:
            with open(self._path) as f:
                raw = json.load(f)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "ServiceRequestOriginIndex: corrupt or unreadable file at "
                "%s (%s) — starting from empty. In-flight service "
                "responses for prior nonces will be dropped.",
                self._path, e,
            )
            return {}
        if not isinstance(raw, dict):
            logger.warning(
                "ServiceRequestOriginIndex: file at %s has wrong shape "
                "(expected dict, got %s) — starting from empty.",
                self._path, type(raw).__name__,
            )
            return {}
        return raw

    def _save(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._path)
        except OSError:
            logger.exception(
                "ServiceRequestOriginIndex: failed to write %s", self._path,
            )
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _prune(self, data: dict[str, dict]) -> dict[str, dict]:
        if not data:
            return data
        cutoff = datetime.now(timezone.utc) - self._ttl
        kept: dict[str, dict] = {}
        dropped: list[str] = []
        for nonce, entry in data.items():
            created = _parse_iso(entry.get("created_at", "") if isinstance(entry, dict) else "")
            if created is None or created < cutoff:
                dropped.append(nonce)
            else:
                kept[nonce] = entry
        if dropped:
            logger.info(
                "ServiceRequestOriginIndex: pruned %d expired origin "
                "record(s) (TTL=%s)",
                len(dropped), self._ttl,
            )
        return kept

    def record(self, nonce: str, origin: RequestOrigin) -> bool:
        """Insert a new origin record. Returns ``True`` on success,
        ``False`` if the nonce was already recorded (no overwrite,
        WARNING logged)."""
        data = self._prune(self._load())
        if nonce in data:
            existing = data[nonce]
            logger.warning(
                "ServiceRequestOriginIndex: refusing to overwrite existing "
                "origin record for nonce %s — caller bug? Existing "
                "target_address=%s, new=%s",
                nonce,
                existing.get("target_address") if isinstance(existing, dict) else "?",
                origin.target_address,
            )
            # Persist any pruning that happened above.
            self._save(data)
            return False
        data[nonce] = asdict(origin)
        self._save(data)
        scenario_log.log(
            "service_request_sent",
            parent_conv_id=origin.group_conversation_id,
            data={
                "nonce": nonce,
                "service_address": origin.target_address,
            },
        )
        scenario_log.log(
            "service_call_started",
            layer="named",
            parent_conv_id=origin.group_conversation_id,
            data={
                "nonce": nonce,
                "service_address": origin.target_address,
            },
        )
        return True

    def pop_if(
        self, nonce: str, expected_iss: str,
    ) -> Optional[RequestOrigin]:
        """Atomically check that a record exists for ``nonce`` AND its
        ``target_address`` matches ``expected_iss``. If both hold, delete
        the record and return it. Otherwise, leave any record in place
        and return ``None``.

        This closes the sniping vector: a forged response from a peer
        that isn't the original target doesn't consume the record, so
        the legitimate response can still land.
        """
        data = self._prune(self._load())
        entry = data.get(nonce)
        if entry is None:
            self._save(data)
            return None
        if not isinstance(entry, dict) or entry.get("target_address") != expected_iss:
            self._save(data)
            return None
        del data[nonce]
        self._save(data)
        try:
            origin = RequestOrigin(**entry)
        except TypeError as e:
            logger.warning(
                "ServiceRequestOriginIndex: dropping origin record for "
                "nonce %s — stored shape doesn't match RequestOrigin "
                "(%s). Likely a stale on-disk format.",
                nonce, e,
            )
            return None
        scenario_log.log(
            "service_response_received",
            parent_conv_id=origin.group_conversation_id,
            data={
                "nonce": nonce,
                "service_address": origin.target_address,
            },
        )
        scenario_log.log(
            "service_call_completed",
            layer="named",
            parent_conv_id=origin.group_conversation_id,
            data={
                "nonce": nonce,
                "service_address": origin.target_address,
            },
        )
        return origin
