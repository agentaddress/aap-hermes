"""Runtime state shared between tool handler, command handler, and adapter.

Hermes invokes the tool/command callbacks separately from the adapter, but
they all need access to the same AAPClient instance. We use module-level
state for v0.1 (single-adapter, single-process). Replace with a proper context
if Hermes ever supports multiple parallel AAP adapters.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from . import scenario_log
from .adapter import AAPPlatformAdapter
from .commands import handle_aap_command
from .mirror import mirror_home_reply_to_group_session, post_system_notice
from .tools import (
    aap_describe_service_handler,
    aap_group_complete_handler,
    aap_group_list_handler,
    aap_group_send_handler,
    aap_group_start_handler,
    aap_list_relationships_handler,
    aap_list_services_handler,
    aap_propose_friendship_handler,
    aap_propose_relationship_handler,
    aap_revoke_relationship_handler,
    aap_send_message_handler,
    aap_send_service_request_handler,
    aap_verify_confirm_handler,
    aap_verify_start_handler,
)

logger = logging.getLogger(__name__)

_adapter: Optional[AAPPlatformAdapter] = None


def set_adapter(adapter: AAPPlatformAdapter) -> None:
    global _adapter
    _adapter = adapter


def get_adapter() -> Optional[AAPPlatformAdapter]:
    return _adapter


def get_stores():
    """Return the adapter's store bundle (gateway mode) or build fresh stores
    from HERMES_HOME (CLI / test mode). Used by tools.py and commands.py to
    avoid per-call inline store construction."""
    if _adapter is not None:
        return _adapter.stores
    from .adapter import _build_stores_from_env
    return _build_stores_from_env()


@asynccontextmanager
async def _resolve_runtime():
    """Yield ``(client, identity, catalog_cache, has_gateway_adapter)``.

    Gateway mode (default): use the running adapter's long-lived
    AAPClient + ServiceCatalogCache. ``has_gateway_adapter`` is True so
    callers know the dispatch loop is running and synchronous
    request/reply waits will resolve.

    CLI mode (no adapter): build transient resources from disk-loaded
    identity + a fresh httpx-backed catalog cache. Closed when the
    context exits. ``has_gateway_adapter`` is False — synchronous
    request/reply waits will time out because nothing is polling the
    inbox here; the gateway (if running on the same identity) will
    receive replies and mirror them to the home channel.
    """
    if _adapter is not None:
        # Always create a fresh AAPClient for tool calls. The adapter's
        # long-lived client owns the polling connection and is bound to the
        # AAP poll-loop's event loop. Tool calls can fire from any platform's
        # thread (Telegram, Discord, etc.), each with its own event loop, so
        # reusing the adapter client causes "bound to a different event loop"
        # errors on the httpx connection pool's internal asyncio primitives.
        from pathlib import Path
        from aap.client import AAPClient
        fresh_client = AAPClient(
            relay_url=_adapter.client.relay_url,
            seed=_adapter.identity.private_seed,
            public_key=_adapter.identity.public_key,
            encryption_private_key=_adapter.identity.encryption_private_key,
            address=_adapter.identity.address,
        )
        try:
            yield fresh_client, _adapter.identity, _adapter._service_catalog, True
        finally:
            await fresh_client.close()
        return

    from pathlib import Path
    from aap.client import AAPClient
    from .config import Settings, build_address
    from aap.identity import load_or_generate
    from aap.services import ServiceCatalogCache

    try:
        settings = Settings()
    except Exception as e:
        raise RuntimeError(f"AAP not configured: {e}") from e
    address = build_address(settings)
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    identity = load_or_generate(
        identity_path=home / "aap.json",
        env_seed_b64=settings.AAP_PRIVATE_SEED_B64,
        address=address,
    )
    client = AAPClient(
        relay_url=settings.AAP_RELAY_URL,
        seed=identity.private_seed,
        public_key=identity.public_key,
        encryption_private_key=identity.encryption_private_key,
        address=identity.address,
    )
    async def agent_public_key_resolver(agent_address: str) -> bytes:
        from aap.keys import decode_b64url

        card = await client.resolve_agent_card(agent_address)
        return decode_b64url(card.public_key)

    catalog_cache = ServiceCatalogCache(
        cache_dir=home / "aap-service-catalog-cache",
        agent_public_key_resolver=agent_public_key_resolver,
    )
    try:
        yield client, identity, catalog_cache, False
    finally:
        await client.close()
        await catalog_cache.aclose()


def _bad_args(detail: str) -> dict[str, Any]:
    return {"status": "error", "detail": detail}


def _scenario_logged(tool_name: str):
    """Wrap an async tool handler so it emits scenario_log tool_call/tool_result.

    Cheap when HERMES_SCENARIO_LOG_DIR is unset (scenario_log.log is a pass).
    """

    def _decorate(fn):
        async def _wrapped(args: dict, **kwargs: Any) -> dict[str, Any]:
            scenario_log.log(
                "tool_call", data={"name": tool_name, "args": args},
            )
            result = await fn(args, **kwargs)
            scenario_log.log(
                "tool_result", data={"name": tool_name, "result": result},
            )
            return result

        # Preserve the wrapped function's name / docstring so other code
        # that introspects (e.g., logging) sees the original.
        _wrapped.__name__ = fn.__name__
        _wrapped.__doc__ = fn.__doc__
        return _wrapped

    return _decorate


@_scenario_logged("aap_send_message")
async def tool_handler_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args(f"aap_send_message: expected args dict, got {type(args).__name__}")
    to = args.get("to")
    text = args.get("text")
    if not to:
        return _bad_args("aap_send_message: missing required argument 'to'")
    if not text:
        return _bad_args("aap_send_message: missing required argument 'text'")
    try:
        async with _resolve_runtime() as (client, identity, catalog_cache, _has):
            return await aap_send_message_handler(
                client, identity, catalog_cache, to, text,
            )
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_group_start")
async def group_start_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_group_start: expected args dict")
    members = args.get("members")
    purpose = args.get("purpose")
    name = args.get("name")
    if not isinstance(members, list) or not members:
        return _bad_args(
            "aap_group_start: 'members' must be a non-empty list of "
            "peer addresses (don't include yourself)."
        )
    if not isinstance(purpose, str) or not purpose.strip():
        return _bad_args(
            "aap_group_start: 'purpose' must be a short description string."
        )
    if not isinstance(name, str) or not name.strip():
        return _bad_args(
            "aap_group_start: 'name' is required — confirm a short group name "
            "(2-4 words, 30 chars max) with your user before calling this tool."
        )
    goal = args.get("goal", "")
    if not isinstance(goal, str) or not goal.strip():
        return _bad_args(
            "aap_group_start: 'goal' is required — specify the concrete outcome "
            "that marks this group conversation as complete."
        )
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            result = await aap_group_start_handler(
                client, identity, members, purpose, name=name.strip(), goal=goal.strip()
            )
        if isinstance(result, dict) and result.get("conversation_id"):
            scenario_log.log(
                "group_started",
                layer="named",
                conv_id=result["conversation_id"],
                data={
                    "members": args.get("members", []),
                    "purpose": args.get("purpose"),
                },
            )
        return result
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_group_complete")
async def group_complete_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_group_complete: expected args dict")
    conv_id = args.get("conversation_id")
    outcome = args.get("outcome")
    if not conv_id:
        return _bad_args("aap_group_complete: missing required argument 'conversation_id'")
    if not outcome:
        return _bad_args("aap_group_complete: missing required argument 'outcome'")
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await aap_group_complete_handler(client, identity, conv_id, outcome)
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_group_list")
async def group_list_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    # No args required; ignore anything passed.
    return aap_group_list_handler()


@_scenario_logged("aap_group_send")
async def group_send_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_group_send: expected args dict")
    conv_id = args.get("conversation_id")
    text = args.get("text")
    if not conv_id:
        return _bad_args("aap_group_send: missing required argument 'conversation_id'")
    if not text:
        return _bad_args("aap_group_send: missing required argument 'text'")
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await aap_group_send_handler(client, identity, conv_id, text)
    except RuntimeError as e:
        return _bad_args(str(e))



# -- v0.6 service + relationship tool wrappers ------------------------------


@_scenario_logged("aap_list_services")
async def list_services_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_list_services: expected args dict")
    business = args.get("business_address")
    if not business:
        return _bad_args("aap_list_services: missing 'business_address'")
    try:
        async with _resolve_runtime() as (_c, _i, catalog_cache, _has):
            return await aap_list_services_handler(catalog_cache, business)
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_describe_service")
async def describe_service_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_describe_service: expected args dict")
    business = args.get("business_address")
    service_id = args.get("service_id")
    if not business:
        return _bad_args("aap_describe_service: missing 'business_address'")
    if not service_id:
        return _bad_args("aap_describe_service: missing 'service_id'")
    try:
        async with _resolve_runtime() as (_c, _i, catalog_cache, _has):
            return await aap_describe_service_handler(catalog_cache, business, service_id)
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_send_service_request")
async def send_service_request_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_send_service_request: expected args dict")
    business = args.get("business_address")
    service_id = args.get("service_id")
    payload = args.get("payload")
    group_conversation_id = args.get("group_conversation_id") or None
    if not business:
        return _bad_args("aap_send_service_request: missing 'business_address'")
    if not service_id:
        return _bad_args("aap_send_service_request: missing 'service_id'")
    if not isinstance(payload, dict):
        return _bad_args(
            "aap_send_service_request: 'payload' must be a dict matching the catalog input_schema"
        )
    try:
        async with _resolve_runtime() as (client, identity, catalog_cache, _has):
            return await aap_send_service_request_handler(
                client, identity, catalog_cache, business, service_id, payload,
                group_conversation_id=group_conversation_id,
            )
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_propose_friendship")
async def propose_friendship_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_propose_friendship: expected args dict")
    peer = args.get("peer_address")
    if not peer:
        return _bad_args("aap_propose_friendship: missing 'peer_address'")
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await aap_propose_friendship_handler(client, identity, peer)
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_propose_relationship")
async def propose_relationship_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_propose_relationship: expected args dict")
    peer = args.get("peer_address")
    rel_type = args.get("relationship_type")
    resource = args.get("resource")
    if not peer:
        return _bad_args("aap_propose_relationship: missing 'peer_address'")
    if not rel_type:
        return _bad_args(
            "aap_propose_relationship: missing 'relationship_type' "
            "(must be 'friend', 'admin', or 'team')"
        )
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await aap_propose_relationship_handler(
                client, identity, peer, rel_type, resource,
            )
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_list_relationships")
async def list_relationships_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    # Pure store read — no client / adapter needed; works in CLI mode unchanged.
    return aap_list_relationships_handler()


@_scenario_logged("aap_revoke_relationship")
async def revoke_relationship_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_revoke_relationship: expected args dict")
    peer = args.get("peer_address")
    rel_type = args.get("relationship_type")
    resource = args.get("resource")
    if not peer:
        return _bad_args("aap_revoke_relationship: missing 'peer_address'")
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await aap_revoke_relationship_handler(
                client, identity, peer, rel_type, resource,
            )
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_verify_start")
async def verify_start_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_verify_start: expected args dict")
    identity_type = args.get("identity_type")
    value = args.get("value")
    if not identity_type:
        return _bad_args("aap_verify_start: missing 'identity_type'")
    if not value:
        return _bad_args("aap_verify_start: missing 'value'")
    try:
        async with _resolve_runtime() as (_c, identity, _cc, _has):
            return await aap_verify_start_handler(identity, identity_type, value)
    except RuntimeError as e:
        return _bad_args(str(e))


@_scenario_logged("aap_verify_confirm")
async def verify_confirm_tool_wrapper(args: dict, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return _bad_args("aap_verify_confirm: expected args dict")
    code = args.get("code")
    if not code:
        return _bad_args("aap_verify_confirm: missing 'code'")
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await aap_verify_confirm_handler(client, identity, code)
    except RuntimeError as e:
        return _bad_args(str(e))


async def command_handler_wrapper(text: str, **kwargs: Any) -> str:
    """Route a /aap slash command to the right client/identity.

    When the gateway is running, ``_adapter`` is set and we build a transient
    AAPClient from its identity. The adapter's long-lived client owns the AAP
    poll connection and must stay on that event loop. When invoked from the chat
    REPL — which doesn't start platform adapters — we build the same transient
    client from env + identity file. That makes ``/aap send``, ``/aap whoami``,
    and ``/aap status`` usable from any Hermes surface, not just the gateway.

    Hermes's slash-command dispatcher strips the registered prefix before
    invoking us — so for ``/aap send X y`` we receive bare args (``"send X y"``).
    But the gateway pre-dispatch path hands us the full text. Normalise to
    the full ``/aap ...`` form so ``handle_aap_command`` parses both.
    """
    if text and not text.lstrip().startswith("/aap"):
        text = f"/aap {text.lstrip()}"

    if _adapter is not None:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            return await handle_aap_command(text, client, identity)

    from pathlib import Path
    from aap.client import AAPClient
    from .config import Settings, build_address
    from aap.identity import load_or_generate

    try:
        settings = Settings()
    except Exception as e:
        return f"AAP not configured: {e}"
    address = build_address(settings)
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    identity = load_or_generate(
        identity_path=home / "aap.json",
        env_seed_b64=settings.AAP_PRIVATE_SEED_B64,
        address=address,
    )
    client = AAPClient(
        relay_url=settings.AAP_RELAY_URL,
        seed=identity.private_seed,
        public_key=identity.public_key,
        encryption_private_key=identity.encryption_private_key,
        address=identity.address,
    )
    try:
        return await handle_aap_command(text, client, identity)
    finally:
        await client.close()


def predispatch_consent_check(event: Any = None, **kwargs: Any) -> Any:
    """``pre_gateway_dispatch`` hook: treat a bare ``approve`` / ``deny`` /
    ``block`` reply on the home channel as resolution of the most-recent
    pending consent prompt — either a discovery-introduction-request or
    a relationship-proposal. Returns ``{"action": "skip", ...}`` to
    suppress further dispatch when we handled the reply; ``None`` otherwise.

    Priority: introductions first (time-bounded by verifier wait window),
    then relationship proposals.

    MUST be a sync function — Hermes's ``invoke_hook`` calls callbacks
    synchronously and ignores returned coroutines. The actual approve/deny
    envelope send happens on a background task scheduled into the gateway's
    running event loop.
    """
    text_raw = getattr(event, "text", "") or ""
    text = text_raw.strip().lower()
    if text not in {"approve", "deny", "block"}:
        return None
    if _adapter is None:
        return None

    # Discovery introduction first (verifier-bounded wait window).
    from aap.stores.pending_introductions import PendingIntroductions
    _home_pi = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    intro_nonce = PendingIntroductions.load(_home_pi).most_recent_nonce()
    if intro_nonce is not None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "predispatch_consent_check called outside an event loop; "
                "cannot schedule async resolution for intro=%s", intro_nonce,
            )
            return None
        loop.create_task(_resolve_intro_async(text, intro_nonce))
        return {"action": "skip", "reason": "aap-intro-resolved"}

    # ``block`` only makes sense for introductions.
    if text == "block":
        return None

    # Friendship proposal consent.
    from aap.stores.pending_proposals import PendingProposalStore
    _home_pp = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    proposal_nonce = PendingProposalStore.load(_home_pp).most_recent_inbound_nonce()
    if proposal_nonce is None:
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "predispatch_consent_check called outside an event loop; "
            "cannot schedule async resolution for proposal=%s", proposal_nonce,
        )
        return None
    loop.create_task(_resolve_proposal_async(text, proposal_nonce))
    return {"action": "skip", "reason": "aap-proposal-resolved"}


def predispatch_group_reply_bridge(event: Any = None, **kwargs: Any) -> Any:
    """Route home-channel replies directly into the active AAP group session.

    When the bridge succeeds, suppress the normal home-channel dispatch so the
    reply is handled exactly once — in the group session — rather than also
    spawning a fresh home-channel session that starts without group context.
    """
    try:
        bridged = mirror_home_reply_to_group_session(event)
    except Exception:
        logger.exception("AAP group home-reply bridge failed")
        return None
    if bridged:
        return {"action": "skip", "reason": "aap-group-home-reply-bridged"}
    return None


async def _resolve_intro_async(verb: str, nonce: str) -> None:
    from .commands import _discover_approve, _discover_deny, _discover_block
    from aap.stores.pending_introductions import PendingIntroductions

    _home_ri = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            if verb == "approve":
                reply = await _discover_approve([nonce], client, identity)
            elif verb == "deny":
                reply = await _discover_deny([nonce], client, identity)
            else:  # block
                row = PendingIntroductions.load(_home_ri).get(nonce)
                if row is None:
                    post_system_notice(f"❌ No pending introduction for {nonce}")
                    return
                reply = await _discover_block(
                    [row.searcher], client, identity,
                )
                PendingIntroductions.load(_home_ri).resolve(nonce)
    except Exception as e:
        logger.exception("Bare-word introduction resolution failed")
        post_system_notice(f"❌ Failed to resolve introduction ({nonce}): {e}")
        return

    emoji = {"approve": "✅", "deny": "🚫", "block": "⛔"}[verb]
    post_system_notice(f"{emoji} {reply}")


async def _resolve_proposal_async(verb: str, nonce: str) -> None:
    from .commands import _friend_accept_cmd, _friend_decline_cmd
    try:
        async with _resolve_runtime() as (client, identity, _cc, _has):
            if verb == "approve":
                reply = await _friend_accept_cmd(nonce, client, identity)
            else:
                reply = await _friend_decline_cmd(nonce, client, identity)
    except Exception as e:
        logger.exception("Bare-word proposal resolution failed")
        post_system_notice(f"❌ Failed to resolve proposal ({nonce}): {e}")
        return
    emoji = "✅" if verb == "approve" else "🚫"
    post_system_notice(f"{emoji} {reply}")


async def predispatch_command_check(event: Any) -> Any:
    """Pre-dispatch hook: if the message starts with /aap, handle and suppress."""
    if not hasattr(event, "text") or not event.text.startswith("/aap"):
        return event
    if _adapter is None:
        return event
    async with _resolve_runtime() as (client, identity, _cc, _has):
        reply = await handle_aap_command(event.text, client, identity)
    logger.info("/aap reply: %s", reply)
    return None  # suppress further dispatch
