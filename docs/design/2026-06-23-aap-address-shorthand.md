# AAP Address Shorthand

## Goal

Make the caret (`^`) the user-facing marker for an AAP agent address.

When a user or agent enters an address ending in `^`, Hermes should treat it as
shorthand for the hosted Agent Address namespace:

```text
chris^  =>  chris^agentaddress.org
whatever^  =>  whatever^agentaddress.org
```

The shorthand is only an input convenience. Wire protocol values, signed
envelopes, relationship records, Agent Cards, logs, and persisted state should
continue to use full canonical addresses.

## Product Rule

`<localpart>^` means `<localpart>^agentaddress.org`.

Examples:

```text
/aap friend chris^
# Equivalent to:
/aap friend chris^agentaddress.org

/aap send chris+hermes2^ hello
# Equivalent to:
/aap send chris+hermes2^agentaddress.org hello
```

Do not treat bare strings without `^` as AAP shorthand:

```text
chris        # not an AAP address shorthand
chris^       # AAP hosted-address shorthand
chris^x.com  # explicit AAP address
```

This keeps contact names, aliases, and natural-language phrases separate from
agent addresses.

## Where It Should Live

The canonical parser and formatter should live in `aap-python`, because address
syntax is protocol/library surface area shared by Hermes, future CLIs, `aap-js`,
OpenClaw, and other host integrations.

Recommended split:

1. Keep `Address.parse()` strict.
   It should continue to require a full `<localpart>^<domain>` address.

2. Add a separate user-input helper in `aap-python`.
   Suggested API:

   ```python
   Address.parse_user_input(
       value: str,
       *,
       default_domain: str = "agentaddress.org",
   ) -> Address
   ```

3. Use the helper only at user/LLM input boundaries.
   Internal protocol handling should continue to call strict parsing or compare
   already-canonical strings.

This prevents shorthand from leaking into signed protocol data while still
giving every app the same expansion behavior.

## Hermes Integration Points

After `aap-python` exposes the helper, `aap-hermes` should use it anywhere a
human or model supplies a peer/business address:

- `/aap friend <peer> [type] [resource]`
- `/aap unfriend <peer>`
- `/aap send <peer> <text>`
- `/aap bind <peer>` and `/aap unbind <peer>`
- `/aap inspect peer <peer>`
- `/aap clear_conversation <peer>`
- `/aap services <business>` and `/aap describe <business> <service>`
- group commands that accept member addresses
  (`/aap group start`, `/aap group add`)
- LLM tool handlers:
  - `aap_send_message(to=...)`
  - `aap_propose_relationship(peer_address=...)`
  - `aap_revoke_relationship(peer_address=...)`
  - `aap_list_services(business_address=...)`
  - `aap_describe_service(business_address=...)`
  - `aap_send_service_request(business_address=...)`
  - `aap_group_start(members=...)`

The returned address should immediately be converted to `str(Address)` and used
from that point onward.

Every place that currently calls `str(Address.parse(x))` on a human- or
model-supplied address should switch to the user-input helper, not just the most
common commands. If shorthand works in `send` but not `inspect` or `services`,
the UX is inconsistent. Treat this as the single rule:

```text
user/LLM input boundary  => parse_user_input
stored/signed/internal data => parse
```

## Non-Goals

- Do not change the AAP wire format.
- Do not store `chris^` in relationship or pending-proposal files.
- Do not allow empty localparts such as `^`.
- Do not infer `agentaddress.org` from bare localparts such as `chris`.
- Do not silently rewrite explicit domains, e.g. `chris^example.com` stays
  `chris^example.com`.

## Validation Rules

`Address.parse_user_input("chris^")` should:

- trim leading/trailing whitespace;
- if the value ends with `^`, append `agentaddress.org`;
- then run the existing strict parser;
- return the canonical lowercase address object.

The implementation does not need custom multi-caret handling. A simple
`value.endswith("^")` check is enough because the expanded value still goes
through strict parsing:

```text
chris^      => chris^agentaddress.org
chris^^     => chris^^agentaddress.org => invalid localpart
^           => ^agentaddress.org => invalid localpart
chris^x.com => chris^x.com
```

This means all existing localpart validation still applies:

```text
Chris^       => chris^agentaddress.org
chris+bot^   => chris+bot^agentaddress.org
bad name^    => invalid localpart
^            => invalid localpart
chris^^      => invalid localpart/domain shape
```

## Test Plan

`aap-python` tests:

- `Address.parse()` still rejects `chris^`.
- `Address.parse_user_input("chris^")` returns `chris^agentaddress.org`.
- `Address.parse_user_input("Chris+Bot^")` returns
  `chris+bot^agentaddress.org`.
- `Address.parse_user_input("chris^example.com")` returns
  `chris^example.com`.
- invalid shorthand still fails through the strict parser.

`aap-hermes` tests:

- `/aap friend chris^` proposes to `chris^agentaddress.org`.
- `/aap send chris^ hello` sends to `chris^agentaddress.org`.
- LLM tool input `{"to": "chris^"}` expands before relationship checks.
- Relationship stores contain only canonical full addresses.

## Acceptance Criteria

- Users can type `whatever^` anywhere Hermes asks for an AAP address.
- The UI/CLI confirms the expanded full address in responses.
- No shorthand address is ever signed, transmitted, or persisted.
- Existing full-address behavior remains unchanged.
