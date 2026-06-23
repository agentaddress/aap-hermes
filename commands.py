"""/aap slash command handler."""

from __future__ import annotations

import logging
import shlex
from aap.keys import decode_b64url, encode_b64url

from aap.client import AAPClient, AAPClientError
from aap.identity import IdentityFile
from .address_input import parse_user_address
from .mirror import mirror_to_home_channels

logger = logging.getLogger(__name__)


async def _resolve_verifier_public_key(stores, verifier_domain: str) -> bytes | None:
    """Resolve a verifier key from the signed trust list currently held by stores."""
    trust_list = await stores.trust_list_cache.get()
    return await stores.verifier_pubkey_cache.get(verifier_domain, trust_list)


def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/", 1)[0]


def _get_stores():
    """Return the adapter's store bundle (gateway mode) or fresh stores from
    HERMES_HOME (CLI/test mode). Replaces per-call inline store construction."""
    from . import _runtime
    return _runtime.get_stores()


async def handle_aap_command(
    command_text: str,
    client: AAPClient,
    identity: IdentityFile,
) -> str:
    """Parse and dispatch a /aap <subcommand> ... call.

    Returns a string to echo back to the user.
    """
    if not command_text.startswith("/aap"):
        return ""  # not our command

    try:
        parts = shlex.split(command_text)
    except ValueError as e:
        return f"Could not parse /aap command: {e}"

    if len(parts) < 2:
        return _help()

    subcommand = parts[1].lower()

    if subcommand == "whoami":
        return (
            f"Address: {identity.address}\n"
            f"Public key: {encode_b64url(identity.public_key)}"
        )

    if subcommand == "send":
        if len(parts) < 4:
            return "Usage: /aap send <address> <text>"
        to = parts[2]
        text = " ".join(parts[3:])
        try:
            to = parse_user_address(to)
        except ValueError as e:
            return f"Invalid address {to!r}: {e}"
        # v0.6: relationship-gated send. Friend / admin / team peers receive
        # chat freely; everyone else is refused.
        if _get_stores().relationships.any_relationship_with(to) is None:
            return (
                f"No friend/admin/team relationship with {to}. "
                f"Use /aap friend {to} to propose one, then retry."
            )
        try:
            envelope_id = await client.send_envelope(to=to, text=text)
        except AAPClientError as e:
            return f"Send failed: {e}"
        mirror_to_home_channels(
            sender=None, recipient=to, text=text, direction="outbound",
        )
        return f"Sent to {to} (envelope id {envelope_id})"

    if subcommand == "status":
        return f"AAP adapter running. Address: {identity.address}"

    if subcommand == "rotate-key":
        if len(parts) < 3:
            return (
                "Usage:\n"
                "  /aap rotate-key start <email>\n"
                "  /aap rotate-key confirm <code>"
            )
        sub = parts[2].lower()
        if sub == "start":
            if len(parts) < 4:
                return "Usage: /aap rotate-key start <email>"
            return await _rotate_key_start(identity.address, parts[3])
        if sub == "confirm":
            if len(parts) < 4:
                return "Usage: /aap rotate-key confirm <code>"
            return await _rotate_key_confirm(parts[3])
        return f"Unknown /aap rotate-key subcommand: {sub!r}"

    if subcommand == "inspect":
        return _inspect_cmd(parts[2:])

    if subcommand == "bind":
        return _bind_identity(parts[2:])

    if subcommand == "unbind":
        if len(parts) < 3:
            return "Usage: /aap unbind <address>"
        return _unbind_identity(parts[2])

    if subcommand == "group":
        return await _handle_group_subcommand(parts[2:], client, identity)

    if subcommand == "verify":
        return await _handle_verify_subcommand(parts[2:], client, identity)

    if subcommand == "attestations":
        return _handle_attestations_subcommand(parts[2:])

    if subcommand == "verifiers":
        return await _handle_verifiers_subcommand(parts[2:])

    if subcommand == "trust-verifier":
        return _trust_verifier(parts[2:])

    if subcommand == "distrust-verifier":
        return _distrust_verifier(parts[2:])

    if subcommand == "discover":
        return await _handle_discover_subcommand(parts[2:], client, identity)

    # v0.6 — services + relationships
    if subcommand == "services":
        return await _list_services_cmd(parts[2:], client, identity)

    if subcommand == "describe":
        return await _describe_service_cmd(parts[2:], client, identity)

    if subcommand == "friend":
        return await _propose_friendship_cmd(parts[2:], client, identity)

    if subcommand == "unfriend":
        return await _revoke_friendship_cmd(parts[2:], client, identity)

    if subcommand == "friends":
        return _list_friends_cmd()

    if subcommand == "friend-accept":
        if len(parts) < 3:
            return "Usage: /aap friend-accept <proposal-nonce>"
        return await _friend_accept_cmd(parts[2], client, identity)

    if subcommand == "friend-decline":
        if len(parts) < 3:
            return "Usage: /aap friend-decline <proposal-nonce>"
        return await _friend_decline_cmd(parts[2], client, identity)

    if subcommand == "clear_conversation":
        return _clear_conversation_cmd(parts[2:])

    return _help()


def _help() -> str:
    return (
        "AAP commands:\n"
        "  /aap send <address> <text>          send a message to a friend/admin/team peer\n"
        "  /aap whoami                         print your AAP address\n"
        "  /aap status                         print adapter status\n"
        "  /aap inspect [group|peer|home] [id]  inspect recent hidden channel transcripts\n"
        "  /aap friend <peer> [type] [resource]  propose a relationship (type=friend|admin|team, default friend; team needs a resource label)\n"
        "  /aap friend-accept <nonce>          accept an inbound friendship proposal\n"
        "  /aap friend-decline <nonce>         decline an inbound friendship proposal\n"
        "  /aap unfriend <peer>                revoke a friendship\n"
        "  /aap friends                        list current friend/admin/team relationships\n"
        "  /aap services <business>            list a business's published service catalog\n"
        "  /aap describe <business> <service>  show one service's full JSON Schema\n"
        "  /aap bind <address> <contact-id>    bind a peer to a local contact\n"
        "  /aap unbind <address>               remove an identity binding\n"
        "  /aap verify phone <number>                       verify a phone number with a trusted verifier\n"
        "  /aap verify email <addr>                         verify an email address\n"
        "  /aap verify confirm <code>                       confirm an in-flight verification\n"
        "  /aap attestations list                           list held verification attestations\n"
        "  /aap verifiers list                              list trusted verifiers\n"
        "  /aap trust-verifier <domain> <public-key-b64>    add a verifier to the local override list\n"
        "  /aap distrust-verifier <domain>                  remove a verifier via override\n"
        "  /aap discover phone <number>                     query trusted verifiers for an agent\n"
        "  /aap discover approve <nonce>                    approve a pending introduction\n"
        "  /aap discover deny <nonce>                       decline a pending introduction\n"
        "  /aap discover block <searcher-address>           block a specific searcher\n"
        "  /aap discover list                               list pending introductions\n"
        "  /aap clear_conversation <peer|conv_id>           wipe the local AAP chat history with a 1:1 peer OR group\n"
        "  /aap group start <m1> <m2>... [-- <purpose>]    start a multi-peer conversation\n"
        "  /aap group accept <nonce> [--no-auto-trust]     accept an inbound group invitation\n"
        "  /aap group leave <conv_id> [<reason>]           leave a group conversation\n"
        "  /aap group add <conv_id> <member>               add a member (requires friend/admin/team)\n"
        "  /aap group remove <conv_id> <member>            remove a member\n"
        "  /aap group send <conv_id> <text>                broadcast a message to a group\n"
        "  /aap group list                                  list current group conversations"
    )


def _inspect_cmd(args: list[str]) -> str:
    """Inspect recent hidden AAP/Home channel transcript data.

    Reads the local Hermes profile's sessions index and SQLite message store.
    This is intentionally read-only and bounded so it is safe to call from any
    Hermes surface when debugging multi-channel flows.
    """
    import json
    import os
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    sessions_file = home / "sessions" / "sessions.json"
    db_file = home / "state.db"
    if not sessions_file.exists():
        return f"No sessions index found at {sessions_file}."
    if not db_file.exists():
        return f"No state database found at {db_file}."

    try:
        sessions = json.loads(sessions_file.read_text())
    except Exception as e:
        return f"Could not read sessions index: {e}"
    if not isinstance(sessions, dict):
        return "Could not inspect sessions: sessions index is malformed."

    def parse_limit(raw: str | None, default: int = 8) -> int:
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(1, min(value, 20))

    def entry_for_key(session_key: str) -> dict | None:
        entry = sessions.get(session_key)
        return entry if isinstance(entry, dict) else None

    def entries_matching(kind: str, ident: str | None = None) -> list[tuple[str, dict]]:
        rows: list[tuple[str, dict]] = []
        for key, entry in sessions.items():
            if not isinstance(entry, dict):
                continue
            if kind == "group":
                prefix = "agent:main:aap:dm:aap-group:"
                if not key.startswith(prefix):
                    continue
                if ident and key != f"{prefix}{ident}":
                    continue
            elif kind == "peer":
                prefix = "agent:main:aap:dm:"
                if not key.startswith(prefix) or key.startswith(f"{prefix}aap-group:"):
                    continue
                if ident and key != f"{prefix}{ident}":
                    continue
            elif kind == "home":
                if ":aap:" in key:
                    continue
                if ident and ident not in key:
                    continue
            rows.append((key, entry))
        return sorted(rows, key=lambda row: row[1].get("updated_at") or "", reverse=True)

    def summarize_index() -> str:
        groups = entries_matching("group")
        peers = entries_matching("peer")
        homes = entries_matching("home")
        lines = [
            "AAP inspect: local channel sessions",
            f"Home channels: {len(homes)}",
            f"AAP groups: {len(groups)}",
            f"AAP peers/services: {len(peers)}",
            "",
            "Use:",
            "  /aap inspect group <conversation_id> [limit]",
            "  /aap inspect peer <agent-address> [limit]",
            "  /aap inspect home [limit]",
        ]
        if groups:
            lines.append("")
            lines.append("Groups:")
            for key, entry in groups[:8]:
                conv_id = key.rsplit("aap-group:", 1)[-1]
                lines.append(
                    f"  - {conv_id} session={entry.get('session_id')} "
                    f"updated={entry.get('updated_at')}"
                )
        if peers:
            lines.append("")
            lines.append("AAP peers/services:")
            for key, entry in peers[:8]:
                peer = key.rsplit(":dm:", 1)[-1]
                lines.append(
                    f"  - {peer} session={entry.get('session_id')} "
                    f"updated={entry.get('updated_at')}"
                )
        return "\n".join(lines)

    def clip(text: str, limit: int = 99999) -> str:
        text = " ".join((text or "").replace("\n", " | ").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    def clean_user_content(text: str) -> str:
        """Strip trust preambles from user-role messages for readable display.

        Covers:
        - Group inbound:  [trust context: AAP message from <addr> within group '...' ...]<message>
        - 1:1 inbound:    [trust context: AAP message from <addr>. ...]<message>
        - Home reply:     [Home-channel reply from my user for AAP group '...' (...)]: <message>
        - Broadcast echo: [Broadcast sent to group — my user confirmed]: <message>
        - Service resp:   GROUP UPDATE REQUIRED / trust note prefixes
        """
        import re as _re
        t = (text or "").strip()

        # Home-channel reply injection
        m = _re.match(
            r"\[Home-channel reply from my user for AAP group '([^']+)' \([^)]+\)\]:\s*(.*)",
            t, _re.DOTALL,
        )
        if m:
            return f"[home reply → group '{m.group(1)}']: {m.group(2).strip()}"

        # Broadcast echo mirror
        m = _re.match(
            r"\[Broadcast sent to group(?:[^\]]*)?\]:\s*(.*)",
            t, _re.DOTALL,
        )
        if m:
            return f"[broadcast echo]: {m.group(1).strip()}"

        # Trust preamble (group or 1:1) — find the [Agent <addr>]: marker we
        # prepend to every message body. The preamble itself contains [NO_REPLY]
        # and similar bracketed tokens, so we can't rely on finding the closing
        # ] of the trust context — instead we anchor on the unambiguous label.
        m = _re.search(r'\[Agent ([\w.\-+^]+)\]:\s*(.*)', t, _re.DOTALL)
        if m:
            sender_short = m.group(1).split("^")[0]
            msg = m.group(2).strip()
            return f"[from {sender_short}]: {msg}" if msg else f"[from {sender_short}]: (empty)"

        # Fallback: at least show the sender from the trust context header
        m = _re.match(r'\[trust context: AAP message from ([\w.\-+^]+)', t)
        if m:
            sender_short = m.group(1).split("^")[0]
            return f"[from {sender_short}]: (message body not found)"

        return t

    def render_tool_calls(raw: str | None) -> str:
        if not raw:
            return ""
        try:
            calls = json.loads(raw)
        except Exception:
            return clip(raw)
        rendered: list[str] = []
        for call in calls if isinstance(calls, list) else [calls]:
            if not isinstance(call, dict):
                rendered.append(clip(str(call)))
                continue
            fn = call.get("function") or {}
            name = fn.get("name") or call.get("name") or "tool"
            args = fn.get("arguments") or call.get("arguments") or ""
            rendered.append(f"{name}({clip(str(args))})")
        return "; ".join(rendered)

    def render_session(label: str, session_id: str, limit: int) -> str:
        try:
            with sqlite3.connect(db_file) as db:
                rows = db.execute(
                    """
                    select timestamp, role, coalesce(tool_name, ''),
                           coalesce(content, ''), coalesce(tool_calls, '')
                    from messages
                    where session_id = ?
                    order by timestamp desc
                    limit ?
                    """,
                    (session_id, limit),
                ).fetchall()
        except Exception as e:
            return f"Could not read messages for {label}: {e}"
        rows = list(reversed(rows))
        lines = [f"{label}", f"session={session_id}", f"last {len(rows)} message(s):"]
        if not rows:
            lines.append("  (no messages)")
            return "\n".join(lines)
        for ts, role, tool_name, content, tool_calls in rows:
            try:
                stamp = datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                stamp = str(ts)
            hidden = render_tool_calls(tool_calls)
            if hidden:
                body = hidden
                if content:
                    body += f" | {clip(content)}"
            elif role == "user":
                body = clip(clean_user_content(content))
            else:
                body = clip(content)
            role_label = f"{role}:{tool_name}" if tool_name else role
            lines.append(f"  {stamp} {role_label}: {body}")
        return "\n".join(lines)

    if not args:
        return summarize_index()

    kind = args[0].lower()
    if kind == "group":
        if len(args) < 2:
            return "Usage: /aap inspect group <conversation_id> [limit]"
        conv_id = args[1]
        limit = parse_limit(args[2] if len(args) > 2 else None)
        key = f"agent:main:aap:dm:aap-group:{conv_id}"
        entry = entry_for_key(key)
        if entry is None:
            return f"No AAP group session found for {conv_id!r}."
        return render_session(f"AAP group {conv_id}", str(entry.get("session_id")), limit)

    if kind == "peer":
        if len(args) < 2:
            return "Usage: /aap inspect peer <agent-address> [limit]"
        try:
            peer = parse_user_address(args[1])
        except ValueError as e:
            return f"Invalid address {args[1]!r}: {e}"
        limit = parse_limit(args[2] if len(args) > 2 else None)
        key = f"agent:main:aap:dm:{peer}"
        entry = entry_for_key(key)
        if entry is None:
            return f"No AAP peer/service session found for {peer!r}."
        return render_session(f"AAP peer/service {peer}", str(entry.get("session_id")), limit)

    if kind == "home":
        limit = parse_limit(args[1] if len(args) > 1 else None)
        matches = entries_matching("home")
        if not matches:
            return "No home-channel sessions found."
        key, entry = matches[0]
        return render_session(f"Home channel {key}", str(entry.get("session_id")), limit)

    return (
        "Usage:\n"
        "  /aap inspect\n"
        "  /aap inspect group <conversation_id> [limit]\n"
        "  /aap inspect peer <agent-address> [limit]\n"
        "  /aap inspect home [limit]"
    )


def _bind_identity(args: list[str]) -> str:
    if len(args) < 2:
        return "Usage: /aap bind <address> <contact-id>"
    try:
        peer = parse_user_address(args[0])
    except ValueError as e:
        return f"Invalid address {args[0]!r}: {e}"
    contact_id = args[1].strip()
    if not contact_id:
        return "Usage: /aap bind <address> <contact-id>"
    _get_stores().identity_bindings.bind(
        peer_address=peer,
        contact_id=contact_id,
        matched_identifier={"type": "manual", "value": contact_id},
    )
    return f"Bound {peer} to contact {contact_id}."


def _unbind_identity(address: str) -> str:
    try:
        peer = parse_user_address(address)
    except ValueError as e:
        return f"Invalid address {address!r}: {e}"
    removed = _get_stores().identity_bindings.unbind(peer)
    if not removed:
        return f"No identity binding found for {peer}."
    return f"Removed identity binding for {peer}."


def _clear_conversation_cmd(args: list[str]) -> str:
    """Reset the AAP session for either a 1:1 peer OR a group conversation.

    Args:
      <peer-address>          — clears the AAP-with-peer 1:1 session
      <conversation-id>       — clears the group session for that conv_id
                                (session is keyed ``aap-group:<conv_id>``)

    Useful when stale history from a prior unrelated topic is polluting
    the LLM's context. Works both inside the gateway (live in-memory
    reset, takes effect immediately) and from the CLI REPL (rewrites
    the on-disk session index + SQLite; running gateway needs a
    restart to drop its in-memory cache).
    """
    if not args:
        return "Usage: /aap clear_conversation <peer-address|conversation-id>"
    raw = args[0]

    # Distinguish peer addresses from conversation_ids: agent addresses
    # parse via parse_user_address; UUID-shaped conv_ids do not. Treat anything
    # Address-shaped as a 1:1 peer; everything else as a conv_id.
    label: str
    try:
        peer = parse_user_address(raw)
        label = peer
        chat_id = peer
    except ValueError:
        # Fall back to group conversation
        if not raw.strip():
            return f"Invalid argument {raw!r}: empty"
        label = f"group {raw}"
        chat_id = f"aap-group:{raw}"

    try:
        from gateway.run import _gateway_runner_ref  # type: ignore
        from gateway.session import build_session_key  # type: ignore
    except ImportError:
        return (
            "Cannot clear conversation: gateway modules unavailable. "
            "Use `hermes sessions delete <id>` from the command line "
            "instead."
        )

    from ._hermes_base import Platform
    try:
        from gateway.platforms.base import SessionSource  # type: ignore
    except ImportError:
        from gateway.session import SessionSource  # type: ignore

    source = SessionSource(
        platform=Platform("aap"),
        chat_id=chat_id,
        chat_type="dm",
        user_id=chat_id,
        user_name=chat_id,
    )
    session_key = build_session_key(source)

    # Preferred path: a live gateway runner is in this process — reset
    # via the in-memory SessionStore so the change is immediate.
    runner = _gateway_runner_ref()
    if runner is not None and getattr(runner, "session_store", None) is not None:
        new_entry = runner.session_store.reset_session(session_key)
        if new_entry is None:
            return f"No existing AAP conversation with {label} to clear."
        return (
            f"Cleared AAP conversation with {label}. "
            f"Next message starts fresh (new session: {new_entry.session_id})."
        )

    # Fallback: no live runner (e.g. invoked from the standalone CLI
    # REPL). Walk sessions.json by hand, delete the entry, drop the
    # SQLite rows. The currently-running gateway process won't see the
    # change until it restarts because its _entries cache is loaded
    # once at startup.
    return _clear_conversation_offline(label, session_key)


def _clear_conversation_offline(label: str, session_key: str) -> str:
    """Direct on-disk session-index + SQLite cleanup for CLI mode."""
    import json
    import os
    from pathlib import Path

    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    sessions_file = home / "sessions" / "sessions.json"
    if not sessions_file.exists():
        return (
            f"No sessions index at {sessions_file} — nothing to clear."
        )

    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"Could not read sessions index: {e}"

    entry = data.get(session_key)
    if not isinstance(entry, dict):
        return f"No existing AAP conversation with {label} to clear."
    session_id = entry.get("session_id")

    # Drop the entry and rewrite the index atomically.
    data.pop(session_key, None)
    try:
        tmp = sessions_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, sessions_file)
    except Exception as e:
        return f"Could not rewrite sessions index: {e}"

    # Best-effort: delete the SQLite rows for this session_id so the
    # transcript and message history go too. Failure here is non-fatal —
    # the index removal alone is enough to make the next inbound use a
    # fresh session.
    sqlite_note = ""
    if session_id:
        try:
            from hermes_state import SessionDB  # type: ignore
            db = SessionDB()
            db.delete_session(session_id, sessions_dir=home / "sessions")
        except Exception as e:
            sqlite_note = (
                f"\n(SQLite cleanup skipped: {e} — index removal still "
                f"completed; old messages may linger in the DB until "
                f"`hermes sessions delete {session_id}`.)"
            )

    return (
        f"Cleared AAP conversation with {label} (session {session_id}).\n"
        f"⚠  Restart the gateway for this to take effect on the "
        f"live process: `hermes -p <profile> gateway restart`.{sqlite_note}"
    )


async def _handle_group_subcommand(
    args: list[str], client, identity
) -> str:
    """Dispatch ``/aap group <action> ...``."""
    if not args:
        return _group_help()
    action = args[0].lower()
    rest = args[1:]
    if action == "start":
        return await _group_start(rest, client, identity)
    if action == "accept":
        return await _group_accept(rest, client, identity)
    if action == "leave":
        return await _group_leave(rest, client, identity)
    if action == "add":
        return await _group_add(rest, client, identity)
    if action == "remove":
        return await _group_remove(rest, client, identity)
    if action == "list":
        return _group_list()
    if action == "send":
        return await _group_send(rest, client, identity)
    return _group_help()


def _group_help() -> str:
    return (
        "Usage:\n"
        "  /aap group start <m1> <m2>... [-- <purpose>]\n"
        "  /aap group accept <nonce> [--no-auto-trust]\n"
        "  /aap group leave <conversation_id> [<reason>...]\n"
        "  /aap group add <conversation_id> <member>\n"
        "  /aap group remove <conversation_id> <member>\n"
        "  /aap group list\n"
        "  /aap group send <conversation_id> <text>"
    )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_conversation_id() -> str:
    import secrets
    return secrets.token_urlsafe(9)


def _split_members_purpose(args: list[str]) -> tuple[list[str], str]:
    """Split args by '--' separator: everything before is members,
    everything after is the purpose (joined by spaces)."""
    if "--" in args:
        i = args.index("--")
        members = args[:i]
        purpose = " ".join(args[i + 1:])
    else:
        members = list(args)
        purpose = "Group conversation"
    return members, purpose


async def _group_start(args: list[str], client, identity) -> str:
    """``/aap group start <m1> <m2>... [-- <purpose>]``.

    v0.6: invitations go out unauthenticated at the envelope level. The
    receiver's adapter gates on whether the convener has a friend/admin/
    team relationship with them — so each member you invite must already
    have one of those relationships established with you.
    """
    from aap.conversations import Conversation
    from aap.group_flow import build_group_invitation_envelope

    members_in, purpose = _split_members_purpose(args)
    if not members_in:
        return "Usage: /aap group start <m1> <m2>... [-- <purpose>]"

    canonical_members: list[str] = []
    for m in members_in:
        try:
            canonical_members.append(parse_user_address(m))
        except ValueError as e:
            return f"Invalid address {m!r}: {e}"
    members_in = canonical_members

    members = [identity.address] + [m for m in members_in if m != identity.address]
    if len(members) > 10:
        return (
            f"Group size cap (10) exceeded: you specified {len(members) - 1} other "
            f"members plus yourself."
        )

    # Pre-flight: warn about members we have no relationship with — the
    # invitation will be dropped on their side.
    _stores_gs = _get_stores()
    no_relationship = [m for m in members[1:] if _stores_gs.relationships.any_relationship_with(m) is None]

    conversation_id = _new_conversation_id()
    _stores_gs.conversations.record(Conversation(
        conversation_id=conversation_id,
        purpose=purpose,
        members=members,
        convener=identity.address,
        accepted_at=_now_iso(),
        last_message_at=None,
    ))

    results: list[tuple[str, str]] = []
    for recipient in members[1:]:
        env = build_group_invitation_envelope(
            convener_seed=identity.private_seed,
            convener_address=identity.address,
            conversation_id=conversation_id,
            purpose=purpose,
            members=members,
        )
        try:
            await client.send_envelope_raw(to=recipient, envelope_json=env.to_json())
            results.append((recipient, "sent"))
        except Exception as e:
            results.append((recipient, f"error: {e}"))

    ok = sum(1 for _, s in results if s == "sent")
    warn = ""
    if no_relationship:
        warn = (
            f" Warning: {len(no_relationship)} invitee(s) have no friend/admin/team "
            f"relationship with you — their adapter will drop the invitation: "
            f"{', '.join(no_relationship)}"
        )
    return (
        f"Created group {conversation_id} ({purpose!r}); "
        f"invitations sent to {ok}/{len(members) - 1} member(s).{warn}"
    )


async def _group_accept(args: list[str], client, identity) -> str:
    """``/aap group accept <nonce>``.

    v0.6: accepting just records the conversation locally. No bootstrap
    window or capability_request to other members — group chat is
    authorized by conversation membership at the receiver. The
    ``--no-auto-trust`` flag is accepted for backward compatibility but
    has no effect.
    """
    from aap.conversations import Conversation

    if not args:
        return "Usage: /aap group accept <nonce>"
    nonce = args[0]

    _stores_ga = _get_stores()
    pending = _stores_ga.pending_consents
    entry = pending.get(nonce)
    if not entry:
        return f"Group invitation {nonce!r} not found (may have already been resolved)."
    invite_data = entry["request"]
    if invite_data.get("kind") != "group_invitation":
        return f"{nonce!r} is not a group invitation."

    conversation_id = invite_data["conversation_id"]
    purpose = invite_data["purpose"]
    members = list(invite_data["members"])
    convener = invite_data["convener"]

    _stores_ga.conversations.record(Conversation(
        conversation_id=conversation_id,
        purpose=purpose,
        members=members,
        convener=convener,
        accepted_at=_now_iso(),
        last_message_at=None,
    ))
    pending.resolve(nonce)
    return f"Accepted group {conversation_id} ({purpose!r}). {len(members)} members."


async def _group_leave(args: list[str], client, identity) -> str:
    """``/aap group leave <conversation_id> [<reason>...]``."""
    from aap.group_flow import build_group_leave_envelope

    if not args:
        return "Usage: /aap group leave <conversation_id> [<reason>...]"
    conversation_id = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else None

    store = _get_stores().conversations
    conv = store.get(conversation_id)
    if conv is None:
        return f"Unknown conversation {conversation_id!r}."

    other_members = [m for m in conv.members if m != identity.address]
    sent_ok = 0
    for peer in other_members:
        env = build_group_leave_envelope(
            leaver_seed=identity.private_seed,
            leaver_address=identity.address,
            conversation_id=conversation_id,
            reason=reason,
        )
        try:
            await client.send_envelope_raw(to=peer, envelope_json=env.to_json())
            sent_ok += 1
        except Exception:
            pass

    store.dissolve(conversation_id)
    return (
        f"Left group {conversation_id}; notified {sent_ok}/{len(other_members)} "
        f"member(s){f' (reason: {reason})' if reason else ''}."
    )


async def _group_add(args: list[str], client, identity) -> str:
    """``/aap group add <conversation_id> <member>`` (convener-only)."""
    from aap.group_flow import build_group_membership_update_envelope

    if len(args) < 2:
        return "Usage: /aap group add <conversation_id> <member>"
    conversation_id, new_member = args[0], args[1]
    try:
        new_member = parse_user_address(new_member)
    except ValueError as e:
        return f"Invalid address {new_member!r}: {e}"

    store = _get_stores().conversations
    conv = store.get(conversation_id)
    if conv is None:
        return f"Unknown conversation {conversation_id!r}."
    if conv.convener != identity.address:
        return f"Only the convener can add members to {conversation_id}."
    if new_member in conv.members:
        return f"{new_member} is already a member of {conversation_id}."
    if len(conv.members) + 1 > 10:
        return (
            f"Group size cap (10) exceeded: {conversation_id} would have "
            f"{len(conv.members) + 1} members."
        )

    new_members = list(conv.members) + [new_member]
    store.update_members(conversation_id, new_members)

    targets = [m for m in new_members if m != identity.address]
    sent_ok = 0
    for peer in targets:
        env = build_group_membership_update_envelope(
            convener_seed=identity.private_seed,
            convener_address=identity.address,
            conversation_id=conversation_id,
            members=new_members,
            added=[new_member],
            removed=[],
            convener_changed_from=None,
        )
        try:
            await client.send_envelope_raw(to=peer, envelope_json=env.to_json())
            sent_ok += 1
        except Exception:
            pass
    return (
        f"Added {new_member} to {conversation_id}; "
        f"broadcast update to {sent_ok}/{len(targets)} member(s)."
    )


async def _group_remove(args: list[str], client, identity) -> str:
    """``/aap group remove <conversation_id> <member>`` (convener-only)."""
    from aap.group_flow import build_group_membership_update_envelope

    if len(args) < 2:
        return "Usage: /aap group remove <conversation_id> <member>"
    conversation_id, member = args[0], args[1]

    store = _get_stores().conversations
    conv = store.get(conversation_id)
    if conv is None:
        return f"Unknown conversation {conversation_id!r}."
    if conv.convener != identity.address:
        return f"Only the convener can remove members from {conversation_id}."
    if member not in conv.members:
        return f"{member} is not a member of {conversation_id}."

    new_members = [m for m in conv.members if m != member]
    store.update_members(conversation_id, new_members)

    targets = [m for m in conv.members if m != identity.address]
    sent_ok = 0
    for peer in targets:
        env = build_group_membership_update_envelope(
            convener_seed=identity.private_seed,
            convener_address=identity.address,
            conversation_id=conversation_id,
            members=new_members,
            added=[],
            removed=[member],
            convener_changed_from=None,
        )
        try:
            await client.send_envelope_raw(to=peer, envelope_json=env.to_json())
            sent_ok += 1
        except Exception:
            pass
    return (
        f"Removed {member} from {conversation_id}; "
        f"broadcast update to {sent_ok}/{len(targets)} member(s)."
    )


def _group_list() -> str:
    convs = _get_stores().conversations.list_active()
    if not convs:
        return "No active group conversations."
    lines = [f"Active group conversations ({len(convs)}):"]
    for c in convs:
        you_are = "convener" if c.convener == _local_self_address() else "member"
        lines.append(
            f"  • {c.conversation_id} — {c.purpose!r} "
            f"({len(c.members)} members; you: {you_are})"
        )
    return "\n".join(lines)


def _local_self_address() -> str:
    """Best-effort local address for display. Tries env first to avoid
    importing the runtime adapter from a sync function."""
    import os
    localpart = os.getenv("AAP_LOCALPART", "")
    domain = os.getenv("AAP_INSTANCE_DOMAIN", "")
    if localpart and domain:
        return f"{localpart}^{domain}"
    return ""


async def _group_send(args: list[str], client, identity) -> str:
    """``/aap group send <conversation_id> <text>``."""
    from aap.conversations import broadcast_to_conversation

    if len(args) < 2:
        return "Usage: /aap group send <conversation_id> <text>"
    conversation_id = args[0]
    text = " ".join(args[1:])

    try:
        results = await broadcast_to_conversation(
            client=client,
            self_address=identity.address,
            conversation_id=conversation_id,
            text=text,
            store=_get_stores().conversations,
        )
    except ValueError as e:
        return f"Send failed: {e}"

    ok = sum(1 for _, r in results if isinstance(r, int))
    failed = [(addr, r) for addr, r in results if not isinstance(r, int)]
    msg = f"Sent to {ok}/{len(results)} recipient(s)"
    if failed:
        failed_summary = ", ".join(f"{addr} ({err})" for addr, err in failed)
        msg += f"; failed: {failed_summary}"
    return msg + "."


# ── v0.9.0 verification + attestations + trusted-verifiers ─────────────────


async def _handle_verify_subcommand(args: list[str], client, identity) -> str:
    """Dispatch ``/aap verify <action> ...``."""
    if not args:
        return (
            "Usage:\n"
            "  /aap verify phone <number>\n"
            "  /aap verify email <addr>\n"
            "  /aap verify confirm <code>"
        )
    action = args[0].lower()
    rest = args[1:]
    if action == "phone":
        return await _verify_start("phone", rest, identity)
    if action == "email":
        return await _verify_start("email", rest, identity)
    if action == "confirm":
        return await _verify_confirm(rest, client, identity)
    return f"Unknown verify action {action!r}. Use phone, email, or confirm."


async def _verify_start(identity_type: str, args: list[str], identity) -> str:
    from aap.verifiers import trusted_verifiers_supporting
    from aap.stores.verification_flow import PendingVerificationRow
    from aap.verifier_client import (
        VerifierClientError,
        start_email_verification,
        start_sms_verification,
    )

    if not args:
        target_kind = "phone number" if identity_type == "phone" else "email address"
        return f"Usage: /aap verify {identity_type} <{target_kind}>"
    target = args[0]

    _stores_vs = _get_stores()
    _entries_vs = await _stores_vs.trust_list_cache.get()

    verifiers_list = trusted_verifiers_supporting(_entries_vs, identity_type)
    if not verifiers_list:
        return (
            f"No trusted verifier supports {identity_type!r}. "
            f"Check /aap verifiers list."
        )
    verifier = verifiers_list[0]
    verifier_public_key = await _stores_vs.verifier_pubkey_cache.get(
        verifier.domain,
        _entries_vs,
    )
    if verifier_public_key is None:
        return (
            f"Trusted verifier {verifier.domain} has no valid public key in "
            "the signed trust list."
        )

    try:
        if identity_type == "phone":
            result = await start_sms_verification(
                seed=identity.private_seed,
                subject_address=identity.address,
                phone=target,
                verification_endpoint=verifier.verification_endpoint,
                verifier_domain=verifier.domain,
                verifier_public_key=verifier_public_key,
            )
            challenge_descr = f"the code sent to {target}"
        else:
            result = await start_email_verification(
                seed=identity.private_seed,
                subject_address=identity.address,
                email=target,
                verification_endpoint=verifier.verification_endpoint,
                verifier_domain=verifier.domain,
                verifier_public_key=verifier_public_key,
            )
            challenge_descr = f"the link sent to {target}"
    except VerifierClientError as e:
        return f"Verification request failed: {e}"

    _stores_vs.pending_verifications.add(
        PendingVerificationRow(
            otp_id=result.otp_id,
            identity_type=identity_type,
            identifier_value=target,
            verifier_domain=verifier.domain,
            verification_endpoint=verifier.verification_endpoint,
            expires_at=result.expires_at,
        )
    )
    return (
        f"Verification started with {verifier.domain}. "
        f"Enter {challenge_descr}:\n"
        f"  /aap verify confirm <code>"
    )


async def _verify_confirm(args: list[str], client, identity) -> str:
    from aap.verifier_client import (
        VerifierClientError,
        confirm_email_verification,
        confirm_sms_verification,
    )

    if not args:
        return "Usage: /aap verify confirm <code>"
    code = args[0]

    _stores_vc = _get_stores()
    pending = _stores_vc.pending_verifications
    # Two acceptable forms: `confirm <code>` (one pending) or `confirm <otp_id> <code>`.
    row = None
    if len(args) >= 2:
        row = pending.get(args[0])
        code = args[1] if row else args[0]
    if row is None:
        row = pending.find_one()
    if row is None:
        return (
            "No pending verification to confirm. Run /aap verify phone "
            "<number> or /aap verify email <addr> first."
        )
    verifier_public_key = await _resolve_verifier_public_key(
        _stores_vc,
        row.verifier_domain,
    )
    if verifier_public_key is None:
        return (
            f"Cannot confirm: verifier {row.verifier_domain} is not in the "
            "signed trust list or has no valid public key."
        )

    try:
        if row.identity_type == "phone":
            attestation_json = await confirm_sms_verification(
                seed=identity.private_seed,
                subject_address=identity.address,
                otp_id=row.otp_id,
                otp=code,
                verification_endpoint=row.verification_endpoint,
                verifier_domain=row.verifier_domain,
                verifier_public_key=verifier_public_key,
            )
        else:
            attestation_json = await confirm_email_verification(
                seed=identity.private_seed,
                subject_address=identity.address,
                otp_id=row.otp_id,
                token=code,
                verification_endpoint=row.verification_endpoint,
                verifier_domain=row.verifier_domain,
                verifier_public_key=verifier_public_key,
            )
    except VerifierClientError as e:
        return f"Confirmation failed: {e}"

    try:
        _stores_vc.attestations.record(
            attestation_json,
            verifier_public_key=verifier_public_key,
        )
    except ValueError as e:
        return f"Verifier returned an invalid attestation: {e}"

    pending.remove(row.otp_id)  # renamed from resolve() in SDK

    # v0.6: no auto-grant. Discovery envelopes from trusted verifiers bypass
    # the relationship gate at the adapter level (they're handled in
    # _handle_discovery_introduction_request before the chat gate). No
    # capability token is needed for the verifier to reach us.
    kind_label = "Phone" if row.identity_type == "phone" else "Email"
    return (
        f"{kind_label} verified: {row.identifier_value}. "
        f"Stored attestation from {row.verifier_domain}."
    )


def _handle_attestations_subcommand(args: list[str]) -> str:
    if not args:
        return "Usage: /aap attestations list"
    action = args[0].lower()
    if action != "list":
        return f"Unknown attestations action {action!r}. Use 'list'."
    rows = _get_stores().attestations.rows
    if not rows:
        return "No verification attestations held."
    lines = [f"Held attestations ({len(rows)}):"]
    for r in rows:
        lines.append(
            f"  • {r.identity_type}={r.identifier_value} "
            f"verified_by={r.verifier} exp={r.expires_at}"
        )
    return "\n".join(lines)


async def _handle_verifiers_subcommand(args: list[str]) -> str:
    if not args:
        return "Usage: /aap verifiers list"
    action = args[0].lower()
    if action != "list":
        return f"Unknown verifiers action {action!r}. Use 'list'."
    entries = await _get_stores().trust_list_cache.get()
    if not entries:
        return "No trusted verifiers configured."
    lines = [f"Trusted verifiers ({len(entries)}):"]
    for v in entries:
        ids = ", ".join(v.supported_identities)
        lines.append(f"  • {v.domain} ({ids})")
    return "\n".join(lines)


def _trust_verifier(args: list[str]) -> str:
    """Add ``<domain> <public-key-b64>`` to the local-overrides ``add`` list.

    The override entry mirrors the standards-body schema; for ergonomics,
    we infer ``https://<domain>/...`` endpoint defaults so the user can
    type only the domain plus the verifier's pinned Ed25519 public key.
    """
    if len(args) < 2:
        return "Usage: /aap trust-verifier <domain> <public-key-b64>"
    domain = args[0].strip()
    public_key_b64 = args[1].strip()
    try:
        public_key = decode_b64url(public_key_b64)
    except ValueError as e:
        return f"Invalid verifier public key: {e}"
    if len(public_key) != 32:
        return "Invalid verifier public key: expected a 32-byte Ed25519 public key."
    overrides = _load_overrides_file()
    add_list = list(overrides.get("add") or [])
    remove_list = list(overrides.get("remove") or [])
    # If the domain was previously removed, un-remove it.
    if domain in remove_list:
        remove_list = [d for d in remove_list if d != domain]
    add_list = [e for e in add_list if e.get("domain") != domain]
    add_list.append({
        "domain": domain,
        "supported_identities": ["phone", "email"],
        "discovery_endpoint": f"https://{domain}/aap/discover",
        "verification_endpoint": f"https://{domain}/aap/verify",
        "pubkey_endpoint": f"https://{domain}/.well-known/aap-verifier-key",
        "public_key": public_key_b64,
    })
    overrides["add"] = add_list
    overrides["remove"] = remove_list
    _save_overrides_file(overrides)
    return f"Added {domain} to local trusted-verifier overrides."


def _distrust_verifier(args: list[str]) -> str:
    if not args:
        return "Usage: /aap distrust-verifier <domain>"
    domain = args[0].strip()
    overrides = _load_overrides_file()
    add_list = [e for e in (overrides.get("add") or []) if e.get("domain") != domain]
    remove_list = list(overrides.get("remove") or [])
    if domain not in remove_list:
        remove_list.append(domain)
    overrides["add"] = add_list
    overrides["remove"] = remove_list
    _save_overrides_file(overrides)
    return f"Removed {domain} from trust list via local override."


def _load_overrides_file() -> dict:
    import json
    import os
    from pathlib import Path

    home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    path = Path(home) / "aap-trusted-verifiers-overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_overrides_file(payload: dict) -> None:
    import json
    import os
    from pathlib import Path

    home = os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))
    path = Path(home) / "aap-trusted-verifiers-overrides.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    # Note: the injected TrustListCache re-reads the overrides file on each
    # get() call — no explicit cache invalidation needed here.


# ── v0.9.0 discovery ───────────────────────────────────────────────────────


async def _handle_discover_subcommand(args: list[str], client, identity) -> str:
    if not args:
        return (
            "Usage:\n"
            "  /aap discover phone <number>\n"
            "  /aap discover approve <nonce>\n"
            "  /aap discover deny <nonce>\n"
            "  /aap discover block <searcher-address>\n"
            "  /aap discover list"
        )
    action = args[0].lower()
    rest = args[1:]
    if action == "phone":
        return await _discover_initiate("phone", rest, identity)
    if action == "email":
        return await _discover_initiate("email", rest, identity)
    if action == "approve":
        return await _discover_approve(rest, client, identity)
    if action == "deny":
        return await _discover_deny(rest, client, identity)
    if action == "block":
        return await _discover_block(rest, client, identity)
    if action == "list":
        return _discover_list()
    return f"Unknown discover action {action!r}."


async def _discover_initiate(identity_type: str, args: list[str], identity) -> str:
    from aap.discovery import query_discovery

    if not args:
        target_kind = "phone number" if identity_type == "phone" else "email address"
        return f"Usage: /aap discover {identity_type} <{target_kind}>"
    target = args[0]

    _stores_di = _get_stores()

    # Optional: attach our own attestations so the target can render a
    # contact-match prompt.
    attestations = [
        r.attestation_envelope_json for r in _stores_di.attestations.rows
        if not r.is_expired() and r.identity_type in {"phone", "email"}
    ]
    trust_list = await _stores_di.trust_list_cache.get()

    async def verifier_public_key_resolver(entry):
        return await _stores_di.verifier_pubkey_cache.get(entry.domain, trust_list)

    address = await query_discovery(
        self_address=identity.address,
        self_seed=identity.private_seed,
        identity_type=identity_type,
        identifier_value=target,
        searcher_label=None,
        trust_list_cache=_stores_di.trust_list_cache,
        verifier_public_key_resolver=verifier_public_key_resolver,
        searcher_attestations=attestations,
    )
    if address is None:
        return (
            f"No match for {identity_type} {target} "
            f"(or target declined / verifier unreachable)."
        )
    return (
        f"Found agent for {identity_type} {target}: {address}.\n"
        f"To start a relationship, run /aap request {address} <scope>."
    )


async def _discover_approve(args: list[str], client, identity) -> str:
    from aap.discovery import build_introduction_response_envelope
    from aap.verifiers import verifier_relay_address

    if not args:
        return "Usage: /aap discover approve <nonce>"
    nonce = args[0]
    pending = _get_stores().pending_introductions
    row = pending.get(nonce)
    if row is None:
        return f"Introduction {nonce!r} not found (or already resolved)."
    env = build_introduction_response_envelope(
        responder_seed=identity.private_seed,
        responder_address=identity.address,
        verifier_nonce=nonce,
        approved=True,
    )
    try:
        await client.send_envelope_raw(
            to=verifier_relay_address(row.verifier_domain),
            envelope_json=env.to_json(),
        )
    except Exception as e:
        return f"Failed to send approval to {row.verifier_domain}: {e}"
    pending.resolve(nonce)
    return (
        f"Approved introduction {nonce}. {row.searcher} will receive your "
        f"AAP address via {row.verifier_domain}."
    )


async def _discover_deny(args: list[str], client, identity) -> str:
    from aap.discovery import build_introduction_response_envelope
    from aap.verifiers import verifier_relay_address

    if not args:
        return "Usage: /aap discover deny <nonce>"
    nonce = args[0]
    pending = _get_stores().pending_introductions
    row = pending.get(nonce)
    if row is None:
        return f"Introduction {nonce!r} not found (or already resolved)."
    env = build_introduction_response_envelope(
        responder_seed=identity.private_seed,
        responder_address=identity.address,
        verifier_nonce=nonce,
        approved=False,
    )
    try:
        await client.send_envelope_raw(
            to=verifier_relay_address(row.verifier_domain),
            envelope_json=env.to_json(),
        )
    except Exception as e:
        return f"Failed to send denial to {row.verifier_domain}: {e}"
    pending.resolve(nonce)
    return f"Denied introduction {nonce} (searcher will see no match)."


async def _discover_block(args: list[str], client, identity) -> str:
    """Send the verifier a block request for ``<searcher-address>``.

    Block-list operations flow through the verifier — the verifier holds
    the per-target block list. We POST a signed envelope to a
    ``/aap/block`` endpoint derived from the verifier's discovery endpoint.
    """
    if not args:
        return "Usage: /aap discover block <searcher-address>"
    searcher = args[0]

    # Determine which verifier this block goes to: prefer the verifier
    # that brokered any pending introduction from this searcher; otherwise
    # broadcast to all trusted verifiers (best-effort).
    _stores_db = _get_stores()
    pending = _stores_db.pending_introductions
    pending_for = [r for r in pending.rows.values() if r.searcher == searcher]
    if pending_for:
        verifier_domains = sorted({r.verifier_domain for r in pending_for})
    else:
        _tl_entries_db = await _stores_db.trust_list_cache.get()
        verifier_domains = [v.domain for v in _tl_entries_db]

    if not verifier_domains:
        return "No trusted verifiers configured; cannot send block request."

    import httpx
    from aap.envelope import Envelope as _Envelope
    from datetime import datetime as _dt, timezone as _tz

    sent_to: list[str] = []
    failed: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=15.0) as http:
        for domain in verifier_domains:
            url = f"https://{domain}/aap/discover/block"
            payload = {"searcher": searcher, "nonce": __import__("secrets").token_urlsafe(12)}
            env = _Envelope(
                type="aap.envelope/v1",
                payload_type="aap.discovery-block-request/v1",
                payload=payload,
                iss=identity.address,
                iat=_dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ).sign(identity.private_seed)
            try:
                resp = await http.post(
                    url,
                    content=env.to_json(),
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code >= 400:
                    failed.append((domain, f"HTTP {resp.status_code}"))
                else:
                    sent_to.append(domain)
            except Exception as e:
                failed.append((domain, str(e)))

    # Auto-deny any still-pending introduction from this searcher.
    auto_denied = 0
    for row in list(pending.rows.values()):
        if row.searcher == searcher:
            pending.resolve(row.verifier_nonce)
            auto_denied += 1

    parts = [f"Block request sent to {len(sent_to)}/{len(verifier_domains)} verifier(s)."]
    if auto_denied:
        parts.append(f"Auto-resolved {auto_denied} pending introduction(s) from {searcher}.")
    if failed:
        parts.append(
            "Failed: " + ", ".join(f"{d} ({e})" for d, e in failed)
        )
    return " ".join(parts)


def _discover_list() -> str:
    rows = list(_get_stores().pending_introductions.rows.values())
    if not rows:
        return "No pending discovery introductions."
    lines = [f"Pending introductions ({len(rows)}):"]
    for r in rows:
        label = f" ({r.searcher_label})" if r.searcher_label else ""
        lines.append(
            f"  • {r.verifier_nonce} — {r.searcher}{label} via {r.verifier_domain} "
            f"(expires {r.expires_at})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# v0.6 — services + relationships subcommands
# ---------------------------------------------------------------------------


async def _list_services_cmd(args, client, identity) -> str:
    """``/aap services <business-address>``."""
    if len(args) < 1:
        return "Usage: /aap services <business-address>"
    try:
        business = parse_user_address(args[0])
    except ValueError as e:
        return f"Invalid address: {e}"

    catalog = await _get_stores().service_catalog_cache.get(business)
    if catalog is None:
        return f"No catalog found at {business}. Is this address a business agent?"
    if not catalog.services:
        return f"{business} publishes an empty catalog."
    lines = [f"Services at {business}:"]
    for sd in catalog.services.values():
        ver = ", ".join(sd.verification_required) if sd.verification_required else "none"
        lines.append(f"  • {sd.id} — {sd.display_name}")
        if sd.description:
            lines.append(f"      {sd.description}")
        lines.append(f"      verification required: {ver}")
    return "\n".join(lines)


async def _describe_service_cmd(args, client, identity) -> str:
    """``/aap describe <business-address> <service-id>``."""
    if len(args) < 2:
        return "Usage: /aap describe <business-address> <service-id>"
    try:
        business = parse_user_address(args[0])
    except ValueError as e:
        return f"Invalid address: {e}"
    service_id = args[1]

    catalog = await _get_stores().service_catalog_cache.get(business)
    if catalog is None:
        return f"No catalog found at {business}."
    sd = catalog.get(service_id)
    if sd is None:
        return (
            f"Service {service_id!r} not found in {business}'s catalog. "
            f"Available: {', '.join(catalog.ids()) or '(none)'}"
        )
    import json as _json
    lines = [
        f"Service: {sd.display_name} ({sd.id})",
    ]
    if sd.description:
        lines.append(f"  {sd.description}")
    lines.append("")
    lines.append("Input schema:")
    lines.append(_json.dumps(sd.input_schema, indent=2))
    if sd.verification_required:
        lines.append("")
        lines.append("Verification required:")
        lines.append(_json.dumps(sd.verification_required, indent=2))
    if sd.recurrence:
        lines.append("")
        lines.append("Recurrence:")
        lines.append(_json.dumps(sd.recurrence, indent=2))
    return "\n".join(lines)


async def _propose_friendship_cmd(args, client, identity) -> str:
    """``/aap friend <peer-address> [type] [resource]``.

    Defaults to type='friend'. Pass 'admin' or 'team' to propose those
    instead. For team, also pass a resource label as the third arg.
    """
    if len(args) < 1:
        return (
            "Usage: /aap friend <peer-address> [friend|admin|team] [<resource>]\n"
            "       (type defaults to 'friend'; 'team' requires a resource label)"
        )
    try:
        peer = parse_user_address(args[0])
    except ValueError as e:
        return f"Invalid address: {e}"

    rel_type = args[1].lower() if len(args) >= 2 else "friend"
    if rel_type not in ("friend", "admin", "team"):
        return (
            f"Invalid type {rel_type!r}. Use one of: friend, admin, team."
        )

    resource = args[2] if len(args) >= 3 else None
    if rel_type == "team" and not resource:
        return (
            "/aap friend <peer> team <resource> — team proposals need a "
            "resource label (e.g. github.com/acme/widgets)."
        )

    from .tools import aap_propose_relationship_handler
    result = await aap_propose_relationship_handler(
        client, identity, peer,
        relationship_type=rel_type, resource=resource,
    )
    if result.get("status") == "pending_approval":
        label = rel_type
        if resource:
            label += f"({resource})"
        return (
            f"{label.capitalize()} proposal sent to {peer} "
            f"(nonce {result['nonce']}). Waiting for them to accept — "
            f"you'll see the acceptance here."
        )
    return f"Could not send proposal: {result.get('detail', result)}"


async def _revoke_friendship_cmd(args, client, identity) -> str:
    """``/aap unfriend <peer-address>`` — revoke ALL relationships
    (friend, admin, team) we hold with this peer. Sends a signed revoke
    envelope per type and clears the local records.
    """
    if len(args) < 1:
        return "Usage: /aap unfriend <peer-address>"
    try:
        peer = parse_user_address(args[0])
    except ValueError as e:
        return f"Invalid address: {e}"

    from aap.relationships import build_relationship_revoke_envelope
    store = _get_stores().relationships
    records = store.all_for_peer(peer)
    if not records:
        return f"No relationships with {peer} to revoke."

    revoked = []
    for r in records:
        env = build_relationship_revoke_envelope(
            seed=identity.private_seed,
            sender_address=identity.address,
            relationship_type=r.relationship_type,
            resource=r.resource,
        )
        try:
            await client.send_envelope_raw(to=peer, envelope_json=env.to_json())
        except Exception:
            logger.exception(
                "revoke envelope send failed for %s/%s (continuing with local removal)",
                peer, r.relationship_type,
            )
        store.revoke(
            self_address=identity.address,
            peer_address=peer,
            revoke_envelope_json=env.to_json(),
            revoker_public_key=identity.public_key,
        )
        label = r.relationship_type
        if r.resource:
            label += f"({r.resource})"
        revoked.append(label)

    return (
        f"Revoked {', '.join(revoked)} with {peer}. "
        f"Local records removed; revocation envelopes sent."
    )


def _list_friends_cmd() -> str:
    """``/aap friends`` — list current relationships (friend/admin/team)."""
    store = _get_stores().relationships
    rows = store.list_all()
    if not rows:
        return "No relationships established."
    lines = [f"Relationships ({len(rows)}):"]
    for r in rows:
        suffix = f" [resource: {r.resource}]" if r.resource else ""
        lines.append(
            f"  • {r.relationship_type}: {r.peer_address}{suffix}"
            f" (since {r.established_at})"
        )
    return "\n".join(lines)


async def _friend_accept_cmd(nonce, client, identity):
    """Resolve a pending inbound friendship proposal by sending RelationshipAccept
    and adding the local RelationshipRecord."""
    from aap.relationships import build_relationship_accept_envelope
    from aap.envelope import Envelope as _Envelope
    from aap.payloads import AgentCard as _AgentCard

    _stores_fa = _get_stores()
    pending = _stores_fa.pending_proposals
    row = pending.take_inbound(nonce)
    if row is None:
        return f"No pending inbound proposal for nonce {nonce!r}"

    # Build a signed self-AgentCard envelope to attach to the accept.
    from aap.keys import encode_b64url as _enc
    from datetime import datetime as _dt, timezone as _tz
    card = _AgentCard(
        address=identity.address,
        did=f"did:web:{identity.address.split('^', 1)[1]}#agent",
        public_key=_enc(identity.public_key),
        endpoints=[{"type": "didcomm", "uri": client.relay_url}],
        kind="personal",
    )
    card_env = _Envelope(
        type="aap.envelope/v1",
        payload_type=_AgentCard.PAYLOAD_TYPE,
        payload=card.to_dict(),
        iss=identity.address,
        iat=_dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ).sign(identity.private_seed)

    accept_env = build_relationship_accept_envelope(
        seed=identity.private_seed,
        sender_address=identity.address,
        proposal_nonce=nonce,
        accepter_card_envelope_json=card_env.to_json(),
    )
    try:
        await client.send_envelope_raw(
            to=row.proposer_address, envelope_json=accept_env.to_json(),
        )
    except Exception as e:
        return f"Send failed: {e}"

    try:
        proposer_public_key = await client.resolve_peer(row.proposer_address)
        _stores_fa.relationships.establish(
            self_address=identity.address,
            peer_address=row.proposer_address,
            proposal_envelope_json=row.proposal_envelope_json,
            accept_envelope_json=accept_env.to_json(),
            proposer_public_key=proposer_public_key,
            accepter_public_key=identity.public_key,
        )
    except Exception as e:
        return f"Accept failed: could not verify/store relationship: {e}"
    return f"✅ Accepted {row.relationship_type} with {row.proposer_address}"


async def _friend_decline_cmd(nonce, client, identity):
    """Resolve a pending inbound friendship proposal by sending RelationshipDecline."""
    from aap.relationships import build_relationship_decline_envelope

    pending = _get_stores().pending_proposals
    row = pending.take_inbound(nonce)
    if row is None:
        return f"No pending inbound proposal for nonce {nonce!r}"

    env = build_relationship_decline_envelope(
        seed=identity.private_seed,
        sender_address=identity.address,
        proposal_nonce=nonce,
    )
    try:
        await client.send_envelope_raw(
            to=row.proposer_address, envelope_json=env.to_json(),
        )
    except Exception as e:
        return f"Send failed: {e}"
    return f"\U0001F6AB Declined {row.relationship_type} from {row.proposer_address}"


# ---------------------------------------------------------------------------
# /aap rotate-key — recovery flow
# ---------------------------------------------------------------------------

_ROTATE_PENDING_FILENAME = "aap-rotate-pending.json"


def _rotate_pending_path():
    import os
    from pathlib import Path
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    return home / _ROTATE_PENDING_FILENAME


async def _rotate_key_start(current_address: str, email: str) -> str:
    """Generate a new keypair, request an OTP from the verifier, and persist
    pending state so /aap rotate-key confirm can finish the flow."""
    import json as _json
    import os

    import httpx
    import secrets

    from aap.address import Address
    from aap.envelope import Envelope
    from aap.envelope_policy import verify_envelope
    from aap.keys import encode_b64url, generate_keypair
    from aap.payloads import VerifyStartResponse
    from aap.verifiers import verifier_relay_address

    try:
        parsed = Address.parse(current_address)
    except ValueError as e:
        return f"Could not parse current address: {e}"

    # Prefer the explicitly-configured relay + verifier URLs. Fall back to
    # the by-convention defaults under the address's domain (verify.<domain>,
    # api.<domain>) only if the env vars are unset — typically for
    # self-hosted operators who haven't completed env configuration.
    relay_url = os.getenv("AAP_RELAY_URL", f"https://api.{parsed.domain}")
    verifier_url = os.getenv("AAP_VERIFIER_URL", f"https://verify.{parsed.domain}")
    stores = _get_stores()
    verifier_domain = _domain_from_url(verifier_url)
    verifier_public_key = await _resolve_verifier_public_key(stores, verifier_domain)
    if verifier_public_key is None:
        return (
            f"Cannot rotate key: verifier {verifier_domain} is not in the "
            "signed trust list or has no valid public key."
        )

    new_seed, new_public = generate_keypair()
    new_public_b64 = encode_b64url(new_public)

    from datetime import datetime, timezone
    iat = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    request_nonce = secrets.token_urlsafe(12)

    start_env = Envelope(
        type="aap.envelope/v1",
        payload_type="aap.verify-email-start/v1",
        payload={
            "email": email,
            "subject_address": current_address,
            "public_key": new_public_b64,
            "nonce": request_nonce,
        },
        iss=current_address,
        iat=iat,
    ).sign(new_seed)

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                f"{verifier_url.rstrip('/')}/aap/verify/email/start",
                content=start_env.to_json(),
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as e:
        return f"Network error reaching verifier: {e}"
    if resp.status_code != 200:
        return f"Verifier rejected start: HTTP {resp.status_code} {resp.text[:300]}"
    try:
        response_env = Envelope.from_json(resp.text)
        if response_env.payload_type != VerifyStartResponse.PAYLOAD_TYPE:
            return "Verifier start response used an unexpected payload type."
        if response_env.iss != verifier_relay_address(verifier_domain):
            return "Verifier start response came from an unexpected issuer."
        verify_envelope(response_env, verifier_public_key)
        start_response = VerifyStartResponse.from_dict(response_env.payload)
    except Exception as e:
        return f"Verifier returned an invalid signed start response: {e}"
    if start_response.request_nonce != request_nonce:
        return "Verifier start response nonce mismatch."
    if not start_response.otp_id:
        return "Verifier response missing otp_id"

    pending = {
        "address": current_address,
        "email": email,
        "otp_id": start_response.otp_id,
        "new_seed_b64": encode_b64url(new_seed),
        "new_public_b64": new_public_b64,
        "relay_url": relay_url,
        "verifier_url": verifier_url,
        "verifier_domain": verifier_domain,
    }
    path = _rotate_pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_json.dumps(pending))
    tmp.chmod(0o600)
    tmp.replace(path)
    return (
        f"Verification email sent to {email}. "
        f"When the code arrives, run:\n"
        f"  /aap rotate-key confirm <code>"
    )


async def _rotate_key_confirm(token: str) -> str:
    """Read pending state, confirm OTP with verifier, submit rotate-key
    envelope, and on success replace the local identity file."""
    import json as _json
    from pathlib import Path
    from datetime import datetime, timezone

    import httpx

    from aap.envelope import Envelope
    from aap.encryption import generate_encryption_keypair
    from aap.keys import decode_b64url, encode_b64url
    from aap.payloads import AgentCard
    from aap.verifier_client import VerifierClientError, confirm_email_verification

    path = _rotate_pending_path()
    if not path.exists():
        return "No pending rotation. Run /aap rotate-key start <email> first."
    pending = _json.loads(path.read_text())
    address = pending["address"]
    otp_id = pending["otp_id"]
    seed = decode_b64url(pending["new_seed_b64"])
    new_public_b64 = pending["new_public_b64"]
    relay_url = pending["relay_url"]
    verifier_url = pending["verifier_url"]
    verifier_domain = pending.get("verifier_domain") or _domain_from_url(verifier_url)
    verifier_public_key = await _resolve_verifier_public_key(_get_stores(), verifier_domain)
    if verifier_public_key is None:
        return (
            f"Cannot rotate key: verifier {verifier_domain} is not in the "
            "signed trust list or has no valid public key."
        )

    try:
        attestation_json = await confirm_email_verification(
            seed=seed,
            subject_address=address,
            otp_id=otp_id,
            token=token,
            verification_endpoint=f"{verifier_url.rstrip('/')}/aap/verify",
            verifier_domain=verifier_domain,
            verifier_public_key=verifier_public_key,
        )
    except VerifierClientError as e:
        return f"Confirmation failed: {e}"
    attestation_dict = _json.loads(attestation_json)

    # Extract localpart from the address.
    from aap.address import Address
    parsed = Address.parse(address)
    localpart = parsed.localpart
    domain = parsed.domain

    iat = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    encryption_private, encryption_public = generate_encryption_keypair()
    agent_card = AgentCard(
        address=address,
        did=f"did:web:{domain}#agent",
        public_key=new_public_b64,
        encryption_key=encode_b64url(encryption_public),
        endpoints=[{"type": "didcomm", "uri": relay_url.rstrip("/")}],
        kind="personal",
    )
    agent_card_env = Envelope(
        type="aap.envelope/v1",
        payload_type=AgentCard.PAYLOAD_TYPE,
        payload=agent_card.to_dict(),
        iss=address,
        iat=iat,
    ).sign(seed)
    rotate_env = Envelope(
        type="aap.envelope/v1",
        payload_type="aap.address-rotate/v1",
        payload={
            "localpart": localpart,
            "new_public_key": new_public_b64,
            "email_attestation": attestation_dict,
            "agent_card_envelope": agent_card_env.to_dict(),
        },
        iss=address,
        iat=iat,
    ).sign(seed)

    try:
        r = httpx.post(
            f"{relay_url.rstrip('/')}/aap/addresses/rotate-key",
            content=rotate_env.to_json(),
            headers={"Content-Type": "application/json"},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return f"Rotate-key network error: {e}"
    if r.status_code != 200:
        return f"Rotate-key rejected: HTTP {r.status_code} {r.text[:300]}"

    # Replace local identity file with the new keypair.
    import os
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    identity_path = home / "aap.json"
    tmp = identity_path.with_suffix(identity_path.suffix + ".tmp")
    tmp.write_text(_json.dumps({
        "private_seed_b64": encode_b64url(seed),
        "public_key_b64": new_public_b64,
        "encryption_private_key_b64": encode_b64url(encryption_private),
        "encryption_public_key_b64": encode_b64url(encryption_public),
        "address": address,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    tmp.chmod(0o600)
    tmp.replace(identity_path)

    # Wipe the pending state.
    path.unlink()
    return (
        f"✅ Rotated key for {address}. "
        f"Restart your gateway so peers re-resolve via /.well-known/aap-resolve."
    )
