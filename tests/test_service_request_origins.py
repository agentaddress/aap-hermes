"""Tests for ServiceRequestOriginIndex — atomic per-nonce origin store."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from aap_hermes.service_request_origins import (
    RequestOrigin,
    ServiceRequestOriginIndex,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_origin(
    target: str = "biz^example.com",
    group: str | None = None,
    created_at: str | None = None,
) -> RequestOrigin:
    return RequestOrigin(
        platform="aap",
        chat_id=group if group else "user^example.com",
        chat_type="group" if group else "dm",
        user_id="user^example.com",
        user_name="Ian",
        thread_id=None,
        chat_name="Group Dinner" if group else None,
        target_address=target,
        group_conversation_id=group,
        created_at=created_at or _iso_now(),
    )


def test_record_then_pop_if_with_matching_iss_returns_origin(tmp_path):
    store = ServiceRequestOriginIndex(tmp_path)
    origin = _make_origin()
    assert store.record("nonce-1", origin) is True

    popped = store.pop_if("nonce-1", "biz^example.com")
    assert popped == origin

    # Second pop returns None — record consumed.
    assert store.pop_if("nonce-1", "biz^example.com") is None


def test_pop_if_with_mismatched_iss_returns_none_and_keeps_record(tmp_path, caplog):
    store = ServiceRequestOriginIndex(tmp_path)
    origin = _make_origin(target="biz^example.com")
    store.record("nonce-1", origin)

    # Mismatched issuer — record must stay put.
    assert store.pop_if("nonce-1", "attacker^example.com") is None

    # Legitimate issuer can still claim the record afterwards.
    popped = store.pop_if("nonce-1", "biz^example.com")
    assert popped == origin


def test_pop_if_unknown_nonce_returns_none(tmp_path):
    store = ServiceRequestOriginIndex(tmp_path)
    assert store.pop_if("never-recorded", "biz^example.com") is None


def test_record_refuses_duplicate_nonce(tmp_path, caplog):
    store = ServiceRequestOriginIndex(tmp_path)
    origin_a = _make_origin(target="biz-a^example.com")
    origin_b = _make_origin(target="biz-b^example.com")
    assert store.record("nonce-1", origin_a) is True
    with caplog.at_level(logging.WARNING):
        assert store.record("nonce-1", origin_b) is False
    assert "refusing to overwrite" in caplog.text
    # Original record must still be routable to biz-a.
    popped = store.pop_if("nonce-1", "biz-a^example.com")
    assert popped == origin_a


def test_load_warns_on_corrupt_file(tmp_path, caplog):
    path = tmp_path / "aap-service-request-origins.json"
    path.write_text("{not valid json")
    store = ServiceRequestOriginIndex(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert store.pop_if("anything", "anyone") is None
    text = caplog.text.lower()
    assert "corrupt" in text or "unreadable" in text


def test_load_warns_on_wrong_shape(tmp_path, caplog):
    path = tmp_path / "aap-service-request-origins.json"
    path.write_text('["not", "a", "dict"]')
    store = ServiceRequestOriginIndex(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert store.pop_if("anything", "anyone") is None
    assert "wrong shape" in caplog.text.lower()


def test_ttl_prunes_expired_records(tmp_path):
    store = ServiceRequestOriginIndex(tmp_path, ttl=timedelta(days=7))
    stale_when = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace(
        "+00:00", "Z"
    )
    stale = _make_origin(created_at=stale_when)
    store.record("stale", stale)

    # pop_if triggers a load, which triggers a prune.
    assert store.pop_if("stale", stale.target_address) is None

    raw = json.loads((tmp_path / "aap-service-request-origins.json").read_text())
    assert "stale" not in raw


def test_atomic_write_does_not_leave_partial_file(tmp_path):
    store = ServiceRequestOriginIndex(tmp_path)
    store.record("nonce-1", _make_origin())
    assert list(tmp_path.glob("*.tmp")) == []


def test_field_round_trip_telegram_origin(tmp_path):
    store = ServiceRequestOriginIndex(tmp_path)
    origin = RequestOrigin(
        platform="telegram",
        chat_id="tg:12345",
        chat_type="dm",
        user_id="tg-user-9",
        user_name="Ian",
        thread_id="thread-99",
        chat_name=None,
        target_address="biz^example.com",
        group_conversation_id=None,
        created_at=_iso_now(),
    )
    store.record("nonce-1", origin)
    popped = store.pop_if("nonce-1", "biz^example.com")
    assert popped == origin


def test_pop_if_with_stale_record_shape_returns_none(tmp_path, caplog):
    # Simulate an on-disk record that pre-dates the current dataclass shape.
    path = tmp_path / "aap-service-request-origins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "nonce-1": {
                    "platform": "aap",
                    "target_address": "biz^example.com",
                    # missing every other field
                    "created_at": _iso_now(),
                }
            }
        )
    )
    store = ServiceRequestOriginIndex(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert store.pop_if("nonce-1", "biz^example.com") is None
    assert "stale on-disk format" in caplog.text.lower()
