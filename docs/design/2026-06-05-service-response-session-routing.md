# Service-response session routing — design spec

**Date:** 2026-06-05
**Status:** Proposed (revised after codex/gpt-5.5 review)
**Reporter:** caught during scenario 2 live test (aap-business-example on Fly)

## TL;DR

Inbound `aap.service-response/v1` envelopes that arrive in the fully-async
(fire-and-forget) path spin up a brand-new LLM session keyed by the
**business agent's address**, not the user/group session that issued the
original `aap_send_service_request`. The new session has zero history of
the originating turn, so it reasons from scratch about what the inbound
response means and contradicts the originating session.

The fix is to persist the originating `SessionSource` (plus the original
target address, for an impersonation check) per `request_nonce` when
`aap_send_service_request` runs, and route the async response back into
that session.

The naive version of this fix has three sharp edges that the design must
address explicitly: the lookup must atomically validate `iss` *before*
consuming the record (so a forged response can't snipe it), the AAP
adapter's post-turn auto-reply must not deliver the LLM's final text to
the responding business when the origin is a user/group session, and the
session-source ContextVar must be populated for non-AAP-originated turns
too (Telegram, home channel) since service requests can be issued from
those turns.

## Symptom

Live scenario 2 trace, 2026-06-05, hermes9 ↔ `bookings@dinetable-test.fly.dev`:

| time (UTC) | session | event |
|---|---|---|
| 11:13:46 | `d412e4c9` (group session for `Group Dinner`) | `aap_send_service_request` for `book-table` (no attestation yet) |
| 11:22:48 | `d412e4c9` | `aap_verify_confirm` → fresh phone attestation stored |
| 11:22:55 | `d412e4c9` | `aap_send_service_request` again, this time with attestation attached |
| 11:22:55 | `d412e4c9` | tool result: `sent:true`. LLM emits *"Phone verified ✅ and booking request resent with the verified attestation attached. Confirmation will arrive shortly."* |
| 11:22:53 | (inbound) | dinetable replies: `denied`, `denial_reason: verification_required` (the poller had a parser bug — separate fix, in aap-business-example) |
| 11:23:00 | **`f04bb1af` — brand-new session, history=0** | sees the inbound, has zero session history of the verification, tells Ian: *"USER REQUIRED: booking denied, please verify your phone."* |
| 11:23:02 | `f04bb1af` | broadcasts the same denial to the group |
| 11:23:31 | `d412e4c9` | Ian: "you have my attestation, please retry" |
| 11:23:40 | `d412e4c9` | *"retry was already sent with attestation, no further action needed"* — true from `d412e4c9`'s POV, contradicts what `f04bb1af` just told Ian on the same home channel |

Two separate sessions emitted contradictory messages about the same booking
within 40 seconds. From Ian's POV, the agent looked confused — saying
"denied, please verify" and "already sent, just waiting" alternately.

Note: the current `_handle_service_response` *does* know about the group
mapping — it injects a `GROUP UPDATE REQUIRED` prompt fragment so the new
session knows to call `aap_group_send`. The problem isn't "the agent has
no idea this was a group booking." It's that the new session has no
**conversational history** — no record of the prior tool calls, the prior
assistant text, or the user's verification — so it can't reason about
what's already been said or done.

## Root cause

`adapter.py:_handle_service_response` (function starts at line 1451, the
problematic dispatch block is lines 1505–1547, and the post-turn delivery
block is lines 1560–1580):

```python
# Look up whether this request was made on behalf of a group.
group_conv_id = self.stores.service_request_groups.pop(resp.request_nonce)
# ... build summary, inject GROUP UPDATE REQUIRED prompt fragment ...

event = MessageEvent(
    text=trust_preamble + group_context_note + summary,
    source=SessionSource(
        platform=Platform("aap"),
        chat_id=sender,         # ← business agent's address
        chat_type="dm",         # ← always dm, even when origin was a group
        user_id=sender,
        user_name=sender,
        thread_id=None,
    ),
    ...
)
response = await self._message_handler(event)
# ... and then post-turn: send(chat_id=sender, content=reply_text)
```

Two layered problems:

1. **`source.chat_id` is the business address**, so the gateway session
   manager creates/looks up a session keyed by
   `(aap, business-address, dm)`. That session isn't the one in which
   `aap_send_service_request` was called.
2. **After the LLM turn returns, the AAP adapter sends the final assistant
   text back to `sender`** (the business) — line 1576 — unless the LLM
   used a tool or emitted `[NO_REPLY]`. The group-inbound path (line 1000)
   has explicit "drop final text — make the LLM call `aap_group_send` or
   `aap_send_message`" handling; the service-response path doesn't. So
   even after we fix routing, the LLM's text reply meant for the user
   could be auto-shipped to the business agent.

The `service_request_groups` store
(`aap.stores.service_request_groups.ServiceRequestGroupIndex`, populated
at `tools.py:604`) knows which group the request was for, but it's used
only to build the prompt fragment — never to route the event.

## Why this matters beyond service responses

Any async-reply pattern in AAP will hit the same routing issue:

- `aap.relationship-proposal-response/v1`
- Any future `pending`-status flow where the business agent calls back
  later with a result

`service-followup` arguably needs similar treatment, but its data flow is
different (issued grants store the business as `counterparty`, not the
originating customer session) and there's no real grant-issuance path
in the codebase today — only tests call `record_issued`. Defer until that
codepath exists and a separate policy decision (auto-react vs.
user-confirm) is made.

## Proposed fix

### Step 1 — New origin store, lives in aap-hermes

Create `aap_hermes/stores/service_request_origins.py` (the exact module
home can be wherever this project keeps non-SDK persistence — `_runtime.py`
already knows about the other stores). Do **not** put this in aap-python:
the record holds `SessionSource`, which is a Hermes/gateway type, not a
protocol type. The existing `aap.stores.service_request_groups` should
be deleted from aap-python in a follow-up release once nothing imports
it.

Record shape — explicit fields, not blind `asdict(SessionSource)`:

```python
@dataclass(frozen=True)
class RequestOrigin:
    # Originating-session fields (normalize Platform → str at write time;
    # blind dataclass-to-dict would leak the Platform object's shape).
    platform: str
    chat_id: str
    chat_type: str
    user_id: str | None
    user_name: str | None
    thread_id: str | None
    chat_name: str | None
    # Routing/security
    target_address: str           # the AAP peer we sent the request to
    group_conversation_id: str | None  # convenience for group_context_note
    # Lifecycle
    created_at: str               # ISO8601 UTC, for TTL pruning
```

Reconstruct `SessionSource(platform=Platform(origin.platform), …)`
explicitly at dispatch time.

Store implementation requirements (the existing
`ServiceRequestGroupIndex` falls short on all of these — don't inherit
its bad behavior):

- **Atomic write**: write to `<path>.tmp` and `os.replace`. The current
  store writes directly to the final path (`service_request_groups.py:36`)
  and corrupts on partial write.
- **Loud on corruption**: log at WARNING (or higher) when the file is
  unreadable. The current store silently returns `{}` and continues
  (`service_request_groups.py:29`), which would mask data loss for this
  use case.
- **TTL pruning**: drop records older than N days (suggested: 7) on every
  load. The store grows monotonically otherwise — async services that
  never respond would leak entries.
- **Reject duplicate nonce on `record`**: log WARNING and refuse (or
  overwrite only if `created_at` is newer and `target_address` matches).
  The current store silently overwrites
  (`service_request_groups.py:44`); a buggy caller that re-uses a nonce
  would silently cross-route a future response into the wrong session.
- **Atomic conditional pop** — see Step 3.
- Concurrency: still single-writer assumed. If we move to multi-process
  or threaded writers, swap to sqlite. Note it; don't fix it here.

### Step 2 — Capture origin at request time, from ANY platform

In `tools.py:aap_send_service_request_handler`, after the send succeeds:

```python
source = current_turn_session_source()      # see note below
origin = RequestOrigin.from_session_source(
    source,
    target_address=business_address,
    group_conversation_id=group_conv_id,    # existing computation
)
_stores.service_request_origins.record(nonce, origin)
```

`current_turn_session_source()` must work for AAP, Telegram, and home-channel
originated turns. `turn_context.py` today has AAP-specific ContextVars
that are explicitly None on Telegram-originated turns (`turn_context.py:15`).
That isn't enough — `aap_send_service_request` can be called from any
platform's turn (per `tools.py:598`, the tool already takes
`group_conversation_id` explicitly to support Telegram-originated
bookings).

The right hook is gateway-side: `BasePlatformAdapter` (or its dispatch
loop) should set a session-source ContextVar around `_message_handler`
for *every* platform turn. Hermes may already expose an API for "current
turn's SessionSource" via its session manager — verify before
implementation. If no such API exists:

- short-term: add an `aap_hermes`-side ContextVar
  (`set_current_session_source` / `get_current_session_source`) and have
  the AAP adapter populate it at dispatch (lines 988 and 1554). Other
  adapters' tool calls into `aap_send_service_request` would see `None`,
  and we'd fall back to the legacy ContextVar pair plus the explicit
  `group_conversation_id` arg.
- medium-term: push the ContextVar set/reset into the gateway base class
  so all platforms benefit. Track as a Hermes-side follow-up.

If `current_turn_session_source()` returns `None`, log WARNING and skip
recording. The send still goes out; the response will land in the legacy
fresh-session path. Better than crashing; surfaces the gap in logs so we
catch missed coverage.

### Step 3 — Use origin when dispatching the response

Atomic check-then-pop:

```python
origin = self.stores.service_request_origins.pop_if(
    nonce=resp.request_nonce,
    expected_iss=envelope.iss,
)
if origin is None:
    # Either (a) no record for this nonce, or (b) record exists but iss
    # doesn't match the original target_address. We don't tell those
    # apart — both are dropped so an attacker can't probe.
    logger.warning(
        "Dropping service_response from %s for nonce %s "
        "(no matching origin or iss mismatch)",
        envelope.iss, resp.request_nonce,
    )
    return
```

The `pop_if(nonce, expected_iss)` contract: returns the record AND
deletes it iff a record exists *and* its `target_address == expected_iss`;
otherwise returns None and **leaves the record in place**. This closes
the sniping vector: a peer that learns a nonce can't race a legitimate
response with a forged one, because the forged response's `iss` won't
match and the record stays available for the real reply.

Then dispatch with the recorded source AND change the post-turn auto-reply
behavior so the LLM's final text doesn't get shipped to the business:

```python
source = origin.to_session_source()
event = MessageEvent(
    text=trust_preamble + group_context_note + summary,
    source=source,
    message_id=str(resp.request_nonce),
    timestamp=_parse_iat(envelope.iat),
)

# Pre-flight: if Hermes can tell us the originating session no longer
# exists (e.g. /aap clear_conversation ran), mirror to the user's home
# channel and skip the LLM dispatch — don't resurrect a deleted session.
# If Hermes doesn't expose a "does this session exist" check, dispatch
# anyway and accept that the gateway creates a fresh (history-less)
# session for the cleared origin until that hook lands.
if self._session_exists is not None and not self._session_exists(source):
    mirror_to_home_channels(
        sender=envelope.iss,
        recipient=None,
        text=summary + "\n(original conversation was cleared)",
        direction="inbound",
    )
    return

response = await self._message_handler(event)

# Service-response routing post-turn: do NOT auto-deliver the LLM's
# final text to `sender` (the business). The originating session is a
# user/group session; the LLM should call send_message / aap_group_send
# explicitly if it wants to reach the human. Mirror the group-inbound
# drop-final-text policy at adapter.py:1000.
reply_text = response if isinstance(response, str) else ""
if reply_text.strip() and not _is_no_reply(reply_text):
    logger.info(
        "service_response: dropping non-tool final text from session "
        "%s (LLM should use send_message/aap_group_send explicitly): %s",
        source.chat_id, reply_text[:120],
    )
```

The `already_sent_to(sender)` branch (current line 1569) doesn't apply
here because `sender` is the business and the LLM should never be
intentionally replying *to the business* on the service-response path —
it's replying to the human. If a future use case wants the LLM to
auto-reply to the business (e.g., "thanks, see you Friday"), it should
go through an explicit `aap_send_message` tool call, not a final-text
fallthrough.

The issuer check also closes an impersonation gap that exists today on
the synchronous `pending_responses.resolve` path
(`adapter.py:1473`). Fix in the same PR: extend `PendingResponses.register`
to take `expected_target_address`, and refuse to resolve a nonce whose
`envelope.iss` doesn't match. Today `register`/`resolve` carry no issuer
metadata at all (`pending_responses.py:33`, `pending_responses.py:42`),
so a peer that learns a nonce can satisfy a tool-call's future with
arbitrary payload. Same vector as the async path; same fix.

## Alternatives considered

1. **Prompt the LLM to "check pending requests" before reasoning about
   any inbound.** Fragile — relies on the LLM to introspect, and the
   inbound session still doesn't have access to the originating
   session's history. Doesn't solve the root cause.

2. **Make `aap_send_service_request` synchronous (await the future) so
   the response is delivered back to the same tool call.** This is what
   `pending_responses` was for. Problem: services can take minutes
   (think: "the chef is checking the calendar"). Holding an LLM turn
   open for that long is a bad UX (tool turn budget, no chance for the
   user to follow up, no opportunity for the LLM to do anything else
   meanwhile). The async path is the right default.

3. **Single agent-wide session.** Loses isolation between concurrent
   conversations. Not workable for an agent that's in multiple groups
   plus DMs.

4. **Inject the originating session's full history into the new session.**
   Doable but heavyweight: requires either copying messages or running
   the LLM with two histories. Per-nonce routing is simpler and reuses
   the gateway's existing session machinery.

5. **Heuristic: route into "the most recent session that touched this
   business address."** Loses with concurrent groups/users, and the send
   path doesn't actually mirror into any peer session today
   (`tools.py:592` only records outbound contact + optional group id).
   Per-nonce origin is strictly simpler and correct.

## Edge cases and open questions

- **Rehydration of evicted sessions.** *Assumes* Hermes lazily rehydrates
  evicted sessions from `state.db` when handed a known `SessionSource`
  key. The repo shows session persistence at `commands.py:215` and key
  derivation at `commands.py:422`, but doesn't expose gateway-internal
  rehydration. Verify before implementation; if Hermes doesn't rehydrate
  the way we assume, a stale-origin response will resurrect an empty
  session and the contradiction problem recurs.
- **Deleted originating session.** `/aap clear_conversation`
  (`commands.py:459`) wipes the session index and SQLite rows. The
  origin record in `service_request_origins` survives. Policy: pre-flight
  check via Hermes session-existence API and mirror-to-home if cleared
  (preferred). If no such API exists, dispatch and accept the empty
  resurrection; revisit when Hermes exposes the hook.
- **Concurrent requests from the same session.** Each nonce gets its own
  origin record; same session lookups, no issue.
- **Duplicate nonce on `record`.** Logged WARNING and refused (Step 1).
  Should never happen in practice; if it does, it's a bug we want to
  surface, not silently cross-route a future response.
- **Multiple recipients (broadcast service requests).** Not currently a
  thing, but if it becomes one, the origin index would need one row per
  (nonce, target).
- **Unsolicited / forged service-responses.** Dropped with WARNING via
  `pop_if` failing the iss match (Step 3). The store record for the real
  nonce stays put so the legitimate response can still land — that's
  the whole point of `pop_if` vs. `pop`-then-validate.
- **Re-entrant nonces.** A routed response delivered to the originating
  session may cause the LLM to issue a new `aap_send_service_request`
  ("retry with X"). That call generates a fresh nonce and records its
  own origin — no overwrite. No retry/turn budget added here; turn
  budgeting is a separate concern.
- **TTL expiry vs. slow services.** With a 7-day prune, a business
  that replies on day 8 lands in the legacy fresh-session fallback. That
  fallback should also be policy: probably "drop with WARNING" given the
  whole point of this fix is to remove the sender-keyed fresh session.
  Trade-off is acceptable — 7+ day async replies are pathological.
- **Non-AAP origin without ContextVar set.** Step 2's
  `current_turn_session_source()` returns None → log WARNING, skip
  record, send still goes out. Response will fail the `pop_if` lookup
  on the way back in and get dropped. Surfaces in logs as a gap in
  cross-platform coverage.

## Test plan sketch

1. **Unit:** `ServiceRequestOriginIndex.record(nonce, origin)` /
   `pop_if(nonce, expected_iss)` round-trip including field fidelity
   (especially `platform`); `pop_if` with mismatched iss returns None
   AND leaves the record in place; `pop_if` with unknown nonce returns
   None. Atomic write survives a torn-write test (truncate the .tmp
   file mid-test, verify final file is either old-good or new-good).
   Corruption is logged at WARNING. TTL prune drops records past
   threshold.
2. **Unit:** `tools.aap_send_service_request_handler` records an origin
   whose normalized fields match the current turn `SessionSource` and
   whose `target_address` matches the business address it sent to.
   Telegram-originated turn: when `current_turn_session_source()`
   returns a Telegram source, the record is correct; when it returns
   `None`, the handler logs WARNING and still completes the send.
3. **Adapter integration:** when a `ServiceResponse` envelope arrives
   for a known nonce with matching `iss`, `_handle_service_response`
   dispatches a `MessageEvent` whose `source` is the recorded origin.
4. **Adapter integration (sniping race):** the store has a recorded
   origin for nonce N with `target_address=A`. An envelope from `B`
   with nonce N arrives first → `pop_if` returns None, WARNING logged,
   record still present. Then envelope from `A` with nonce N arrives →
   dispatched correctly.
5. **Adapter integration (unknown nonce):** envelope with unknown
   nonce → no dispatch, WARNING logged.
6. **Adapter integration (deleted origin session):** record an origin,
   simulate `/aap clear_conversation` on that session, receive matching
   service response → mirrored to home channel, no LLM dispatch (or, if
   the session-existence hook isn't available, dispatch happens — assert
   whichever behavior the code actually implements).
7. **Adapter integration (post-turn auto-reply suppression):** routed
   service-response turn ends with non-empty final assistant text → text
   is dropped with INFO log; `self.send(chat_id=sender, …)` is NOT
   called.
8. **Pending-response impersonation:** `PendingResponses.register(nonce,
   future, expected_target_address=A)`; a response from `B` with the
   same nonce → future stays unresolved, WARNING logged.
9. **End-to-end scenario 2 regression:** book-table denial dispatched
   into the original group session (verify by inspecting `state.db` —
   the denial appears as a message row in the `Group Dinner` group's
   session, not in a fresh `bookings@…` DM session).

## Files to touch

- `adapter.py` — `_handle_service_response` (rewrite the dispatch block
  + the post-turn try/finally to suppress auto-reply to sender);
  `_StoreBundle` dataclass (point at the new origin store);
  `_handle_service_response` and the symmetric pending path: pass
  `expected_target_address` when registering pending responses.
- `tools.py` — `aap_send_service_request_handler` (capture origin via
  cross-platform session-source helper).
- `turn_context.py` — short-term addition of
  `set_current_session_source` / `get_current_session_source` if no
  gateway-side hook exists; medium-term, deprecate in favor of a gateway
  ContextVar.
- New module — `service_request_origins.py` in aap-hermes (location
  per the existing store-import pattern in `_runtime.py`).
- `aap` SDK — no changes required for this fix. Follow-up PR: delete
  `aap.stores.service_request_groups` once no importer remains;
  optionally extend `PendingResponses.register` to carry
  `expected_target_address` if/when we want that API to live in the SDK
  rather than be wrapped on the aap-hermes side.
- `tests/test_service_response.py` — new file; covers routing,
  sniping race, unknown nonce, deleted-origin-session, post-turn
  suppression, pending-response impersonation.
- `tests/test_v06_tools.py` — origin-capture assertions including the
  Telegram-origin case.

## Related issues

- **`aap-business-example/lib/services.js` attestation-parsing bug**
  (fixed today, commit pending): the dinetable poller was treating
  `verification_attestations` entries as objects instead of JSON
  strings, so the second book-table call at 11:22:55 was denied even
  though the attestation was correctly attached. Without that bug, the
  session-routing flaw documented here would have been harder to
  trigger in this run — but it would still surface for any service
  that returns `pending` first and `confirmed` later.
- **No client-side service-request timeout** — if a service never
  replies, the originating session has no signal to escalate to the
  user. Separate from this fix; the TTL prune (Step 1) bounds the
  origin record's lifetime but doesn't itself notify anyone.
- **`pending_responses.resolve` lacks an issuer check** — same
  impersonation gap as the async path. Fixed in the same PR per Step 3.
- **Gateway-side cross-platform session-source ContextVar** — the
  cleanest home for the helper Step 2 needs. Filed as a Hermes-side
  follow-up; this spec ships with the short-term aap-hermes-local hook
  and degrades gracefully when other platforms call
  `aap_send_service_request`.
- **`service-followup` doesn't dispatch a turn today and has no real
  grant-issuance codepath**. When both change, the same per-nonce origin
  pattern applies — but the data flow needs reworking (issued grants
  store `counterparty=business_address`, not the customer session
  source). Defer.
