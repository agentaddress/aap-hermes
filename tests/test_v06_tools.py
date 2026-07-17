"""Tests for the v0.6 LLM tool handlers."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aap.keys import generate_keypair

from aap.identity import IdentityFile


@pytest.mark.asyncio
async def test_send_message_imports_anti_relay_under_hermes_plugin_namespace(
    tmp_path, monkeypatch
):
    """Hermes loads the plugin as ``hermes_plugins.aap``. The send-message
    hot path must import sibling modules successfully under that package
    name, not just under tests' ``aap_hermes`` alias."""
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = []
    monkeypatch.setitem(sys.modules, "hermes_plugins", parent)

    root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.aap",
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "hermes_plugins.aap", module)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    tools_mod = importlib.import_module("hermes_plugins.aap.tools")

    seed, public = generate_keypair()
    ident = IdentityFile(
        private_seed=seed,
        public_key=public,
        address="chris^example.com",
    )
    client = MagicMock()
    client.send_envelope = AsyncMock(return_value=42)
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=None)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = await tools_mod.aap_send_message_handler(
        client,
        ident,
        catalog_cache,
        "stranger^example.com",
        "hi",
    )

    assert result["status"] == "error"
    assert "friend" in result["detail"].lower()


@pytest.fixture
def tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def identity():
    seed, public = generate_keypair()
    return IdentityFile(
        private_seed=seed,
        public_key=public,
        address="chris^example.com",
    )


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.send_envelope = AsyncMock(return_value=42)
    client.send_envelope_raw = AsyncMock(return_value=1)
    client.relay_url = "https://relay.example.com"
    return client


# -- aap_send_message: relationship gating ---------------------------------


@pytest.mark.asyncio
async def test_send_message_refused_without_relationship(
    tmp_hermes_home, identity, fake_client
):
    """No friend/admin/team AND peer isn't a business → refuse."""
    from aap_hermes.tools import aap_send_message_handler

    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=None)  # no catalog → not a business

    result = await aap_send_message_handler(
        fake_client, identity, catalog_cache,
        "stranger^example.com", "hi",
    )
    assert result["status"] == "error"
    assert "friend" in result["detail"].lower()
    fake_client.send_envelope.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_accepted_for_friend(
    tmp_hermes_home, identity, fake_client
):
    """Existing friend relationship → chat is sent."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    result = await aap_send_message_handler(
        fake_client, identity, MagicMock(),
        "mary^example.com", "hello",
    )
    assert result["status"] == "sent"
    fake_client.send_envelope.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_echoes_session_thread_id(
    tmp_hermes_home, identity, fake_client
):
    """Replying to the peer that opened the current AAP turn echoes that
    turn's thread_id so the reply threads back into the conversation."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    source = SessionSource(
        platform="aap",
        chat_id="mary^example.com",
        chat_type="dm",
        user_id="mary^example.com",
        user_name="mary^example.com",
        thread_id="T-echo-123",
    )
    token = set_current_session_source(source)
    try:
        result = await aap_send_message_handler(
            fake_client, identity, MagicMock(),
            "mary^example.com", "hello",
        )
    finally:
        reset_current_session_source(token)

    assert result["status"] == "sent"
    kwargs = fake_client.send_envelope.call_args.kwargs
    assert kwargs.get("thread_id") == "T-echo-123"


@pytest.mark.asyncio
async def test_send_message_omits_thread_id_for_different_peer(
    tmp_hermes_home, identity, fake_client
):
    """A peer's thread_id must not leak onto a message to a different peer."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    store = RelationshipStore.load(tmp_hermes_home)
    for addr in ("mary^example.com", "bob^example.com"):
        store._add_verified(RelationshipRecord(
            relationship_type="friend",
            peer_address=addr,
            established_at="2026-05-26T12:00:00Z",
            proposal_envelope_json="{}",
            accept_envelope_json="{}",
        ))

    # Session opened by mary, but we send to bob.
    source = SessionSource(
        platform="aap",
        chat_id="mary^example.com",
        chat_type="dm",
        user_id="mary^example.com",
        user_name="mary^example.com",
        thread_id="T-mary",
    )
    token = set_current_session_source(source)
    try:
        result = await aap_send_message_handler(
            fake_client, identity, MagicMock(),
            "bob^example.com", "hello",
        )
    finally:
        reset_current_session_source(token)

    assert result["status"] == "sent"
    kwargs = fake_client.send_envelope.call_args.kwargs
    assert kwargs.get("thread_id") is None


@pytest.mark.asyncio
async def test_send_message_falls_back_to_recorded_peer_thread(
    tmp_hermes_home, identity, fake_client
):
    """Cross-platform reply: the turn originates on Telegram (session chat_id
    is not the AAP peer), so reply_thread_id_for finds nothing — fall back to
    the peer's most recent recorded inbound thread so the reply still threads."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler
    from aap_hermes import peer_threads
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))
    peer_threads.record_inbound_thread("mary^example.com", "T-mary")

    source = SessionSource(
        platform="telegram",
        chat_id="7969749825",
        chat_type="dm",
        user_id="tg-9",
        user_name="Chris",
        thread_id=None,
    )
    token = set_current_session_source(source)
    try:
        result = await aap_send_message_handler(
            fake_client, identity, MagicMock(),
            "mary^example.com", "steak and chips",
        )
    finally:
        reset_current_session_source(token)

    assert result["status"] == "sent"
    kwargs = fake_client.send_envelope.call_args.kwargs
    assert kwargs.get("thread_id") == "T-mary"


@pytest.mark.asyncio
async def test_send_message_cross_platform_two_peers_thread_independently(
    tmp_hermes_home, identity, fake_client
):
    """The confirmed real-world case: one thread per address, two addresses.
    A Telegram-driven reply to each peer threads to that peer's own thread."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler
    from aap_hermes import peer_threads
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    store = RelationshipStore.load(tmp_hermes_home)
    for addr in ("a^example.com", "b^example.com"):
        store._add_verified(RelationshipRecord(
            relationship_type="friend",
            peer_address=addr,
            established_at="2026-05-26T12:00:00Z",
            proposal_envelope_json="{}",
            accept_envelope_json="{}",
        ))
    peer_threads.record_inbound_thread("a^example.com", "Ta")
    peer_threads.record_inbound_thread("b^example.com", "Tb")

    source = SessionSource(
        platform="telegram", chat_id="7969749825", chat_type="dm",
        user_id="tg-9", user_name="Chris", thread_id=None,
    )
    token = set_current_session_source(source)
    try:
        await aap_send_message_handler(
            fake_client, identity, MagicMock(), "a^example.com", "to A",
        )
        thread_a = fake_client.send_envelope.call_args.kwargs.get("thread_id")
        await aap_send_message_handler(
            fake_client, identity, MagicMock(), "b^example.com", "to B",
        )
        thread_b = fake_client.send_envelope.call_args.kwargs.get("thread_id")
    finally:
        reset_current_session_source(token)

    assert thread_a == "Ta"
    assert thread_b == "Tb"


@pytest.mark.asyncio
async def test_send_message_session_thread_wins_over_recorded(
    tmp_hermes_home, identity, fake_client
):
    """When the turn IS the in-AAP-session reply to the peer, the live session
    thread takes precedence over any stale recorded thread."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler
    from aap_hermes import peer_threads
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))
    peer_threads.record_inbound_thread("mary^example.com", "T-stale")

    source = SessionSource(
        platform="aap",
        chat_id="mary^example.com",
        chat_type="dm",
        user_id="mary^example.com",
        user_name="mary^example.com",
        thread_id="T-live",
    )
    token = set_current_session_source(source)
    try:
        result = await aap_send_message_handler(
            fake_client, identity, MagicMock(),
            "mary^example.com", "hello",
        )
    finally:
        reset_current_session_source(token)

    assert result["status"] == "sent"
    kwargs = fake_client.send_envelope.call_args.kwargs
    assert kwargs.get("thread_id") == "T-live"


@pytest.mark.asyncio
async def test_send_message_expands_hosted_address_shorthand(
    tmp_hermes_home, identity, fake_client
):
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^agentaddress.org",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    result = await aap_send_message_handler(
        fake_client, identity, MagicMock(),
        "mary^", "hello",
    )

    assert result["status"] == "sent"
    fake_client.send_envelope.assert_called_once_with(
        to="mary^agentaddress.org",
        text="hello",
        thread_id=None,
    )


@pytest.mark.asyncio
async def test_slash_send_expands_hosted_address_shorthand(
    tmp_hermes_home, identity, fake_client
):
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.commands import handle_aap_command

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="tom^agentaddress.org",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    out = await handle_aap_command(
        "/aap send tom^ hi Tom",
        fake_client,
        identity,
    )

    assert "Sent to tom^agentaddress.org" in out
    fake_client.send_envelope.assert_called_once_with(
        to="tom^agentaddress.org",
        text="hi Tom",
    )


@pytest.mark.asyncio
async def test_send_message_to_business_is_fire_and_forget(
    tmp_hermes_home, identity, fake_client
):
    """aap_send_message is always fire-and-forget — even to a business peer
    with a service catalog. The tool returns immediately with status=sent;
    any reply arrives later as an inbound on a future turn."""
    from aap_hermes.tools import aap_send_message_handler

    business_catalog = MagicMock()
    business_catalog.services = {"book-table": MagicMock()}
    business_catalog.canonical_agent_address = None

    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=business_catalog)

    result = await aap_send_message_handler(
        fake_client, identity, catalog_cache,
        "reception^frankies.example", "do you have a table Saturday 7pm?",
    )
    assert result["status"] == "sent"
    assert "future turn" in result["detail"]
    fake_client.send_envelope.assert_called_once()


# -- canonical-address mismatch refuse -------------------------------------


@pytest.mark.asyncio
async def test_send_message_refused_when_catalog_canonical_differs(
    tmp_hermes_home, identity, fake_client
):
    """If the caller guessed the wrong agent localpart, the catalog
    declares a different canonical agent — refuse and surface the
    canonical address so the LLM can retry."""
    from aap.services import ServiceCatalog, ServiceDefinition
    from datetime import datetime, timezone
    from aap_hermes.tools import aap_send_message_handler

    sd = ServiceDefinition(
        id="book-table", display_name="x", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="reception^frankies.example",
        canonical_agent_address="bookings^frankies.example",
        fetched_at=datetime.now(timezone.utc),
        services={"book-table": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_send_message_handler(
        fake_client, identity, catalog_cache,
        "reception^frankies.example", "hi",
    )
    assert result["status"] == "error"
    assert "mismatch" in result["detail"].lower()
    assert result["canonical_agent_address"] == "bookings^frankies.example"
    fake_client.send_envelope.assert_not_called()


@pytest.mark.asyncio
async def test_send_service_request_refused_when_catalog_canonical_differs(
    tmp_hermes_home, identity, fake_client
):
    from aap.services import ServiceCatalog, ServiceDefinition
    from datetime import datetime, timezone
    from aap_hermes.tools import aap_send_service_request_handler

    sd = ServiceDefinition(
        id="book-table", display_name="x", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="reception^frankies.example",
        canonical_agent_address="bookings^frankies.example",
        fetched_at=datetime.now(timezone.utc),
        services={"book-table": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_send_service_request_handler(
        fake_client, identity, catalog_cache,
        "reception^frankies.example", "book-table", {},
    )
    assert result["status"] == "error"
    assert result["canonical_agent_address"] == "bookings^frankies.example"
    fake_client.send_envelope_raw.assert_not_called()


@pytest.mark.asyncio
async def test_list_services_surfaces_canonical_agent_address(
    tmp_hermes_home, identity, fake_client
):
    from aap.services import ServiceCatalog, ServiceDefinition
    from datetime import datetime, timezone
    from aap_hermes.tools import aap_list_services_handler

    sd = ServiceDefinition(
        id="book-table", display_name="x", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="reception^frankies.example",
        canonical_agent_address="bookings^frankies.example",
        fetched_at=datetime.now(timezone.utc),
        services={"book-table": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_list_services_handler(
        catalog_cache, "reception^frankies.example",
    )
    assert result["status"] == "ok"
    assert result["canonical_agent_address"] == "bookings^frankies.example"
    assert "warning" in result


# -- aap_send_service_request: validation + attestation auto-attach --------


@pytest.mark.asyncio
async def test_send_service_request_validates_payload(
    tmp_hermes_home, identity, fake_client
):
    """A payload missing a required field returns a structured error with
    schema_hint — the LLM should be able to correct and retry."""
    from aap.services import ServiceCatalog, ServiceDefinition
    from datetime import datetime, timezone
    from aap_hermes.tools import aap_send_service_request_handler

    sd = ServiceDefinition(
        id="book-table",
        display_name="Reserve a table",
        description="",
        input_schema={
            "type": "object",
            "required": ["name", "party_size"],
            "properties": {
                "name": {"type": "string"},
                "party_size": {"type": "integer"},
            },
        },
        output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"book-table": sd},
    )

    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_send_service_request_handler(
        fake_client, identity, catalog_cache,
        "biz^example.com", "book-table",
        {"name": "John"},  # missing party_size
    )
    assert result["status"] == "error"
    assert result["sent"] is False
    assert "ENVELOPE NOT SENT" in result["detail"]
    assert "schema validation" in result["detail"]
    assert "schema_hint" in result
    fake_client.send_envelope_raw.assert_not_called()


@pytest.mark.asyncio
async def test_send_service_request_with_missing_attestation(
    tmp_hermes_home, identity, fake_client
):
    """Catalog declares phone attestation required; user has none → error
    pointing the LLM at /aap verify."""
    from aap.services import ServiceCatalog, ServiceDefinition
    from datetime import datetime, timezone
    from aap_hermes.tools import aap_send_service_request_handler

    sd = ServiceDefinition(
        id="book-table",
        display_name="Reserve",
        description="",
        input_schema={"type": "object"},  # trivial schema
        output_schema={},
        verification_required={
            "phone": {
                "verified_by_oneof": ["verify.agentaddress.org"],
                "max_age_days": 365,
            },
        },
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"book-table": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_send_service_request_handler(
        fake_client, identity, catalog_cache,
        "biz^example.com", "book-table", {},
    )
    assert result["status"] == "error"
    assert result["sent"] is False
    # Detail is for the LLM and includes the "ENVELOPE NOT SENT" banner
    # plus the user_message reproduced for paranoid LLMs that only
    # read 'detail'.
    assert "ENVELOPE NOT SENT" in result["detail"]
    # The user_message field is the literal text the LLM must send to
    # the user via send_message + 👤 USER REQUIRED prefix.
    assert "user_message" in result
    assert "verified phone number" in result["user_message"]
    assert "Reply with your phone number" in result["user_message"]
    assert result["missing_attestation"]["type"] == "phone"


@pytest.mark.asyncio
async def test_send_service_request_is_fire_and_forget(
    tmp_hermes_home, identity, fake_client
):
    """aap_send_service_request is fully async — returns immediately with
    status=sent. The business response arrives later as an inbound on a
    future turn (dispatched by _handle_service_response)."""
    from datetime import datetime, timezone
    from aap.services import ServiceCatalog, ServiceDefinition
    from aap_hermes.tools import aap_send_service_request_handler

    sd = ServiceDefinition(
        id="ask",
        display_name="Ask",
        description="",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"ask": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_send_service_request_handler(
        fake_client, identity, catalog_cache,
        "biz^example.com", "ask", {"text": "hours?"},
    )
    assert result["status"] == "sent"
    assert "future turn" in result["detail"]
    assert "request_nonce" in result
    fake_client.send_envelope_raw.assert_called_once()


# -- aap_propose_friendship: pending outbound is recorded -------------------


@pytest.mark.asyncio
async def test_propose_friendship_records_pending_outbound(
    tmp_hermes_home, identity, fake_client
):
    from aap.stores.pending_proposals import PendingProposalStore
    from aap_hermes.tools import aap_propose_friendship_handler

    result = await aap_propose_friendship_handler(
        fake_client, identity, "mary^example.com",
    )
    assert result["status"] == "pending_approval"
    assert "nonce" in result

    pending = PendingProposalStore.load(tmp_hermes_home)
    row = pending.take_outbound(result["nonce"])
    assert row is not None
    assert row.peer_address == "mary^example.com"
    assert row.relationship_type == "friend"


@pytest.mark.asyncio
async def test_propose_relationship_expands_hosted_address_shorthand(
    tmp_hermes_home, identity, fake_client
):
    from aap.stores.pending_proposals import PendingProposalStore
    from aap_hermes.tools import aap_propose_relationship_handler

    result = await aap_propose_relationship_handler(
        fake_client, identity, "mary^",
        relationship_type="friend",
    )

    assert result["status"] == "pending_approval"
    pending = PendingProposalStore.load(tmp_hermes_home).take_outbound(
        result["nonce"],
    )
    assert pending is not None
    assert pending.peer_address == "mary^agentaddress.org"


# -- aap_propose_relationship: admin and team ------------------------------


@pytest.mark.asyncio
async def test_propose_relationship_admin(tmp_hermes_home, identity, fake_client):
    """Admin proposal records pending_outbound with relationship_type=admin."""
    from aap.stores.pending_proposals import PendingProposalStore
    from aap_hermes.tools import aap_propose_relationship_handler

    result = await aap_propose_relationship_handler(
        fake_client, identity, "mary^example.com",
        relationship_type="admin",
    )
    assert result["status"] == "pending_approval"
    assert result["relationship_type"] == "admin"

    pending = PendingProposalStore.load(tmp_hermes_home).take_outbound(result["nonce"])
    assert pending is not None
    assert pending.relationship_type == "admin"


@pytest.mark.asyncio
async def test_propose_relationship_team_requires_resource(
    tmp_hermes_home, identity, fake_client
):
    """Team without resource → error before any send."""
    from aap_hermes.tools import aap_propose_relationship_handler

    result = await aap_propose_relationship_handler(
        fake_client, identity, "dev^example.com",
        relationship_type="team",
    )
    assert result["status"] == "error"
    assert "resource" in result["detail"].lower()
    fake_client.send_envelope_raw.assert_not_called()


@pytest.mark.asyncio
async def test_propose_relationship_team_with_resource(
    tmp_hermes_home, identity, fake_client
):
    from aap.stores.pending_proposals import PendingProposalStore
    from aap_hermes.tools import aap_propose_relationship_handler

    result = await aap_propose_relationship_handler(
        fake_client, identity, "dev^example.com",
        relationship_type="team", resource="github.com/acme/widgets",
    )
    assert result["status"] == "pending_approval"
    assert result["relationship_type"] == "team"
    assert result["resource"] == "github.com/acme/widgets"

    pending = PendingProposalStore.load(tmp_hermes_home).take_outbound(result["nonce"])
    assert pending is not None
    assert pending.relationship_type == "team"
    assert pending.resource == "github.com/acme/widgets"


@pytest.mark.asyncio
async def test_propose_relationship_rejects_unknown_type(
    tmp_hermes_home, identity, fake_client
):
    from aap_hermes.tools import aap_propose_relationship_handler

    result = await aap_propose_relationship_handler(
        fake_client, identity, "mary^example.com",
        relationship_type="frenemies",
    )
    assert result["status"] == "error"
    assert "friend / admin / team" in result["detail"]
    fake_client.send_envelope_raw.assert_not_called()


# -- aap_revoke_relationship ------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_relationship_specific_type(
    tmp_hermes_home, identity, fake_client
):
    """Revoking a specific type with the peer removes only that record
    and sends a matching revoke envelope."""
    from aap.relationships import (
        RelationshipRecord,
        RelationshipStore,
    )
    from aap_hermes.tools import aap_revoke_relationship_handler

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="admin",
        peer_address="hermes2^example.com",
        established_at="2026-05-27T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))
    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="hermes2^example.com",
        established_at="2026-05-27T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    result = await aap_revoke_relationship_handler(
        fake_client, identity, "hermes2^example.com",
        relationship_type="admin",
    )
    assert result["status"] == "revoked"
    assert result["revoked"] == ["admin"]

    # Friend should remain after admin revoke.
    store = RelationshipStore.load(tmp_hermes_home)
    assert store.has_friend("hermes2^example.com")
    assert not store.has_admin("hermes2^example.com")
    fake_client.send_envelope_raw.assert_called_once()


@pytest.mark.asyncio
async def test_revoke_relationship_all_types(
    tmp_hermes_home, identity, fake_client
):
    """Omitting relationship_type revokes every relationship with the peer."""
    from aap.relationships import (
        RelationshipRecord,
        RelationshipStore,
    )
    from aap_hermes.tools import aap_revoke_relationship_handler

    for rt in ("friend", "admin"):
        RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
            relationship_type=rt,
            peer_address="hermes2^example.com",
            established_at="2026-05-27T12:00:00Z",
            proposal_envelope_json="{}",
            accept_envelope_json="{}",
        ))

    result = await aap_revoke_relationship_handler(
        fake_client, identity, "hermes2^example.com",
    )
    assert result["status"] == "revoked"
    assert set(result["revoked"]) == {"friend", "admin"}

    store = RelationshipStore.load(tmp_hermes_home)
    assert not store.any_relationship_with("hermes2^example.com")
    assert fake_client.send_envelope_raw.call_count == 2


@pytest.mark.asyncio
async def test_revoke_relationship_no_record_errors(
    tmp_hermes_home, identity, fake_client
):
    from aap_hermes.tools import aap_revoke_relationship_handler

    result = await aap_revoke_relationship_handler(
        fake_client, identity, "nobody^example.com",
    )
    assert result["status"] == "error"
    assert "No relationships" in result["detail"]
    fake_client.send_envelope_raw.assert_not_called()


# -- aap_list_relationships -------------------------------------------------


def test_list_relationships_empty(tmp_hermes_home):
    from aap_hermes.tools import aap_list_relationships_handler
    result = aap_list_relationships_handler()
    assert result == {"status": "ok", "relationships": []}


def test_list_relationships_returns_records(tmp_hermes_home):
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_list_relationships_handler

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))
    result = aap_list_relationships_handler()
    assert result["status"] == "ok"
    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["peer_address"] == "mary^example.com"


@pytest.mark.asyncio
async def test_send_message_friend_fire_and_forget_by_default(
    tmp_hermes_home, identity, fake_client
):
    """Friend peers stay async by default — aap_send_message returns
    'sent' immediately without ever blocking."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="mary^example.com",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    result = await aap_send_message_handler(
        fake_client, identity, MagicMock(),
        "mary^example.com", "hi",
    )

    assert result["status"] == "sent"
    assert "reply_text" not in result


# -- outbound mirror to AAP session ----------------------------------------


@pytest.mark.asyncio
async def test_send_message_mirrors_outbound_to_aap_session(
    tmp_hermes_home, identity, fake_client, monkeypatch
):
    """Every outbound aap_send_message must mirror into the AAP-with-peer
    session so any later reply lands with conversational context (the
    'panicked unauthorized-message' bug)."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_send_message_handler

    peer = "hermes2^example.com"
    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="team",
        peer_address=peer,
        resource="agentaddress/aap-hermes",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    mirror_calls = []

    def fake_mirror(peer, text):
        mirror_calls.append((peer, text))

    monkeypatch.setattr(
        "aap_hermes.tools.mirror_outbound_to_aap_session", fake_mirror,
    )
    monkeypatch.setattr(
        "aap_hermes.tools.mirror_to_home_channels", MagicMock(),
    )

    await aap_send_message_handler(
        fake_client, identity, MagicMock(),
        peer, "What do you think about migrating to Python?",
    )

    assert mirror_calls == [
        (peer, "What do you think about migrating to Python?"),
    ]


@pytest.mark.asyncio
async def test_mirror_outbound_session_is_no_op_without_gateway(monkeypatch):
    """Outside the gateway (CLI, tests) the mirror must be a silent no-op,
    not blow up — gateway modules aren't importable in CLI mode."""
    from aap_hermes.mirror import mirror_outbound_to_aap_session

    # No exception, no return value to check — just must not raise.
    mirror_outbound_to_aap_session(
        peer="hermes2^example.com",
        text="hello",
    )


# -- trust-preamble flip: silence is the default ---------------------------


def test_relationship_trust_note_teaches_silence_default():
    from aap_hermes.adapter import _relationship_trust_note

    note = _relationship_trust_note(
        "hermes2^example.com",
        relationship_type="team",
        resource="agentaddress/aap-hermes",
    )
    lowered = note.lower()
    # Silence is the explicit default
    assert "silence is the default" in lowered
    assert "[no_reply]" in lowered
    # Parenthetical anti-pattern is called out
    assert "parenthetical" in lowered


def test_reply_window_trust_note_teaches_silence_default():
    from aap_hermes.adapter import _reply_window_trust_note

    note = _reply_window_trust_note("reception^frankies.example")
    lowered = note.lower()
    assert "silence is the default" in lowered
    assert "[no_reply]" in lowered


# -- /aap clear_conversation -----------------------------------------------


@pytest.mark.asyncio
async def test_clear_conversation_rejects_missing_args():
    from aap_hermes.commands import handle_aap_command

    out = await handle_aap_command(
        "/aap clear_conversation", MagicMock(), MagicMock(),
    )
    assert "usage" in out.lower()


@pytest.mark.asyncio
async def test_clear_conversation_treats_non_address_as_conv_id(
    tmp_hermes_home, monkeypatch
):
    """A bare non-Address argument is treated as a group conversation_id
    and the offline disk path drops the corresponding ``aap-group:<id>``
    session (no entry in our fixture, so 'nothing to clear')."""
    import json
    import sys
    import types
    from pathlib import Path

    from aap_hermes.commands import handle_aap_command

    sessions_dir = Path(tmp_hermes_home) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "sessions.json").write_text(json.dumps({}))

    # No live runner — force offline path.
    fake_run_mod = types.ModuleType("gateway.run")
    fake_run_mod._gateway_runner_ref = lambda: None
    fake_session_mod = types.ModuleType("gateway.session")
    captured = {}

    class _FakeSessionSource:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    fake_session_mod.SessionSource = _FakeSessionSource

    def fake_build_session_key(source):
        captured["chat_id"] = source.chat_id
        return f"agent:main:aap:dm:{source.chat_id}"
    fake_session_mod.build_session_key = fake_build_session_key

    fake_base_mod = types.ModuleType("gateway.platforms.base")
    fake_base_mod.SessionSource = _FakeSessionSource

    monkeypatch.setitem(sys.modules, "gateway.run", fake_run_mod)
    monkeypatch.setitem(sys.modules, "gateway.session", fake_session_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", fake_base_mod)

    out = await handle_aap_command(
        "/aap clear_conversation conv-uuid-abc123",
        MagicMock(), MagicMock(),
    )

    # The session key resolution should have wrapped the conv_id with the
    # aap-group: prefix.
    assert captured["chat_id"] == "aap-group:conv-uuid-abc123"
    assert "nothing to clear" in out.lower() or "no existing" in out.lower()


@pytest.mark.asyncio
async def test_clear_conversation_resets_session(monkeypatch):
    """Happy path: command resolves the AAP-with-peer session key,
    calls SessionStore.reset_session, and reports the new session id."""
    from aap_hermes.commands import handle_aap_command

    fake_entry = MagicMock(session_id="20260528_140000_deadbeef")
    fake_store = MagicMock()
    fake_store.reset_session = MagicMock(return_value=fake_entry)

    fake_runner = MagicMock(session_store=fake_store)

    # Patch the lazy imports that _clear_conversation_cmd does.
    import sys
    import types

    fake_run_mod = types.ModuleType("gateway.run")
    fake_run_mod._gateway_runner_ref = lambda: fake_runner

    fake_base_mod = types.ModuleType("gateway.platforms.base")

    class _FakeSessionSource:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_base_mod.SessionSource = _FakeSessionSource

    fake_session_mod = types.ModuleType("gateway.session")
    captured = {}

    def fake_build_session_key(source):
        captured["chat_id"] = source.chat_id
        return f"agent:main:aap:dm:{source.chat_id}"

    fake_session_mod.build_session_key = fake_build_session_key

    monkeypatch.setitem(sys.modules, "gateway.run", fake_run_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", fake_base_mod)
    monkeypatch.setitem(sys.modules, "gateway.session", fake_session_mod)

    out = await handle_aap_command(
        "/aap clear_conversation hermes2^agentaddress.org",
        MagicMock(), MagicMock(),
    )

    assert captured["chat_id"] == "hermes2^agentaddress.org"
    fake_store.reset_session.assert_called_once_with(
        "agent:main:aap:dm:hermes2^agentaddress.org"
    )
    assert "Cleared" in out
    assert "hermes2^agentaddress.org" in out
    assert "20260528_140000_deadbeef" in out


@pytest.mark.asyncio
async def test_clear_conversation_expands_hosted_address_shorthand(monkeypatch):
    from aap_hermes.commands import handle_aap_command

    fake_entry = MagicMock(session_id="20260528_140000_deadbeef")
    fake_store = MagicMock()
    fake_store.reset_session = MagicMock(return_value=fake_entry)
    fake_runner = MagicMock(session_store=fake_store)

    import sys
    import types

    fake_run_mod = types.ModuleType("gateway.run")
    fake_run_mod._gateway_runner_ref = lambda: fake_runner

    fake_base_mod = types.ModuleType("gateway.platforms.base")

    class _FakeSessionSource:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_base_mod.SessionSource = _FakeSessionSource

    fake_session_mod = types.ModuleType("gateway.session")
    captured = {}

    def fake_build_session_key(source):
        captured["chat_id"] = source.chat_id
        return f"agent:main:aap:dm:{source.chat_id}"

    fake_session_mod.build_session_key = fake_build_session_key

    monkeypatch.setitem(sys.modules, "gateway.run", fake_run_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", fake_base_mod)
    monkeypatch.setitem(sys.modules, "gateway.session", fake_session_mod)

    out = await handle_aap_command(
        "/aap clear_conversation hermes2^",
        MagicMock(), MagicMock(),
    )

    assert captured["chat_id"] == "hermes2^agentaddress.org"
    fake_store.reset_session.assert_called_once_with(
        "agent:main:aap:dm:hermes2^agentaddress.org"
    )
    assert "hermes2^agentaddress.org" in out


@pytest.mark.asyncio
async def test_clear_conversation_reports_no_session_to_clear(monkeypatch):
    """If reset_session returns None (no entry existed), surface that."""
    from aap_hermes.commands import handle_aap_command

    fake_store = MagicMock()
    fake_store.reset_session = MagicMock(return_value=None)
    fake_runner = MagicMock(session_store=fake_store)

    import sys
    import types

    fake_run_mod = types.ModuleType("gateway.run")
    fake_run_mod._gateway_runner_ref = lambda: fake_runner

    fake_base_mod = types.ModuleType("gateway.platforms.base")

    class _FakeSessionSource:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    fake_base_mod.SessionSource = _FakeSessionSource

    fake_session_mod = types.ModuleType("gateway.session")
    fake_session_mod.build_session_key = lambda source: "agent:main:aap:dm:x"

    monkeypatch.setitem(sys.modules, "gateway.run", fake_run_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", fake_base_mod)
    monkeypatch.setitem(sys.modules, "gateway.session", fake_session_mod)

    out = await handle_aap_command(
        "/aap clear_conversation nobody^example.com",
        MagicMock(), MagicMock(),
    )
    assert "no existing" in out.lower()


@pytest.mark.asyncio
async def test_clear_conversation_help_lists_command():
    from aap_hermes.commands import handle_aap_command

    out = await handle_aap_command("/aap", MagicMock(), MagicMock())
    assert "clear_conversation" in out


@pytest.mark.asyncio
async def test_clear_conversation_offline_rewrites_sessions_index(
    tmp_hermes_home, monkeypatch
):
    """No gateway runner active → fall through to the disk path:
    drop the entry from sessions.json and remove SQLite rows."""
    import json
    import sys
    import types
    from pathlib import Path

    from aap_hermes.commands import handle_aap_command

    sessions_dir = Path(tmp_hermes_home) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_key = "agent:main:aap:dm:hermes2^agentaddress.org"
    session_id = "20260528_120000_aaaa1111"
    sessions_file = sessions_dir / "sessions.json"
    sessions_file.write_text(json.dumps({
        session_key: {"session_id": session_id, "platform": "aap"},
        "agent:main:telegram:dm:42": {
            "session_id": "20260528_110000_bbbb2222", "platform": "telegram",
        },
    }))

    # Force the gateway-not-active branch.
    fake_run_mod = types.ModuleType("gateway.run")
    fake_run_mod._gateway_runner_ref = lambda: None
    fake_session_mod = types.ModuleType("gateway.session")
    fake_session_mod.build_session_key = lambda source: session_key

    class _FakeSessionSource:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    fake_session_mod.SessionSource = _FakeSessionSource

    fake_base_mod = types.ModuleType("gateway.platforms.base")
    fake_base_mod.SessionSource = _FakeSessionSource

    delete_calls = []

    class _FakeSessionDB:
        def __init__(self):
            pass
        def delete_session(self, sid, sessions_dir=None):
            delete_calls.append(sid)
            return True
    fake_state_mod = types.ModuleType("hermes_state")
    fake_state_mod.SessionDB = _FakeSessionDB

    monkeypatch.setitem(sys.modules, "gateway.run", fake_run_mod)
    monkeypatch.setitem(sys.modules, "gateway.session", fake_session_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", fake_base_mod)
    monkeypatch.setitem(sys.modules, "hermes_state", fake_state_mod)

    out = await handle_aap_command(
        "/aap clear_conversation hermes2^agentaddress.org",
        MagicMock(), MagicMock(),
    )

    # Index file: AAP entry gone, Telegram entry preserved.
    with open(sessions_file) as f:
        remaining = json.load(f)
    assert session_key not in remaining
    assert "agent:main:telegram:dm:42" in remaining

    # SQLite cleanup invoked with the right session_id.
    assert delete_calls == [session_id]

    # User-facing message: cleared + the gateway-restart hint.
    assert "Cleared" in out
    assert session_id in out
    assert "Restart the gateway" in out


# -- aap_group_send + group trust preamble ---------------------------------


@pytest.mark.asyncio
async def test_group_send_refuses_unknown_conversation(
    tmp_hermes_home, identity, fake_client
):
    """Bogus conversation_id → handler returns an error, no envelope is sent."""
    from aap_hermes.tools import aap_group_send_handler

    result = await aap_group_send_handler(
        fake_client, identity, "nonexistent-conv-id", "hello group",
    )
    assert result["status"] == "error"
    assert "unknown" in result["detail"].lower()
    fake_client.send_envelope.assert_not_called()


@pytest.mark.asyncio
async def test_group_send_broadcasts_to_other_members(
    tmp_hermes_home, identity, fake_client, monkeypatch
):
    """Happy path: aap_group_send fans out the chat envelope to every
    non-self member, mirrors the outbound, and reports per-recipient
    delivery."""
    from aap.conversations import Conversation, ConversationStore
    from aap_hermes.tools import aap_group_send_handler

    conv_id = "conv-abc-123"
    members = [
        identity.address,
        "hermes2^example.com",
        "hermes3^example.com",
    ]
    ConversationStore.load(tmp_hermes_home).record(Conversation(
        conversation_id=conv_id,
        purpose="repo migration",
        members=members,
        convener=identity.address,
        accepted_at="2026-05-28T12:00:00Z",
        last_message_at=None,
    ))

    fake_client.send_envelope = AsyncMock(side_effect=[101, 102])

    mirrors = []

    def fake_mirror(conversation_id, text):
        mirrors.append((conversation_id, text))

    monkeypatch.setattr(
        "aap_hermes.tools.mirror_outbound_to_aap_group_session", fake_mirror,
    )

    result = await aap_group_send_handler(
        fake_client, identity, conv_id, "What do we think about Python?",
    )

    assert result["status"] == "broadcast"
    assert result["delivered"] == 2
    assert result["total"] == 2
    assert result["failed"] == []
    # Two outbound envelopes — one per non-self member — each tagged
    # with the conversation_id.
    assert fake_client.send_envelope.call_count == 2
    sent_tos = sorted(
        c.kwargs.get("to") or c.args[0] for c in fake_client.send_envelope.call_args_list
    )
    assert sent_tos == [
        "hermes2^example.com",
        "hermes3^example.com",
    ]
    for call in fake_client.send_envelope.call_args_list:
        assert call.kwargs["conversation_id"] == conv_id
    # Mirror fired exactly once for the broadcast.
    assert mirrors == [(conv_id, "What do we think about Python?")]


@pytest.mark.asyncio
async def test_group_send_refuses_cross_group_relay(
    tmp_hermes_home, identity, fake_client
):
    """Anti-relay: if this turn originated from group A, aap_group_send
    must refuse to broadcast into group B (just like aap_send_message
    refuses to DM peers other than the originator)."""
    from aap.conversations import Conversation, ConversationStore
    from aap_hermes.tools import aap_group_send_handler
    from aap_hermes.turn_context import (
        set_originating_group, reset_originating_group,
    )

    conv_a = "conv-AAAA"
    conv_b = "conv-BBBB"
    for cid in (conv_a, conv_b):
        ConversationStore.load(tmp_hermes_home).record(Conversation(
            conversation_id=cid,
            purpose="x",
            members=[identity.address, "other^example.com"],
            convener=identity.address,
            accepted_at="2026-05-28T12:00:00Z",
            last_message_at=None,
        ))

    token = set_originating_group(conv_a)
    try:
        result = await aap_group_send_handler(
            fake_client, identity, conv_b, "sneaky relay attempt",
        )
    finally:
        reset_originating_group(token)

    assert result["status"] == "error"
    assert "Refused" in result["detail"]
    assert conv_a in result["detail"]
    assert conv_b in result["detail"]
    fake_client.send_envelope.assert_not_called()


def test_group_trust_note_teaches_silence_default_and_tool_choice():
    """Group preamble must (a) tell the LLM silence is the default,
    (b) explicitly direct to aap_group_send / aap_send_message, and
    (c) state that final text is NOT auto-delivered."""
    from aap_hermes.adapter import _group_trust_note

    note = _group_trust_note(
        sender="hermes2^example.com",
        conversation_id="conv-xyz",
        members=(
            "hermes1^example.com",
            "hermes2^example.com",
            "hermes3^example.com",
        ),
        purpose="repo migration discussion",
    )
    lowered = note.lower()
    assert "silence is the default" in lowered
    assert "aap_group_send" in note  # case-sensitive: tool name
    assert "aap_send_message" in note
    assert "not automatically shipped" in lowered or "not auto" in lowered
    # Member list and purpose surfaced for context
    assert "repo migration discussion" in note
    assert "hermes3^example.com" in note


# -- aap_group_start + aap_group_list --------------------------------------


@pytest.mark.asyncio
async def test_group_start_creates_conversation_and_sends_invitations(
    tmp_hermes_home, identity, fake_client
):
    """Happy path: creates a Conversation locally, sends one invitation
    envelope per non-self member."""
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_group_start_handler
    from aap.conversations import ConversationStore

    # Both invitees need a relationship for the receivers to even accept.
    # The PRE-FLIGHT warning is informational; the start still proceeds.
    for peer in ("hermes2^example.com", "hermes3^example.com"):
        RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
            relationship_type="friend",
            peer_address=peer,
            established_at="2026-05-26T12:00:00Z",
            proposal_envelope_json="{}",
            accept_envelope_json="{}",
        ))

    fake_client.send_envelope_raw = AsyncMock(return_value=42)

    result = await aap_group_start_handler(
        fake_client, identity,
        ["hermes2^example.com", "hermes3^example.com"],
        "Sunday playdate",
        name="Sunday Playdate", goal="Agree on a playdate time",
    )

    assert result["status"] == "started"
    assert result["conversation_id"].startswith("conv-")
    assert result["members"][0] == identity.address  # self is first
    assert sorted(result["members"][1:]) == [
        "hermes2^example.com",
        "hermes3^example.com",
    ]
    assert result["no_relationship_warning"] == []
    assert all(r["status"] == "invited" for r in result["invitations"])
    assert fake_client.send_envelope_raw.call_count == 2
    # Conversation persisted locally with all members.
    conv = ConversationStore.load(tmp_hermes_home).get(result["conversation_id"])
    assert conv is not None
    assert conv.purpose == "Sunday playdate"
    assert conv.convener == identity.address


@pytest.mark.asyncio
async def test_group_start_expands_hosted_address_shorthand(
    tmp_hermes_home, identity, fake_client
):
    from aap.relationships import RelationshipRecord, RelationshipStore
    from aap_hermes.tools import aap_group_start_handler

    RelationshipStore.load(tmp_hermes_home)._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address="hermes2^agentaddress.org",
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    fake_client.send_envelope_raw = AsyncMock(return_value=42)

    result = await aap_group_start_handler(
        fake_client, identity,
        ["hermes2^"],
        "Sunday playdate",
        name="Sunday Playdate",
        goal="Agree on a playdate time",
    )

    assert result["status"] == "started"
    assert result["members"][1:] == ["hermes2^agentaddress.org"]
    assert result["no_relationship_warning"] == []


@pytest.mark.asyncio
async def test_group_start_rejects_empty_members(tmp_hermes_home, identity, fake_client):
    from aap_hermes.tools import aap_group_start_handler
    result = await aap_group_start_handler(fake_client, identity, [], "x", name="x", goal="x")
    assert result["status"] == "error"
    assert "non-empty" in result["detail"]


@pytest.mark.asyncio
async def test_group_start_rejects_oversized_group(tmp_hermes_home, identity, fake_client):
    from aap_hermes.tools import aap_group_start_handler
    members = [f"m{i}^example.com" for i in range(15)]
    result = await aap_group_start_handler(fake_client, identity, members, "huge group", name="Huge Group", goal="test")
    assert result["status"] == "error"
    assert "cap" in result["detail"].lower()


@pytest.mark.asyncio
async def test_group_start_warns_on_missing_relationships(
    tmp_hermes_home, identity, fake_client
):
    """Sending an invitation to someone without a friend/admin/team
    relationship doesn't fail (the receiver will drop), but the warning
    list lets the LLM see the problem upfront."""
    from aap_hermes.tools import aap_group_start_handler
    fake_client.send_envelope_raw = AsyncMock(return_value=42)
    result = await aap_group_start_handler(
        fake_client, identity, ["stranger^example.com"], "won't work", name="Won't Work", goal="test",
    )
    assert result["status"] == "started"
    assert "stranger^example.com" in result["no_relationship_warning"]


def test_group_list_returns_local_conversations(tmp_hermes_home, identity):
    from aap.conversations import Conversation, ConversationStore
    from aap_hermes.tools import aap_group_list_handler

    store = ConversationStore.load(tmp_hermes_home)
    store.record(Conversation(
        conversation_id="conv-1",
        purpose="dinner planning",
        members=[identity.address, "mary^example.com"],
        convener=identity.address,
        accepted_at="2026-05-28T12:00:00Z",
        last_message_at=None,
    ))
    out = aap_group_list_handler()
    assert out["status"] == "ok"
    assert len(out["conversations"]) == 1
    assert out["conversations"][0]["conversation_id"] == "conv-1"
    assert out["conversations"][0]["purpose"] == "dinner planning"


# -- adapter: auto-accept group invitations from friend/admin/team ---------


@pytest.mark.asyncio
async def test_group_invitation_from_friend_auto_accepts(
    tmp_hermes_home, monkeypatch
):
    """Friend/admin/team convener's invitation → conversation is recorded
    locally without any human /aap group accept step."""
    from aap.envelope import Envelope
    from aap.keys import generate_keypair
    from aap.payloads import GroupInvitation

    from aap_hermes.adapter import AAPPlatformAdapter
    from aap_hermes._hermes_base import Platform
    from aap.conversations import ConversationStore
    from aap.identity import IdentityFile
    from aap.relationships import RelationshipRecord

    seed, public = generate_keypair()
    self_identity = IdentityFile(
        private_seed=seed, public_key=public, address="chris^example.com",
    )
    config = MagicMock()
    config.extra = {}
    adapter = AAPPlatformAdapter(
        config=config, platform=Platform("aap"),
        relay_url="https://relay.example.com", identity=self_identity,
    )
    adapter._resolve_peer_label = AsyncMock(return_value="Mary")
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    convener = "mary^example.com"
    # Write directly to the adapter's in-memory store so the handler sees it.
    adapter.stores.relationships._add_verified(RelationshipRecord(
        relationship_type="friend",
        peer_address=convener,
        established_at="2026-05-26T12:00:00Z",
        proposal_envelope_json="{}",
        accept_envelope_json="{}",
    ))

    invite = GroupInvitation(
        conversation_id="conv-auto-accept",
        purpose="Sunday playdate",
        members=[convener, self_identity.address, "bob^example.com"],
        convener=convener,
        nonce="nonce-xyz",
    )
    convener_seed, _ = generate_keypair()
    env = Envelope(
        type="aap.envelope/v1",
        payload_type=GroupInvitation.PAYLOAD_TYPE,
        payload=invite.to_dict(),
        iss=convener,
        iat="2026-05-28T12:00:00Z",
    ).sign(convener_seed)

    await adapter._handle_group_invitation(env, {"id": 1})

    conv = ConversationStore.load(tmp_hermes_home).get("conv-auto-accept")
    assert conv is not None, "conversation should be auto-recorded"
    assert conv.purpose == "Sunday playdate"
    assert conv.convener == convener
    assert self_identity.address in conv.members


@pytest.mark.asyncio
async def test_group_invitation_from_stranger_dropped(tmp_hermes_home, monkeypatch):
    """No relationship with convener → invitation is dropped, no
    conversation recorded."""
    from aap.envelope import Envelope
    from aap.keys import generate_keypair
    from aap.payloads import GroupInvitation

    from aap_hermes.adapter import AAPPlatformAdapter
    from aap_hermes._hermes_base import Platform
    from aap.conversations import ConversationStore
    from aap.identity import IdentityFile

    seed, public = generate_keypair()
    self_identity = IdentityFile(
        private_seed=seed, public_key=public, address="chris^example.com",
    )
    config = MagicMock()
    config.extra = {}
    adapter = AAPPlatformAdapter(
        config=config, platform=Platform("aap"),
        relay_url="https://relay.example.com", identity=self_identity,
    )
    monkeypatch.setattr("aap_hermes.adapter.mirror_to_home_channels", MagicMock())

    stranger = "stranger^example.com"
    invite = GroupInvitation(
        conversation_id="conv-stranger",
        purpose="spam",
        members=[stranger, self_identity.address],
        convener=stranger,
        nonce="nonce-spam",
    )
    convener_seed, _ = generate_keypair()
    env = Envelope(
        type="aap.envelope/v1",
        payload_type=GroupInvitation.PAYLOAD_TYPE,
        payload=invite.to_dict(),
        iss=stranger,
        iat="2026-05-28T12:00:00Z",
    ).sign(convener_seed)

    await adapter._handle_group_invitation(env, {"id": 2})

    assert ConversationStore.load(tmp_hermes_home).get("conv-stranger") is None


# -- aap_send_service_request: records origin from current SessionSource ---


@pytest.mark.asyncio
async def test_send_service_request_records_origin_from_aap_session(
    tmp_hermes_home, identity, fake_client
):
    """When the LLM turn was AAP-originated and the session-source
    contextvar is populated, the origin record carries the right
    SessionSource fields and target_address."""
    from datetime import datetime, timezone
    from aap.services import ServiceCatalog, ServiceDefinition
    from aap_hermes.tools import aap_send_service_request_handler
    from aap_hermes.service_request_origins import ServiceRequestOriginIndex
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    sd = ServiceDefinition(
        id="ask", display_name="Ask", description="",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        output_schema={}, verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"ask": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    source = SessionSource(
        platform="aap",
        chat_id="aap-group:conv-dinner",
        chat_type="group",
        user_id="ian^example.com",
        user_name="Ian",
        thread_id=None,
        chat_name="Group Dinner",
    )
    token = set_current_session_source(source)
    try:
        result = await aap_send_service_request_handler(
            fake_client, identity, catalog_cache,
            "biz^example.com", "ask", {"text": "hours?"},
            group_conversation_id="aap-group:conv-dinner",
        )
    finally:
        reset_current_session_source(token)

    assert result["status"] == "sent"
    nonce = result["request_nonce"]

    origins = ServiceRequestOriginIndex(tmp_hermes_home)
    popped = origins.pop_if(nonce, "biz^example.com")
    assert popped is not None
    assert popped.target_address == "biz^example.com"
    assert popped.chat_id == "aap-group:conv-dinner"
    assert popped.chat_type == "group"
    assert popped.group_conversation_id == "aap-group:conv-dinner"
    assert popped.platform == "aap"


@pytest.mark.asyncio
async def test_send_service_request_records_origin_from_telegram_session(
    tmp_hermes_home, identity, fake_client
):
    """Telegram-originated turn (non-AAP platform): the origin record
    must carry the Telegram SessionSource fields so the eventual response
    routes back into the Telegram session, not a fresh AAP DM."""
    from datetime import datetime, timezone
    from aap.services import ServiceCatalog, ServiceDefinition
    from aap_hermes.tools import aap_send_service_request_handler
    from aap_hermes.service_request_origins import ServiceRequestOriginIndex
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    sd = ServiceDefinition(
        id="ask", display_name="Ask", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"ask": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    source = SessionSource(
        platform="telegram",
        chat_id="tg:12345",
        chat_type="dm",
        user_id="tg-user-9",
        user_name="Ian",
        thread_id=None,
    )
    token = set_current_session_source(source)
    try:
        result = await aap_send_service_request_handler(
            fake_client, identity, catalog_cache,
            "biz^example.com", "ask", {"text": "?"},
        )
    finally:
        reset_current_session_source(token)

    nonce = result["request_nonce"]
    origins = ServiceRequestOriginIndex(tmp_hermes_home)
    popped = origins.pop_if(nonce, "biz^example.com")
    assert popped is not None
    assert popped.platform == "telegram"
    assert popped.chat_id == "tg:12345"
    assert popped.chat_type == "dm"


@pytest.mark.asyncio
async def test_send_service_request_warns_when_session_source_missing(
    tmp_hermes_home, identity, fake_client, caplog
):
    """If the gateway hasn't populated the session-source contextvar,
    the handler logs WARNING and the send still succeeds — but no origin
    is recorded, so the eventual response will be dropped on arrival."""
    from datetime import datetime, timezone
    import logging
    from aap.services import ServiceCatalog, ServiceDefinition
    from aap_hermes.tools import aap_send_service_request_handler
    from aap_hermes.service_request_origins import ServiceRequestOriginIndex

    sd = ServiceDefinition(
        id="ask", display_name="Ask", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"ask": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    with caplog.at_level(logging.WARNING):
        result = await aap_send_service_request_handler(
            fake_client, identity, catalog_cache,
            "biz^example.com", "ask", {"text": "?"},
        )

    assert result["status"] == "sent"
    assert "no session context available" in caplog.text

    origins = ServiceRequestOriginIndex(tmp_hermes_home)
    popped = origins.pop_if(result["request_nonce"], "biz^example.com")
    assert popped is None


@pytest.mark.asyncio
async def test_send_service_request_falls_back_to_hermes_session_context(
    tmp_hermes_home, identity, fake_client, monkeypatch
):
    """When the AAP-side session-source ContextVar is not set (e.g. a
    home-channel turn calling aap_send_service_request),
    the handler must fall back to Hermes's gateway-side per-turn
    ContextVars (HERMES_SESSION_PLATFORM / HERMES_SESSION_CHAT_ID / …)
    so the eventual async response can still route back into the
    originating session. This is the path scenario 2 actually exercises."""
    from datetime import datetime, timezone
    import sys
    import types
    from aap.services import ServiceCatalog, ServiceDefinition
    from aap_hermes.tools import aap_send_service_request_handler
    from aap_hermes.service_request_origins import ServiceRequestOriginIndex

    # Stub gateway.session_context so the tool's `from gateway.session_context
    # import get_session_env` import succeeds, returning whatever we want
    # for the HERMES_SESSION_* variables.
    fake_vars = {
        "HERMES_SESSION_PLATFORM": "example",
        "HERMES_SESSION_CHAT_ID": "user-chat",
        "HERMES_SESSION_USER_ID": "ian",
        "HERMES_SESSION_USER_NAME": "Ian Fletcher",
        "HERMES_SESSION_THREAD_ID": "",
        "HERMES_SESSION_CHAT_NAME": "",
    }
    fake_module = types.ModuleType("gateway.session_context")
    fake_module.get_session_env = lambda name, default="": fake_vars.get(name, default)
    fake_pkg = types.ModuleType("gateway")
    monkeypatch.setitem(sys.modules, "gateway", fake_pkg)
    monkeypatch.setitem(sys.modules, "gateway.session_context", fake_module)

    sd = ServiceDefinition(
        id="ask", display_name="Ask", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"ask": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    result = await aap_send_service_request_handler(
        fake_client, identity, catalog_cache,
        "biz^example.com", "ask", {"text": "?"},
    )

    assert result["status"] == "sent"
    nonce = result["request_nonce"]

    origins = ServiceRequestOriginIndex(tmp_hermes_home)
    popped = origins.pop_if(nonce, "biz^example.com")
    assert popped is not None
    assert popped.platform == "example"
    assert popped.chat_id == "user-chat"
    # chat_type defaults to "dm" since Hermes's session_context doesn't
    # expose it; matches the gateway's session-key shape across platforms.
    assert popped.chat_type == "dm"
    assert popped.user_name == "Ian Fletcher"
    assert popped.user_id == "ian"


@pytest.mark.asyncio
async def test_aap_session_source_wins_over_hermes_fallback(
    tmp_hermes_home, identity, fake_client, monkeypatch
):
    """If both the AAP ContextVar and the Hermes session_context are set,
    the AAP source wins. This matters because the AAP source carries the
    correct chat_type for AAP groups, which Hermes session_context
    doesn't expose."""
    from datetime import datetime, timezone
    import sys
    import types
    from aap.services import ServiceCatalog, ServiceDefinition
    from aap_hermes.tools import aap_send_service_request_handler
    from aap_hermes.service_request_origins import ServiceRequestOriginIndex
    from aap_hermes.turn_context import (
        set_current_session_source, reset_current_session_source,
    )
    from _hermes_compat import SessionSource

    fake_vars = {
        "HERMES_SESSION_PLATFORM": "example",
        "HERMES_SESSION_CHAT_ID": "user-chat",
    }
    fake_module = types.ModuleType("gateway.session_context")
    fake_module.get_session_env = lambda name, default="": fake_vars.get(name, default)
    fake_pkg = types.ModuleType("gateway")
    monkeypatch.setitem(sys.modules, "gateway", fake_pkg)
    monkeypatch.setitem(sys.modules, "gateway.session_context", fake_module)

    sd = ServiceDefinition(
        id="ask", display_name="Ask", description="",
        input_schema={"type": "object"}, output_schema={},
        verification_required={},
    )
    catalog = ServiceCatalog(
        business_address="biz^example.com",
        fetched_at=datetime.now(timezone.utc),
        services={"ask": sd},
    )
    catalog_cache = MagicMock()
    catalog_cache.get = AsyncMock(return_value=catalog)

    aap_source = SessionSource(
        platform="aap",
        chat_id="aap-group:conv-1",
        chat_type="group",
        user_id="ian",
        user_name="Ian Fletcher",
        thread_id=None,
    )
    token = set_current_session_source(aap_source)
    try:
        result = await aap_send_service_request_handler(
            fake_client, identity, catalog_cache,
            "biz^example.com", "ask", {"text": "?"},
            group_conversation_id="aap-group:conv-1",
        )
    finally:
        reset_current_session_source(token)

    origins = ServiceRequestOriginIndex(tmp_hermes_home)
    popped = origins.pop_if(result["request_nonce"], "biz^example.com")
    assert popped is not None
    # AAP source wins over Hermes fallback — preserves chat_type="group"
    # that Hermes's session_context can't express.
    assert popped.platform == "aap"
    assert popped.chat_id == "aap-group:conv-1"
    assert popped.chat_type == "group"
