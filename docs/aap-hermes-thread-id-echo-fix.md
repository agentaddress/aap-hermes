# aap-hermes: echo `thread_id` on outbound 1:1 chat replies

**For:** aap-hermes maintainers
**From:** aap-claude (Claude Code edition AAP agent)
**Status:** requesting a fix in aap-hermes / the Hermes gateway
**Date:** 2026-07-16

## One-line ask

When Hermes replies to a 1:1 AAP chat, the outbound reply envelope must carry the
**same `thread_id`** that was on the inbound message. Today it goes out with
`thread_id=None`, which breaks reply-routing for peers that route by `thread_id`
(as the AAP protocol specifies).

## Why this matters

aap-claude has a feature that routes a peer's reply back into the **specific live
Claude Code chat that started the conversation**. It relies on the AAP protocol's
documented `thread_id` contract. From `aap-python` `src/aap/messages.py:38`
(`build_chat_envelope` docstring):

> `thread_id`, when set, identifies a conversation thread within the sender↔recipient
> channel. **Receivers route by thread_id when present**; absent = the default thread
> per peer.

So the round-trip is: aap-claude sends a 1:1 chat with `thread_id=T` → the peer
(Hermes) replies **with the same `thread_id=T`** → aap-claude matches `T` to the
originating chat and delivers the reply there. If the reply omits `thread_id`, the
match fails and aap-claude falls back to a generic notification instead of threading
the reply. (`conversation_id` is deliberately NOT used for 1:1 — it marks a *group*
conversation in this stack, and Hermes correctly drops 1:1 chats that carry an
unknown group id.)

## Observed behavior (evidence)

Test on 2026-07-16: aap-claude sent Hermes (`chris^agentaddress.org`) a 1:1 chat
`"What's for dinner?"` with `thread_id=eb933f79-7160-4ebd-8c40-ac4995d899e0` and no
`conversation_id`.

1. **Send accepted (good).** `~/.hermes/logs/gateway.log`:
   ```
   14:04:14 gateway.run: inbound message: platform=aap user=chris+claude^agentaddress.org ...
   ```
   (No "Dropping chat envelope ... unknown conversation_id" — the fix to send `thread_id`
   instead of `conversation_id` resolved the earlier drop.)

2. **Reply came back with `thread_id=None` (the bug).** The reply ("chicken nuggets")
   arrived at aap-claude's inbox as:
   ```
   sender=chris^agentaddress.org  thread_id=None  conversation_id=None  text="chicken nuggets"
   ```
   With no `thread_id`, aap-claude cannot route it to the originating chat, so it fell
   back to the Telegram/desktop path.

## Root cause

The inbound `thread_id` is captured, and it *is* carried on the per-turn
`SessionSource` — but the two code paths that actually put a chat reply on the wire
never read it back out and pass it to `send_envelope(...)`.

- **Inbound captures it:** `adapter.py:1156` unwraps `text, thread_id`, and
  `adapter.py:1271` builds `SessionSource(..., thread_id=thread_id)`. That source is
  installed on the `current_session_source` ContextVar for the duration of the turn
  (`adapter.py:1320` inline path, `adapter.py:661` decoupled/reasoning path).
- **The two reply sites dropped it:**
  - post-turn auto-reply → `adapter.send()` → `client.send_envelope(to=chat_id,
    text=content)` (`adapter.py:839`) — no `thread_id`.
  - LLM tool reply → `aap_send_message_handler` → `client.send_envelope(to=to,
    text=text)` (`tools.py:201`) — no `thread_id`.
  `AAPClient.send_envelope(..., thread_id=None, ...)` supports the field; it was
  simply never supplied, so every reply went out `thread_id=None`.

> Note: the earlier draft of this doc pinned the bug on `HERMES_SESSION_THREAD_ID`
> / the `RequestOrigin` read at `tools.py:574`. That path is for the async
> **service-request/response** round-trip, not the 1:1 chat reply, and it already
> reads `thread_id` from the `SessionSource` when present. Setting that env var would
> not have changed the observed behavior.

## Required fix (implemented)

A shared helper `turn_context.reply_thread_id_for(target_address)` returns the current
turn's inbound `thread_id`, but only when the reply goes back to the **same peer** that
opened the turn (guarding against leaking one peer's `thread_id` onto a message to a
third party) and only for **non-group** sessions (groups route by `conversation_id`).

Both reply sites now call it:

- `adapter.send()` (`adapter.py`): `thread_id = reply_thread_id_for(chat_id)`, passed to
  `client.send_envelope(...)`.
- `aap_send_message_handler` (`tools.py`): `thread_id=reply_thread_id_for(to)` on its
  `send_envelope(...)` call.

Groups are unaffected — the helper excludes `aap-group:` sessions, and group auto-reply
is already disabled (`adapter.py:1330`).

## Edge case worth confirming

In our test, Hermes's agent returned `[NO_REPLY]` to the AAP message, and the actual
"chicken nuggets" reply was then produced from a **separate Telegram-prompted turn**,
which has no AAP thread context — so even with the fix, that particular cross-session
path would not carry the thread_id. The fix targets the **natural in-thread reply**
(Hermes auto-replies within the inbound AAP DM session). If you also want
human-prompted-from-another-platform replies to thread correctly, the outbound
`aap_send_message(target=<peer>)` would need to look up the peer's most recent active
inbound `thread_id` when the current session has none. That is a secondary
enhancement; the primary fix (in-thread reply echoes thread_id) is what unblocks the
feature.

## Acceptance criteria

Given an inbound 1:1 AAP chat `{to: hermes, thread_id: T, conversation_id: None}`:

- When Hermes replies **in that AAP session**, the outbound envelope has
  `thread_id == T` and `conversation_id == None`.
- Verifiable at the wire level: the reply envelope's payload contains `"thread_id": T`.
- Group behavior unchanged: an inbound group chat (`conversation_id=G`) still replies
  with `conversation_id=G`.

## How to verify end to end

1. From aap-claude: `aap chat`, then send Hermes a 1:1 message (note the `thread_id`).
2. Have Hermes auto-reply (not via a separate Telegram prompt).
3. Confirm the reply row at aap-claude has `thread_id == <the sent thread_id>`.
   Expected result: the reply injects into the originating Claude chat rather than
   falling back to a notification.

## References

- aap-python: `src/aap/messages.py:38` (thread_id contract), `:49` (conversation_id =
  group marker).
- aap-hermes: `adapter.py:1156` (inbound unwrap), `adapter.py:1221`
  (`is_group = conv_id is not None`), `adapter.py:1271` (SessionSource thread_id),
  `adapter.py:1320` / `:661` (session-source ContextVar installed for the turn).
  Fix sites: `adapter.send()` and `aap_send_message_handler` (`tools.py`), both via
  `turn_context.reply_thread_id_for`. (`tools.py:574` / `HERMES_SESSION_THREAD_ID` is
  the service-request origin path — not involved in the chat reply.)
- aap-claude routing: keys on `route_key = conversation_id (group) else thread_id (1:1)`.
