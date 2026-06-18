"""Cross-platform mirror for inbound/outbound AAP messages.

When the user runs this plugin alongside other Hermes platforms (Telegram,
Discord, Slack, etc.), every AAP message - both inbound from peer agents and
outbound from this agent - is also posted to the user's home channel on
each of those platforms. This gives the human a unified view of their
agent's AAP traffic in whatever chat surface they already use.

Opt out with ``AAP_MIRROR=off`` in ``~/.hermes/.env``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from . import scenario_log

logger = logging.getLogger(__name__)

_GROUP_HOME_CONTEXT_TTL_SECONDS = 30 * 60


def _home_channel_chat_id(home_channel) -> str:
    """Return the real chat id from Hermes HomeChannel or legacy string."""
    return str(getattr(home_channel, "chat_id", home_channel))


def _context_store_path() -> Path:
    home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    return Path(home) / "aap-group-home-contexts.json"


@dataclass
class GroupHomeContext:
    platform: str
    chat_id: str
    conversation_id: str
    group_label: str
    sender: str
    text_preview: str
    recorded_at: float


def _load_group_home_contexts() -> list[GroupHomeContext]:
    path = _context_store_path()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    except Exception:
        logger.exception("Failed to load %s", path)
        return []

    contexts: list[GroupHomeContext] = []
    cutoff = time.time() - _GROUP_HOME_CONTEXT_TTL_SECONDS
    for item in data.get("contexts") or []:
        try:
            ctx = GroupHomeContext(
                platform=str(item["platform"]),
                chat_id=str(item["chat_id"]),
                conversation_id=str(item["conversation_id"]),
                group_label=str(item.get("group_label") or item["conversation_id"]),
                sender=str(item.get("sender") or ""),
                text_preview=str(item.get("text_preview") or ""),
                recorded_at=float(item.get("recorded_at") or 0),
            )
        except Exception:
            continue
        if ctx.recorded_at >= cutoff:
            contexts.append(ctx)
    return contexts


def _save_group_home_contexts(contexts: list[GroupHomeContext]) -> None:
    path = _context_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"contexts": [asdict(c) for c in contexts]}, indent=2)
        )
    except Exception:
        logger.exception("Failed to save %s", path)


def record_group_home_context(
    *,
    platform: str,
    chat_id: str,
    conversation_id: str,
    group_label: str,
    sender: str,
    text: str,
) -> None:
    if not conversation_id:
        return
    contexts = [
        c for c in _load_group_home_contexts()
        if not (
            c.platform == platform
            and c.chat_id == chat_id
            and c.conversation_id == conversation_id
        )
    ]
    contexts.append(GroupHomeContext(
        platform=platform,
        chat_id=chat_id,
        conversation_id=conversation_id,
        group_label=group_label,
        sender=sender,
        text_preview=text[:240],
        recorded_at=time.time(),
    ))
    _save_group_home_contexts(contexts[-20:])


def mirror_home_reply_to_group_session(event) -> bool:
    """Copy a home-channel user reply into the recent matching group session.

    The LLM later handling AAP group traffic should see first-class evidence
    that *our* human replied on the home channel, not just an assistant-written
    group broadcast summary.
    """
    if os.getenv("AAP_MIRROR", "on").strip().lower() == "off":
        return False
    text = (getattr(event, "text", "") or "").strip()
    if not text or text.startswith("/aap"):
        return False
    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    if not platform or not chat_id:
        return False

    matches = [
        c for c in _load_group_home_contexts()
        if c.platform == platform and c.chat_id == chat_id
    ]
    if not matches:
        return False
    ctx = max(matches, key=lambda c: c.recorded_at)

    try:
        from . import _runtime
        adapter = _runtime.get_adapter()
    except Exception:
        logger.exception("Could not resolve AAP adapter for home reply bridge")
        return False
    if adapter is None or not hasattr(adapter, "enqueue_group_home_reply"):
        logger.debug("AAP adapter unavailable; skipping home reply bridge")
        return False

    try:
        queued = adapter.enqueue_group_home_reply(
            conversation_id=ctx.conversation_id,
            group_label=ctx.group_label,
            text=text,
        )
    except Exception:
        logger.exception(
            "Home reply bridge failed for %s:%s -> group %s",
            platform, chat_id, ctx.conversation_id,
        )
        return False
    if queued:
        logger.info(
            "Queued home reply from %s:%s into AAP group session (%s)",
            platform, chat_id, ctx.conversation_id,
        )
    return bool(queued)

# Lazy imports because Hermes modules aren't importable at unit-test collection
# time without the full gateway loaded. Tests patch these names directly.
try:
    from gateway.config import load_gateway_config  # type: ignore
except ImportError:  # pragma: no cover
    load_gateway_config = None  # type: ignore

try:
    from tools.send_message_tool import _handle_send  # type: ignore
except ImportError:  # pragma: no cover
    _handle_send = None  # type: ignore


def mirror_to_home_channels(
    *,
    sender: str | None,
    recipient: str | None,
    text: str,
    direction: Literal["inbound", "outbound"],
    thread_id: str | None = None,
    group_label: str | None = None,
) -> None:
    """Post an AAP-traffic notification to every configured home channel.

    Safe to call from both the gateway long-poll path (adapter._dispatch) and
    the REPL lazy-client path (commands.py / _runtime.py). Failures on
    individual platforms are logged and swallowed - they must not block AAP
    message processing.

    When ``group_label`` is provided (for group inbounds), the notification
    reads "AAP group 'Dinner Planning' from hermes3" rather than plain
    "AAP from hermes3".
    """
    # Compute address up-front so the scenario-log event reflects the
    # intended peer even when the home-channel dispatch is short-circuited.
    address = sender if direction == "inbound" else recipient
    if address:
        scenario_log.log(
            "user_view" if direction == "outbound" else "user_input",
            layer="named",
            audience="user",
            data={
                "text": text,
                "direction": direction,
                "peer": address,
            },
        )
    else:
        return

    if os.getenv("AAP_MIRROR", "on").strip().lower() == "off":
        return

    if load_gateway_config is None or _handle_send is None:
        logger.debug("Hermes gateway modules unavailable; skipping mirror")
        return

    try:
        config = load_gateway_config()
    except Exception:
        logger.exception("Could not load gateway config; skipping mirror")
        return

    thread_suffix = f" (thread: {thread_id})" if thread_id else ""
    if direction == "inbound":
        if group_label:
            arrow = f"\U0001f465 AAP group ‘{group_label}’ from"
        else:
            arrow = "\U0001f4e8 AAP from"
    else:
        arrow = "\U0001f4e4 You sent to"
    body = f"{arrow} {address}{thread_suffix}:\n{text}"

    for platform, pconfig in config.platforms.items():
        if not pconfig.enabled or not pconfig.home_channel:
            continue
        try:
            _handle_send({"target": platform.value, "message": body})
        except Exception:
            logger.exception(
                "Mirror to %s home channel failed", platform.value
            )


def mirror_outbound_to_aap_session(peer: str, text: str) -> None:
    """Append an outbound AAP message into the local AAP-with-peer session
    as an assistant turn.

    Without this mirror, the calling LLM (e.g. a Telegram-originated turn
    that uses aap_send_message to ping hermes2) sends a wire envelope but
    leaves no trace in the AAP-with-peer session. When the peer's reply
    arrives later, the AAP session dispatches a fresh LLM turn that has
    no context for *why* the peer is suddenly talking — it sees an
    unsolicited message and panics ("agent X informed me of an
    unauthorized send from my address!").

    Mirroring the outbound here gives that session an "assistant: <our
    outbound>" entry, so the next inbound reads as a coherent reply,
    not a bolt from the blue.

    Failures are logged and swallowed — never block AAP traffic.
    """
    if os.getenv("AAP_MIRROR", "on").strip().lower() == "off":
        return
    try:
        from gateway.run import _gateway_runner_ref  # type: ignore
        from gateway.platforms.base import SessionSource, Platform  # type: ignore
    except ImportError:
        logger.debug("Hermes gateway modules unavailable; skipping AAP session mirror")
        return
    runner = _gateway_runner_ref()
    if runner is None or getattr(runner, "session_store", None) is None:
        logger.debug("Gateway runner not active; skipping AAP session mirror")
        return
    try:
        source = SessionSource(
            platform=Platform("aap"),
            chat_id=peer,
            chat_type="dm",
            user_id=peer,
            user_name=peer,
        )
        entry = runner.session_store.get_or_create_session(source)
        from datetime import datetime, timezone
        runner.session_store.append_to_transcript(
            entry.session_id,
            {
                "role": "assistant",
                "content": text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.exception("AAP session mirror failed for peer %s", peer)


def mirror_outbound_to_aap_group_session(conversation_id: str, text: str) -> None:
    """Append an outbound group-broadcast message into the local
    AAP-group session as an assistant turn.

    Mirrors the 1:1 ``mirror_outbound_to_aap_session`` for groups: the
    group inbound path uses ``chat_id = "aap-group:<conv_id>"`` so the
    group conversation has its own session, distinct from any 1:1
    session with a member. Mirroring our broadcasts keeps that session
    coherent across multiple turns.
    """
    if os.getenv("AAP_MIRROR", "on").strip().lower() == "off":
        return
    try:
        from gateway.run import _gateway_runner_ref  # type: ignore
        from gateway.platforms.base import SessionSource, Platform  # type: ignore
    except ImportError:
        logger.debug(
            "Hermes gateway modules unavailable; skipping AAP group session mirror"
        )
        return
    runner = _gateway_runner_ref()
    if runner is None or getattr(runner, "session_store", None) is None:
        logger.debug("Gateway runner not active; skipping AAP group session mirror")
        return
    chat_id = f"aap-group:{conversation_id}"
    try:
        source = SessionSource(
            platform=Platform("aap"),
            chat_id=chat_id,
            chat_type="dm",
            user_id=chat_id,
            user_name=f"group:{conversation_id}",
        )
        entry = runner.session_store.get_or_create_session(source)
        from datetime import datetime, timezone
        # Prefix with a clear marker so the group session LLM knows this
        # broadcast came from another session (e.g. Telegram). The user's
        # actual home-channel replies are mirrored separately as explicit user
        # turns; do not imply every broadcast is a confirmation.
        marked_text = f"[Broadcast sent to group]: {text}"
        runner.session_store.append_to_transcript(
            entry.session_id,
            {
                "role": "assistant",
                "content": marked_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.exception(
            "AAP group session mirror failed for conv %s", conversation_id,
        )


def mirror_group_inbound_to_home_session(
    group_label: str,
    sender: str,
    text: str,
    conversation_id: str = "",
) -> None:
    """Inject an incoming group message as an assistant turn into each home
    platform session.

    When an agent (e.g. hermes1) receives a group message, the LLM
    processes it in the ``aap-group:<conv_id>`` session and may forward a
    'USER REQUIRED' prompt to the human via ``send_message``. But when the
    human replies in Telegram, that reply lands in the home Telegram session
    which has no knowledge of the group context. By writing the inbound
    message here — before the group session LLM runs — the home session
    transcript has context:

        [assistant]: 📩 Group 'Dinner Planning' from hermes3: "Does that work?"
        [user]: Yes that works for me   ← human reply lands with full context

    Failures are logged and swallowed — never block AAP traffic.
    """
    if os.getenv("AAP_MIRROR", "on").strip().lower() == "off":
        return
    try:
        from gateway.run import _gateway_runner_ref  # type: ignore
        from gateway.platforms.base import SessionSource  # type: ignore
    except ImportError:
        logger.debug(
            "Hermes gateway modules unavailable; skipping group home session injection"
        )
        return
    runner = _gateway_runner_ref()
    if runner is None or getattr(runner, "session_store", None) is None:
        logger.debug("Gateway runner not active; skipping group home session injection")
        return

    if load_gateway_config is None:
        return
    try:
        config = load_gateway_config()
    except Exception:
        logger.exception(
            "Could not load gateway config; skipping group home session injection"
        )
        return

    conv_suffix = (
        f" [group_conversation_id: {conversation_id}]" if conversation_id else ""
    )
    body = (
        f"\U0001f465 Group message forwarded to you from '{group_label}'"
        f"{conv_suffix} (sender: {sender}):\n{text}"
    )
    from datetime import datetime, timezone
    for platform, pconfig in config.platforms.items():
        if not pconfig.enabled or not pconfig.home_channel:
            continue
        try:
            home_chat_id = _home_channel_chat_id(pconfig.home_channel)
            source = SessionSource(
                platform=platform,
                chat_id=home_chat_id,
                chat_type="dm",
                user_id=home_chat_id,
                user_name=home_chat_id,
            )
            entry = runner.session_store.get_or_create_session(source)
            runner.session_store.append_to_transcript(
                entry.session_id,
                {
                    "role": "assistant",
                    "content": body,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.debug(
                "Injected group context into home session %s on %s",
                entry.session_id, platform.value,
            )
            record_group_home_context(
                platform=platform.value,
                chat_id=home_chat_id,
                conversation_id=conversation_id,
                group_label=group_label,
                sender=sender,
                text=text,
            )
        except Exception:
            logger.exception(
                "Group home session injection failed for %s", platform.value
            )


def post_system_notice(text: str) -> None:
    """Post a plain notice (no inbound/outbound arrow framing) to every
    configured home channel. Used by the predispatch consent hook to
    surface "✅ approved" / "❌ denied" confirmations after the user
    types a bare "approve"/"deny" reply.
    """
    if os.getenv("AAP_MIRROR", "on").strip().lower() == "off":
        return
    if load_gateway_config is None or _handle_send is None:
        logger.debug("Hermes gateway modules unavailable; skipping system notice")
        return
    try:
        config = load_gateway_config()
    except Exception:
        logger.exception("Could not load gateway config; skipping system notice")
        return
    for platform, pconfig in config.platforms.items():
        if not pconfig.enabled or not pconfig.home_channel:
            continue
        try:
            _handle_send({"target": platform.value, "message": text})
        except Exception:
            logger.exception(
                "System notice to %s home channel failed", platform.value
            )
