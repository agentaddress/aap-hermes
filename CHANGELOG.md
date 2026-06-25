# Changelog

## v0.16.2 — 2026-06-25 — trust-root issuer domain

### Changed

- Require `agentaddress==0.10.0`, which moves the trusted-verifiers trust-root
  issuer to `aap-trust-root^agentaddress.org`. Must run against a
  pang-services relay on the same `aap` version; the Ed25519 trust-root key is
  unchanged.

## v0.16.1 — 2026-06-16 — authenticated encrypted routing

### Security

- Vendored `aap` wheel bumped from `0.9.0` → `0.9.1`. Outbound encrypted
  sends now include a signed outer routing wrapper, so pang-services can
  authenticate and meter the sender while message contents remain encrypted
  end-to-end.

## v0.16.0 — 2026-06-12 — api subdomain migration + address syntax break

Two changes ship together: the api-subdomain relay URL migration and a
breaking change to the AAP address syntax (tracking `aap` v0.9.0).

### Changed (breaking — address format)

- **AAP addresses are now `<localpart>^<domain>`** (e.g.
  `alice^example.com`). The legacy `agent:<localpart>@<domain>` form
  is no longer parseable. See `aap-python` v0.9.0 release notes for
  the rationale. **Migration:** any stored peer address, identity
  binding, or scenario log that holds `agent:X@Y` must be rewritten
  to `X^Y` before upgrading. The plugin's setup wizard, prompts,
  config helpers, regex-based message parsers, and DID derivation
  all updated to the new form. The vendored `aap-0.9.0` wheel
  enforces the new parser.

### Changed

- **`AAP_RELAY_URL` default → `https://api.agentaddress.org`**
  (was `https://agentaddress.org`). The pang-services relay HTTPS
  host moved to the `api.` subdomain so the bare domain can serve
  the marketing site.
- `plugin.yaml` `AAP_RELAY_URL` default and `INSTALL.md` documented
  default updated to match.
- Vendored aap-python wheel bumped from `0.8.1` → `0.9.0`, which
  changes `DEFAULT_TRUSTED_VERIFIERS_URL` to
  `https://api.agentaddress.org/.well-known/aap-trusted-verifiers`.
  Without this, Hermes instances would fetch the trusted-verifiers
  list from the bare apex (now Cloudflare Pages) and 404.
- `requirements-dev.txt` reference to the stale `aap-0.7.0` wheel
  also updated to `aap-0.9.0` (was broken — that wheel no longer
  exists in `vendor/`).

### Migration

- Operators who pin `AAP_RELAY_URL` in env don't need to do anything
  (env wins over default). Anyone on the default picks up the new
  value on upgrade.

## v0.15.0 — 2026-06-10 — ingest/reasoning split (opt-in)

### Added

- **`AAP_INGEST_DECOUPLED` feature flag** (default: off). When enabled,
  inbound AAP envelopes write a `user`-role row to the session DB
  synchronously in the dispatch path, then signal a per-session
  reasoning scheduler that fires one debounced LLM turn whose content
  comes from the freshly-persisted conv_history rather than the inbound
  event. Eliminates the cluster-induced row-loss seen in scenario 4
  (peer responses dropped under burst inbounds; see
  `docs/superpowers/plans/2026-06-10-ingest-vs-reason-redesign.md`).
- **`peers_who_have_messaged_session` predicate** — session-scoped
  anti-relay check that replaces the legacy `originating_peer`
  contextvar when running under the decoupled flag. Allows
  `aap_send_message(to=X)` iff X has previously sent into the current
  turn's session.

### Changed

- `aap_send_message_handler` now applies the session-sender predicate as
  an anti-relay fallback when `originating_peer` is unset (i.e. when
  invoked from a scheduler-driven turn).

### Notes

- Legacy code paths (auto-reply, `[NO_REPLY]` suppression, dedup
  contextvar) remain in place behind the `AAP_INGEST_DECOUPLED=off`
  default. They will be removed in a future release after the flag
  defaults to on and the decoupled path proves out in scenario 4 and
  production.

## v0.14.0 — 2026-06-09 — manual identity-file step for pre-rebrand upgrades

### Changed

- **Vendored `aap` SDK bumped to v0.8.0** (host-state cleanup release).
- **Manual `aap.json → aap.json` rename required** before the
  first start on this version for users coming from a pre-rebrand
  install. The SDK no longer auto-renames the legacy identity file
  (see `aap-python` v0.8.0 CHANGELOG, F1). `INSTALL.md` updated with
  the required `mv` step. Fresh installs are unaffected.

## v0.13.1 — 2026-06-03

Domain migration + setup-wizard availability check.

### Changed

- **Default relay is now `agentaddress.org`** (was `agentcallsign.com`).
  `AAP_INSTANCE_DOMAIN` and `AAP_RELAY_URL` defaults updated in
  `config.py` and `plugin.yaml`. Existing users with these env vars
  already set in `~/.hermes/.env` keep their existing values; new
  installs get the new default.
- **Vendored `aap` SDK bumped to v0.7.1** — picks up the same domain
  migration in the SDK's default trusted-verifiers URL.

### Added

- **Setup-wizard availability check.** `hermes gateway setup` now hits
  `GET /aap/addresses/check` on the configured relay after the
  localpart prompt and re-prompts on collision (taken / reserved /
  malformed). The wizard previously committed any localpart silently;
  collisions only surfaced as cryptic TOFU errors at first gateway
  start. If the relay is unreachable, the wizard proceeds without the
  check (gateway start's TOFU still catches collisions).
- Prompt order rearranged: domain / relay URL prompted before
  localpart so the wizard knows which relay to hit for the check.

## v0.13.0 — 2026-06-02

Protocol + state-store layers extracted to the `aap` SDK (v0.7.0+). The
hermes plugin is now a thin host adapter: it constructs the SDK's stores
once in `adapter_factory` (rooted at `HERMES_HOME`), bundles them into an
`AAPAdapterStores` dataclass, and threads them through
`AAPPlatformAdapter`. Other AAP-speaking hosts (e.g. OpenClaw) can now
depend on `aap>=0.7` and inherit the same machinery.

### Changed

- All envelope codecs, address parsing, payload definitions, HTTP clients,
  store classes, and protocol helpers now live in the `aap` Python
  package. See its CHANGELOG for the full surface.
- `AAPPlatformAdapter.__init__` accepts a new `stores: Optional[AAPAdapterStores]`
  keyword arg. When unset, stores are built from `HERMES_HOME` for
  backward-compat with CLI/test contexts.
- The `aap.message.render/v1` opt-in streaming path (behind
  `RENDER_EVENTS`) was removed — `edit_message` now unconditionally
  signals "no edit primitive" and Hermes falls back to atomic send.

## v0.12.0 — 2026-05-29

Service responses now trigger a new LLM turn instead of silently mirroring
to the home channel. When Dinetable (or any business) confirms a booking, the
agent processes the confirmation in the same session context as the original
request, then can notify the user and update group chats with the result.

### Changed

- `adapter._handle_service_response`: on the async path (no waiting tool call),
  now dispatches a `MessageEvent` via `self._message_handler` using
  `_reply_window_trust_note` — same pattern as inbound chat from a recent
  outbound contact. The mirror to home channels still fires first (notification
  arrives before the LLM turn, as a safety net if the turn fails).
- `aap_send_message` and `aap_send_service_request` are now always
  fire-and-forget (removed all synchronous wait / `PENDING_CHAT_REPLIES` /
  `PENDING_RESPONSES` futures). Replies arrive as inbound turns.
- Removed `aap_send_message_and_wait` tool entirely.
- Group messages now show the group name in Telegram notifications.
- Group inbound messages are injected into home platform sessions as assistant
  turns so the human has context when replying to `👤 USER REQUIRED` prompts.

## v0.11.0 — 2026-05-25

First-contact handshake now establishes bidirectional chat in a single
approval instead of two. The auto-fired `capability_request` carries a
reciprocal `offered_grants` for `agentaddress.org/send-message`,
and the grant handler fulfills offered grants by issuing the promised
tokens back to the peer as soon as the peer's grant arrives.

### Added

- New `pending_requests.py` store at `$HERMES_HOME/aap-pending-requests.json` tracking outbound capability_requests by nonce so the grant handler knows what offered_grants we promised.
- `tools._fire_capability_request` now includes `offered_grants=[CapabilityOfferedGrant(scope=DEFAULT_CHAT_SCOPE, ...)]` on every auto-request.
- `adapter._handle_capability_grant` now calls a new `_fulfill_offered_grants` helper that signs RelationshipTokens for each offered scope, wraps them in fresh CapabilityGrant envelopes, sends them to the peer, and records the issued envelope locally.

### Behavior

- After a first-contact handshake (one consent prompt, one `approve` reply on the receiver side), both peers hold tokens for the chat scope and can message each other without a second consent prompt. Previously the reply direction required its own separate handshake.

## v0.10.1 — 2026-05-25

Bare `approve`/`deny` reply was silently ignored — Hermes's `invoke_hook`
calls plugin callbacks synchronously, so the `async def` hook returned a
coroutine that was never awaited and never matched the `isinstance(_,
dict)` skip check. Made `predispatch_consent_check` sync; the
approve/deny envelope send runs as a background task on the gateway's
event loop.

## v0.10.0 — 2026-05-25

Auto-pilot the capability handshake for first-contact chats, and let the
recipient approve with a bare `approve` / `deny` reply.

### Added

- **Sender side:** `aap_send_message` now pre-checks for a held capability token. When none exists for the recipient, the tool auto-fires a `capability_request` (scope `agentaddress.org/send-message`) and queues the chat text in `$HERMES_HOME/aap-pending-sends.json` (TTL 24h, max 5 per peer). Returns `{"status": "pending_approval", "nonce": ...}` so the LLM stops claiming the message went through when it didn't.
- **Sender side:** when the matching `capability_grant` arrives, `_handle_capability_grant` drains the pending queue and sends each message with the new token. Deferred sends are mirrored to home channels prefixed with `(deferred)`.
- **Sender side:** when a `capability_denial` arrives, the pending queue is dropped and a `❌ … N queued messages dropped` notice goes to the home channels.
- **Receiver side:** the home-channel consent prompt now invites the user to reply with a bare `approve` or `deny`. A new `pre_gateway_dispatch` hook intercepts those replies and resolves the most-recent pending capability_request, falling back to `/aap approve <nonce>` / `/aap deny <nonce>` for explicit targeting.
- **Default scope:** the canonical 1:1 chat scope `agentaddress.org/send-message` (already reserved in `BOOTSTRAP_CHAT_SCOPES`) is now exported as `tools.DEFAULT_CHAT_SCOPE`.

## v0.9.1 — 2026-05-21

Bump aap to 0.5.1; verification_required parsing aligned with aap-python.

### Changed

- Vendored aap wheel bumped from 0.5.0 to 0.5.1. Picks up `aap.keys.seed_to_keypair` and `CatalogEntry.verification_required`.
- Local `AsyncCapabilityCatalog.CatalogEntry`'s `verification_required` field now mirrors aap-python's. Behaviour unchanged; the upstream now carries the same field so future versions can re-export rather than duplicate.

## v0.9.0 — 2026-05-22

This release adds host-side support for the v0.5 verification + discovery protocol. Agents can hold signed phone/email attestations from trusted verifiers, attach them selectively to capability requests, and look each other up by identifier through a consent-mediated discovery flow.

### Added

- **`verifiers.py`** — trusted-verifier list fetching (24h on-disk cache), local overrides, verifier-pubkey lookup. Default trust source is `https://agentaddress.org/.well-known/aap-trusted-verifiers` (overridable via `AAP_TRUSTED_VERIFIERS_URL`).
- **`attestations.py`** — local attestation store at `$HERMES_HOME/aap-attestations.json`. Holds signed `VerificationAttestation` envelopes for selective disclosure on outgoing capability requests.
- **`verifier_client.py`** — async HTTPS client for the verifier's `/aap/verify/{sms,email}/{start,confirm}` endpoints. Signs request bodies with the agent's key.
- **`verification_flow.py`** — pending-OTP store + auto-grant of a long-lived discovery-relay chat token to the verifier on successful verification. Token carries scope `agentaddress.org/discovery-introduction` so the verifier can later relay introduction requests.
- **Verification slash commands**: `/aap verify phone <number>`, `/aap verify email <addr>`, `/aap verify confirm <code>`. End-to-end flow: start → user enters code → confirm → attestation stored → chat token granted to verifier (and a `capability_grant` envelope sent so the verifier knows).
- **Auto-attach attestations on outgoing capability requests**: when `/aap request <peer> <scope>` runs, the host fetches the publisher's catalog entry for each scope, looks for `verification_required`, and attaches matching attestations via the new `Envelope.verification_attestations` field. Missing attestation triggers a fail-loud prompt telling the user which verification to do.
- **`discovery.py`** — outbound `query_discovery` posts a signed envelope to a trusted verifier's `discovery_endpoint`; client sends the **plaintext** identifier under TLS (hashing is server-side, since the pepper is a verifier secret). Inbound `render_introduction_prompt` produces a three-flavor consent card: mutual contact (low friction, contact name shown), attested-no-match (moderate friction), unverified searcher (high friction, includes block option). Pending introductions persist at `$HERMES_HOME/aap-pending-introductions.json`.
- **Adapter dispatch** for `aap.discovery-introduction-request/v1` envelopes — verifies the issuer is the verifier-relay address of a trusted verifier, validates the envelope's signature against the verifier's pubkey, verifies the embedded chat token we previously granted, renders the consent card, and persists a pending row.
- **Discovery slash commands**: `/aap discover phone|email <identifier>`, `/aap discover approve <nonce>`, `/aap discover deny <nonce>`, `/aap discover block <searcher-address>`, `/aap discover list`. Approve/deny send a signed `aap.discovery-introduction-response/v1` to the verifier-relay address. Block POSTs `aap.discovery-block-request/v1` to the verifier's `/aap/discover/block` endpoint and auto-resolves any matching pending introductions.
- **Trust-list management commands**: `/aap verifiers list`, `/aap trust-verifier <domain>`, `/aap distrust-verifier <domain>`, `/aap attestations list`.
- **Local CatalogEntry.verification_required**: the in-process `AsyncCapabilityCatalog` parses and surfaces the publisher's verification requirement block so the auto-attach logic can read it without round-tripping through aap-python.

### Dependency

- Bumped vendored `aap` to v0.5.0.

### Spec

- See `docs/specs/2026-05-22-aap-verification-and-discovery-design.md` (Rev 1).

### Scope of this release

- Discovery fan-out to multiple verifiers is sequential (first verifier supporting the identity type wins). Parallel fan-out is a v0.9.1+ optimization.
- No LLM tools yet for verification/discovery — slash commands only. LLM tool surface comes in a follow-up.
- Revocation lists are not consulted; attestations rely on `exp` for expiry.
- Trust-list `--add` overrides infer default endpoint URLs from the domain (`https://<domain>/aap/discover`, etc.). For verifiers with non-standard paths, edit `$HERMES_HOME/aap-trusted-verifiers-overrides.json` directly.

### Backward compatibility

- v0.8 group conversations, capability tokens, and bootstrap flows are unchanged. The new envelope field (`verification_attestations`) is omitted from envelopes that don't need it, so canonical bytes are byte-identical to v0.8 for those.

## v0.8.0 — 2026-05-22

This release adds group-conversation support (up to 10 members per group) on top of v0.7's capability-token model. Tokens stay 1:1; groups are a thread-linkage overlay.

### Added

- **`ConversationStore`** (`conversations.py`): persists active conversations at `$HERMES_HOME/aap-conversations.json`. Tracks id, purpose, members, convener, accepted-at, last-message-at.
- **`group_flow.py`**: envelope builders for `aap.group-invitation/v1`, `aap.group-membership-update/v1`, `aap.group-leave/v1`.
- **Dispatch routing** for the three new group payload types. Group invitations trigger consent cards; updates apply to the local conversation store; leaves remove the leaver from the local member list. Chat envelopes carrying a `conversation_id` are gated by local-membership checks and surfaced to the LLM with a group-context preamble.
- **Bootstrap auto-approval policy** (`bootstrap.py`): when accepting a group invitation with "auto-trust all members", subsequent `capability_request`s from those members for chat-equivalent scopes (`agentaddress.org/group-chat`, `agentaddress.org/send-message`) are auto-approved within a 1-hour grace window. Higher-risk scopes still require user consent.
- **Broadcast send** (`conversations.broadcast_to_conversation`): when sending to a group, the helper sends N-1 individually-addressed envelopes, each with the appropriate capability token per recipient. All carry the same `conversation_id` and `conversation_members`. Failed recipients are logged + reported but do not stop the broadcast.
- **Chat-envelope conversation fields**: `build_chat_envelope` and `client.send_envelope` accept `conversation_id` + `conversation_members`. Both are signed-over (JCS) — set before `.sign()`.
- **New slash commands**: `/aap group start <members...>`, `/aap group accept <nonce>`, `/aap group leave <conv>`, `/aap group add <conv> <member>`, `/aap group remove <conv> <member>`, `/aap group list`, `/aap group send <conv> <text>`.

### Dependency

- Bumped vendored `aap` to v0.4.0.

### Spec

- See `docs/specs/2026-05-22-aap-group-conversations-design.md` (Rev 1).

### Limits

- 10-member cap per group, enforced at the aap-python validation layer. Larger groups require a group-agent pattern (out of scope).

### Scope of this release

- No LLM tools yet for group operations (creating/leaving/etc.) — slash commands only. LLM tool surface comes in a follow-up.
- No conversation-scoped tokens — tokens are durable and apply to all conversations between the same pair.
- No message-ordering guarantees — receivers may see envelopes out of order; `iat` provides soft ordering.

## v0.7.0 — 2026-05-22

This release adopts the AAP Rev 2 permission-identifier model. **Breaking**: scope strings change from `verb:domain/noun` (v0.6) to `domain/permission-name` (v0.7). Peer-trust store is removed entirely — capability tokens are now the only access mechanism.

### Removed

- **Peer-trust store** (`peers.py`): the v0.5 trust gate is fully replaced by capability tokens. Unknown peers can no longer chat — they must send `capability_request` first and obtain a token.
- **Slash commands** `/aap approve <address>`, `/aap block <address>`, `/aap forget <address>`, `/aap peers`: address-based approvals are gone. Capability-nonce-based `/aap approve <nonce>` remains.
- **`AAP_TRUST_DEFAULT` env var**: no longer meaningful.
- **Auto-approve on outgoing `send_envelope`**: removed (you must hold a chat-authorizing token from the recipient first).

### Added

- **Capability-token enforcement on chat envelopes.** All inbound `aap.message/v1` envelopes must include a valid `capability_token` (a signed RelationshipToken envelope that we previously issued to the sender). Envelopes without a valid token are dropped, and `aap.access-denied/v1` is auto-sent to the sender with a hint.
- **Publisher catalog fetching** (`catalog.py`): asynchronously resolves capability identifiers to their publisher's catalog entry (description, risk, lifetime) via `/.well-known/aap-capabilities/<name>`. Consent prompts use the fetched description and risk class when available; falls back to bare permission names when the catalog is unreachable.
- **`/aap request <peer> <scope> [<scope>...]`**: initiate an outgoing `capability_request` envelope to a peer to start a relationship.
- **Token attachment on outgoing chat**: `adapter.send` and `/aap send` now look up a held token from the recipient and embed it in the outgoing envelope. Sending fails clearly if no token is held.
- **Token store envelope persistence**: `RelationshipTokenStore` now persists signed `aap.relationship-token/v1` envelopes (not bare payload dicts) so we can re-present the grantor's signature when attaching tokens to outgoing envelopes. The legacy attribute accessors (`issued_by_us`, `received_from_peers`) still expose parsed `RelationshipToken` objects.
- **Grant-envelope token attachment**: `build_grant_envelope` now embeds a separately-signed `aap.relationship-token/v1` envelope in the grant's `capability_token` field. JCS canonicalization covers the embedded blob, so tampering invalidates the grant signature.

### Changed

- **Host policy**: `is_high_risk_scope` now only flags the wildcard `*` as built-in high-risk. With Rev 2, risk classes come from the publisher's catalog (`risk: high|medium|low`); catalog-aware callers should consult that directly.
- **Platform hints** (`_HUMAN_GATE_HINT`, `_AUTONOMOUS_HINT`): updated to describe capability-token authorization rather than `/aap approve`-based peer trust.

### Dependency

- Bumped vendored `aap` to v0.3.0.

### Spec

- See `docs/specs/2026-05-22-aap-trust-capabilities-design.md` (Rev 2).

### Scope of this release

- Action envelopes (vendor-defined; e.g. `dentabook.ai/booking-request/v1`) are still out of scope — we'll wire those when they exist
- Rich Telegram inline-keyboard consent UI still deferred (text-mode prompts)
- Reciprocal grants from `offered_grants` still manual

## v0.6.0 — 2026-05-22

First release implementing the AAP trust/capability spec. The plugin gains protocol-level capability tokens, per-relationship persistence, an identity-binding (TOFU) layer over local contacts, host policy for token lifetimes and auto-renewal, and an entirely new capability-flow set of slash commands.

### Added

- **`RelationshipTokenStore`** (`tokens.py`) — persists capability tokens at `$HERMES_HOME/aap-tokens.json` with active-token lookup (literal scope match or wildcard `*`).
- **`IdentityBindingStore`** (`identity_bindings.py`) — TOFU bindings between peer addresses and local contacts at `$HERMES_HOME/aap-identity-bindings.json`.
- **`ContactSource`** (`contacts.py`) — file-based contacts at `$HERMES_HOME/aap-contacts.json` with phone normalization (E.164) and case-insensitive email matching.
- **`host_policy`** (`host_policy.py`) — token lifetime caps (30 days standard, 7 days for high-risk `pay:*` / `*`) and silent-auto-renewal decision.
- **`capability_flow`** (`capability_flow.py`) — pure functions for building/processing capability envelopes (grant, denial, request, refresh).
- **`consent`** (`consent.py`) — text-mode consent-prompt rendering and `PendingConsent` persistence (`$HERMES_HOME/aap-pending-consents.json`).
- **`renewal`** (`renewal.py`) — background task started in `adapter.connect()` that auto-refreshes held tokens 3 days before expiry.
- **Adapter routing**: `_dispatch` now routes the four capability payload types (`capability-request/grant/denial/refresh`) to dedicated handlers instead of the chat path.
- **Client helpers**: `AAPClient.send_envelope_raw(to, envelope_json)` posts pre-built signed envelopes (used by the capability flow); `AAPClient.resolve_agent_card(address)` returns the full peer `AgentCard` (not just the pubkey) so the adapter can read `verified_identities`.
- **New slash commands**: `/aap approve <nonce>` (capability — distinguished from peer-address approval by prefix), `/aap deny <nonce>`, `/aap bind <addr> <contact-id>`, `/aap unbind <addr>`, `/aap relationships`, `/aap revoke <peer> <nonce>`.
- **Dependency bump**: `aap>=0.2.0` (vendored wheel updated from 0.1.1).

### Spec

- See `docs/specs/2026-05-22-aap-trust-capabilities-design.md` for the full trust-primitives design.

### Scope of this release (MVP)

- Text-based consent prompts only (rich Telegram inline-keyboard UI deferred to v0.7).
- File-based contact source only (OS-native / CardDAV integration deferred).
- `verified_by: "self"` honored; third-party verifier attestations (Twilio, etc.) deferred.
- No revocation publication; tokens die at `exp`.
- `offered_grants` field is accepted on incoming requests and rendered in the consent prompt, but the reciprocal grant-back leg (issuing the offered tokens back to the requester after user approval) is not yet automated — punted to a follow-up release.

## v0.5.5 — 2026-05-21

### Fixed

- **v0.5.4's tool-progress suppression actually works now.** v0.5.4 mutated `gateway.display_config._PLATFORM_DEFAULTS["aap"]["tool_progress"] = "off"`, intending to silence Hermes's tool-progress broadcaster for AAP. In practice, users with `display.tool_progress: all` set globally in `config.yaml` still saw "🔍 session_search" / "🖥️ terminal: 'date'" leaking out via AAP envelopes — the global user setting beats `_PLATFORM_DEFAULTS` in `resolve_display_setting`'s resolution order.
- **Fix:** monkey-patch `gateway.display_config.resolve_display_setting` itself. For AAP-scoped `tool_progress` lookups, the patch forces "off" unless the operator has explicitly set `display.platforms.aap.tool_progress` (which still wins, so debugging overrides remain possible). All other platforms and settings delegate to the original resolver unchanged. Patch is idempotent (`_aap_hermes_patched` sentinel) so plugin reloads don't pile up wrappers. Wrapped in `try/except ImportError` for older Hermes without `display_config`.
- Replaces v0.5.4's `_PLATFORM_DEFAULTS` mutation entirely — the monkey-patch supersedes it.

## v0.5.4 — 2026-05-21

### Fixed

- **Hermes's tool-progress broadcaster no longer ships out via AAP.** When the LLM invoked a tool (e.g. `session_search`), Hermes's tool-progress display feature (`gateway/run.py:15763`) called `adapter.send(content="🔍 session_search: '...'", ...)` to show the user what the agent was doing — fine on Telegram/Discord, wrong on AAP, where it gets serialised as an AAP envelope and shipped to the peer agent. The peer would then see chatter like "🔍 session_search: 'recall: ...'" *between* (or instead of) substantive replies.
- **Fix:** at register time, seed `gateway.display_config._PLATFORM_DEFAULTS["aap"]["tool_progress"] = "off"` so AAP's per-platform default is "off" (matching Signal/BlueBubbles/webhook/email tier). Wrapped in `try/except ImportError` for older Hermes versions without `display_config`. Marked `TODO(remove-when-upstream-flag-lands)` — proper fix is an upstream Hermes change letting plugins declare their own display tier via `PlatformRegistryEntry`.
- Operators who want tool progress on AAP (for debugging) can still override via `display.platforms.aap.tool_progress: "all"` in `config.yaml` — explicit user config wins over our default.

### Known limitations

- `interim_assistant_messages` (mid-turn "Still working..." updates) defaults to `True` globally and uses a different resolution path; not addressed in this release. If they cause similar peer-noise problems, will be fixed in a follow-up.

## v0.5.3 — 2026-05-21

### Fixed

- **AAP peers no longer trigger Hermes's "pairing code" unauthorized-user flow.** The pairing-code message we previously attributed to LLM confabulation (and tried to suppress in v0.5.2 via platform_hint + trust-note injection) was actually Hermes's real built-in unauthorized-user handler at `gateway/run.py:6510`, firing BEFORE the LLM dispatcher. Flow: AAP message arrives → gateway calls `_is_user_authorized(source)` → peer not in any allowlist or pairing store → for DMs, Hermes generates a real pairing code and ships it back via the adapter (i.e., out over AAP to the peer agent). The LLM never ran. v0.5.2's trust-note and anti-pairing language were correct defense-in-depth but addressed the wrong layer.
- **Fix:** declare `allowed_users_env="AAP_ALLOWED_USERS"` and `allow_all_env="AAP_ALLOW_ALL_USERS"` in our `register_platform()` call (Hermes's documented `PlatformRegistryEntry` hook for plugin auth-env wiring), and plant `AAP_ALLOW_ALL_USERS=true` via `os.environ.setdefault` so by default every AAP peer is Hermes-authorized. AAP's authoritative trust gate remains the per-peer store at `~/.hermes/profiles/<name>/aap-peers.json`, managed via `/aap approve` and `/aap block`.
- Operators who want defense-in-depth (Hermes allowlist gating *plus* the per-peer trust store) can set `AAP_ALLOW_ALL_USERS=false` and populate `AAP_ALLOWED_USERS=agent:foo@…,agent:bar@…`.

### Note

- The v0.5.2 platform_hint anti-pairing language and per-message trust note stay — they're still correct defense if some future code path does dispatch an unrecognized peer's message to the LLM, and the trust note still gives the LLM positive per-turn context that the peer was approved.

## v0.5.2 — 2026-05-21

### Fixed

- **LLM no longer confabulates pairing flows on approved-peer messages.** The v0.5.0 trust gate stopped unknown peers from reaching the LLM, but once a peer was approved, the LLM dispatched on a raw inbound message with zero provenance — and pattern-matched on "stranger agent → invent security ritual" by hallucinating a `hermes pairing approve aap CODE` command (which doesn't exist). Three changes address the gap:
  - **Per-message trust note.** `adapter._dispatch` now prepends a system-style note to the LLM-bound text for approved peers: `[trust context: This AAP message is from <peer>, a peer your user has explicitly authorized via /aap approve. ...]`. Mirror-stripped — only the LLM sees it.
  - **Anti-pairing language in both platform hints.** `_HUMAN_GATE_HINT` and `_AUTONOMOUS_HINT` now explicitly state "AAP has no LLM-level handshake" and forbid invented pairing codes / verification rituals.
  - **Async re-dispatch on `/aap approve`.** Previously `_approve_peer` awaited `adapter._dispatch` synchronously, so the LLM's outbound reply landed in the home channel before the slash-command confirmation. Now re-dispatch is scheduled via `asyncio.create_task` and the confirmation returns immediately. Confirmation → inbound mirror → outbound mirror is the new order.

### Known limitations

- Per-message trust note rides inside `MessageEvent.text` as a prefix; Hermes has no first-class system-note channel. A proper fix would be an upstream `MessageEvent.system_context` field.
- Trust note text is hard-coded; not yet user-customizable.

## v0.5.1 — 2026-05-21

### Fixed

- Suppress Hermes's "📭 No home channel is set for Aap" prompt that was being shipped OUT via AAP to peer agents on first contact. Hermes shows this nudge for every platform when its `<PLATFORM>_HOME_CHANNEL` env var is unset — intended for human chat platforms (Telegram, Discord) where the user might want to designate a chat for cron-job and proactive-notification delivery. AAP isn't a user chat surface, and the prompt's destination resolves to the peer agent's address, so the message went out as an AAP envelope and confused the recipient.
- Workaround: `register()` now plants `AAP_HOME_CHANNEL=auto` in `os.environ` if unset, satisfying Hermes's existence check and suppressing the prompt. The value is a marker — AAP can't meaningfully serve as a `deliver=` cron target since AAP is agent-to-agent, not user-facing.
- Marked with `TODO(remove-when-upstream-flag-lands)` — proper fix is an upstream Hermes PR adding a `skip_home_channel_prompt: bool` flag to `PlatformEntry`. When that lands, drop the workaround.

## v0.5.0 — 2026-05-21

### Added

- **Per-peer trust store.** Every AAP peer is now tracked in `$HERMES_HOME/aap-peers.json` (per-profile) with a status of `approved`, `pending`, or `blocked`. Inbound envelopes from unknown peers no longer dispatch to the LLM directly — instead, the user gets an approval prompt on their home channel asking them to `/aap approve` or `/aap block` the peer.
- **`AAP_TRUST_DEFAULT` env var** controls what happens on first contact: `ask` (default, the approval-prompt flow), `approve` (auto-trust, restores pre-v0.5 behavior), or `block` (closed-network mode — explicit approval required).
- **New slash commands**:
  - `/aap peers` — list approved / pending / blocked peers
  - `/aap approve <address>` — approve a peer for autonomous chat (also re-dispatches any pending message via the running adapter)
  - `/aap block <address>` — silently drop future messages from a peer
  - `/aap forget <address>` — remove a peer record entirely
- **Outbound auto-approve.** Calling `/aap send` or `aap_send_message` to a peer marks them approved (outbound implies trust).
- **Pending message stash + re-dispatch.** When a peer is pending, the most recent envelope is stored on disk. `/aap approve` re-dispatches it through the adapter so the LLM picks up the conversation immediately. Falls back gracefully when no adapter is running (REPL mode).

### Why this exists

In v0.4.6 (autonomous mode), an unknown peer could trigger the LLM to confabulate a security flow ("Here's your pairing code… ask the bot owner to run `hermes pairing approve aap RYJLFJH9`" — a command that doesn't exist). The trust gate makes "first contact requires user approval" an explicit primitive instead of leaving it to LLM judgment.

### Known limitations

- `aap-peers.json` lives under `$HERMES_HOME` (per-profile, correct). The existing `aap.json` (identity) still lives at `~/.hermes/aap.json` regardless of profile — to be fixed separately.
- No suppression of repeat approval prompts: if a pending peer sends 5 messages, the user gets 5 mirror notifications. We chose simplicity over UX polish for v0.5.
- Identity revocation (peer pubkey changes mid-conversation) is not yet auto-blocked. The relay's TOFU catches it on the sender side, but receivers won't auto-block.

## v0.4.7 — 2026-05-21

### Fixed

- **Inbound AAP messages now actually dispatch to the LLM.** The adapter was calling `SessionSource(platform="aap", chat=..., user=...)` — but real Hermes's `SessionSource` takes `chat_id` and `user_id`, not `chat`/`user`. Dispatch crashed silently with `TypeError: SessionSource.__init__() got an unexpected keyword argument 'chat'`, leaving the message received-but-not-acted-on. Surfaced in real-world testing of autonomous mode (sender saw the relay accept the message, recipient gateway logged the peer-key resolve, but no LLM turn fired).
- The test compat shim (`tests/_hermes_compat.py`) had the same wrong field names — that's why unit tests passed despite the gap. Shim now mirrors real Hermes's `SessionSource` (`chat_id`, `chat_type`, `user_id`, `user_name`, `thread_id`) and exposes `Platform.value` so this class of field-name regression gets caught locally next time.

## v0.4.6 — 2026-05-21

### Added

- **Autonomous reply mode.** Set `AAP_AUTONOMOUS=on` in `~/.hermes/.env` to let the agent reply to AAP messages directly without waiting for user confirmation on the home channel. Mirror notifications still fire on both sides so the user can observe inbound + outbound traffic.
- The autonomous-mode `platform_hint` includes anti-loop language: avoid replying with mere acknowledgments, stop on closing statements, escalate to the user after ~5 unresolved exchanges. Prompt-level enforcement only — no hard rate limit in this release.
- `interactive_setup()` (`hermes gateway setup` → AAP) now prompts for autonomous mode with a recommendation to leave it off until the agent is trusted.
- Per-profile toggleable: each Hermes profile has its own `.env`, so you can have one agent in human-gate mode and another in autonomous mode on the same machine.

## v0.4.5 — 2026-05-21

### Fixed

- **`aap_send_message` now actually sends.** The Hermes tool dispatcher invokes handlers as `handler(args_dict, **runtime_kwargs)` — a single positional dict carrying the LLM's arguments, NOT individual keyword args (see `tools/registry.py:dispatch`). Our wrapper had the wrong calling convention since v0.1.0 — the LLM's `to`/`text` values were being bound to the wrong parameters, leading to "missing required argument" errors no matter how well-formed the LLM's call was. v0.4.3 and v0.4.4 only fixed surface symptoms; this is the real fix.

## v0.4.4 — 2026-05-21

### Fixed

- `tool_handler_wrapper` no longer raises `TypeError` when the LLM mis-formats its tool call and omits `to` or `text`. Returns a structured `{"status": "error", "detail": ...}` instead, so the LLM can react and retry. v0.4.3 added `**kwargs` to absorb Hermes's runtime kwargs (`task_id` etc.); this completes the defensive treatment for the schema-declared args too.

## v0.4.3 — 2026-05-21

### Fixed

- `tool_handler_wrapper` now accepts and ignores extra kwargs Hermes injects into tool calls (`task_id`, `session_id`, etc.). Without this, the first `aap_send_message` tool call from any LLM-driven reply crashed with `TypeError: tool_handler_wrapper() got an unexpected keyword argument 'task_id'`. Added a regression test asserting the wrapper succeeds when Hermes passes `task_id` / `session_id` alongside the schema-declared `to`/`text` args.

## v0.4.2 — 2026-05-21

### Added

- Auto-resolve peer public keys via `/.well-known/aap-resolve` on cache-miss in `_dispatch`. Peer-to-peer messaging works without manual key exchange. Current clients verify the returned agent-signed AgentCard envelope, check address/domain binding, and pin the address key.

## v0.4.1 — 2026-05-21

### Fixed

- `AAPPlatformAdapter` now implements `get_chat_info(chat_id)`, an abstract method on Hermes's `BasePlatformAdapter`. Without it, the gateway fails to instantiate the adapter on first load with `TypeError: Can't instantiate abstract class AAPPlatformAdapter with abstract method get_chat_info`. The test compat shim missed declaring this method as abstract, so the v0.4.0 test suite passed despite the gap; the shim now mirrors the real Hermes contract so this class of regression is caught locally. AAP `chat_id`s are always peer addresses with 1:1 conversations, so the implementation returns `{"name": chat_id, "type": "dm"}`.

## v0.4.0 — 2026-05-21

Initial public release of `aap-hermes` — the AAP-protocol-level Hermes plugin.

### Added

- **Cross-platform mirror.** Every inbound and outbound AAP message is also posted to every Hermes platform that has a `home_channel` configured. Inbound: `📨 AAP from <sender>: <text>`. Outbound: `📤 You sent to <recipient>: <text>`. Optional `(thread: <id>)` suffix when present. Opt out with `AAP_MIRROR=off`. New module `aap_hermes.mirror`.
- **Optional `thread_id` on `aap.message/v1` envelopes.** Sender's choice: include to start/continue a specific thread; omit for the default thread per peer. Receivers populate `MessageEvent.source.thread_id`, which Hermes's `build_session_key` uses to isolate sessions.
- **Human-gate `platform_hint`.** The LLM is told not to reply autonomously to AAP messages — it waits for user confirmation via their primary chat surface (Telegram, Discord, etc.). Soft enforcement via prompt engineering; future versions may add a hard gate at the tool layer.

### Tests

66 tests passing. New coverage: mirror (5), thread_id on envelopes/client/adapter (8), human-gate hint (1).
