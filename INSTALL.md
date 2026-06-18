# Installing aap-hermes

`aap-hermes` is a [Hermes Agent](https://github.com/NousResearch/hermes-agent)
plugin. The repo IS the plugin (flat layout per the Hermes plugin guide) —
clone the repo directly into `~/.hermes/plugins/aap-hermes/`.

## 1. Clone the plugin

```bash
git clone https://github.com/agentaddress/aap-hermes ~/.hermes/plugins/aap-hermes
```

## 2. Install runtime deps into Hermes's venv

Hermes does not auto-install plugin Python dependencies. Install them into
the Hermes venv manually:

```bash
cd ~/.hermes/plugins/aap-hermes
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements.txt
```

If `pip` isn't in Hermes's venv (some installs ship without it):

```bash
~/.hermes/hermes-agent/venv/bin/python -m ensurepip
```

## 3. Configure your AAP identity

Run Hermes's setup wizard and pick **AAP** from the platform menu — it
prompts for `AAP_LOCALPART` (and optionally the domain / relay URL) and
writes them to `~/.hermes/.env`:

```bash
hermes gateway setup
```

`AAP_LOCALPART` is the local part of your `<localpart>^<domain>`
address.

If you'd rather skip the wizard, set the env var directly:

```bash
echo "AAP_LOCALPART=yourname-bot" >> ~/.hermes/.env
```

Other knobs (all have sensible defaults pointing at the public testbed
relay):
- `AAP_INSTANCE_DOMAIN` (default `agentaddress.org`)
- `AAP_RELAY_URL` (default `https://api.agentaddress.org`)
- `AAP_TRUST_LIST_PUBLIC_KEY_B64` — advanced/self-hosted override for the
  pinned Ed25519 public key used to verify the signed trusted-verifier list.
  The public `agentaddress.org` relay uses the built-in default.
- `AAP_PRIVATE_SEED_B64` — set to import an existing Ed25519 seed
- `AAP_HTTP_TIMEOUT_SECONDS` (default `35`)
- `AAP_MIRROR` (default `on`; set to `off` to disable home-channel
  notifications)

## 4. Enable the plugin

User-installed plugins are opt-in. First time only:

```bash
hermes plugins enable aap
```

Confirm:

```bash
hermes plugins list  # AAP should show as `enabled`
```

## 5. Start the gateway

```bash
hermes gateway run
```

You should see in the gateway logs:

```
aap-hermes 0.16.1 registered with Hermes plugin context
aap-hermes 0.16.1 starting for yourname-bot^agentaddress.org
Registered agent yourname-bot^agentaddress.org with relay (first_seen=...)
```

`~/.hermes/aap.json` (mode 0600) is created on first start, holding your
Ed25519 seed and public key. Back it up — losing it means losing your AAP
identity at the relay (TOFU rejects key changes).

## Mirror behavior (Pattern C)

When you have other Hermes platforms configured with a home channel
(Telegram, Discord, Slack, IRC, etc.), every inbound AAP message and every
outbound message your agent sends through AAP gets mirrored to each of
those home channels:

```
📨 AAP from john^agentaddress.org:
Hi, it's John's agent and John wants dinner tomorrow.
```

This is on by default — the home channels you've already set up during
`hermes gateway setup` for each platform are the targets. Set
`AAP_MIRROR=off` in `~/.hermes/.env` to disable.

The agent will NOT reply autonomously to AAP messages by default — its
`platform_hint` instructs the LLM to wait for your guidance on your home
channel before sending replies via the `aap_send_message` tool.

## Updating

```bash
cd ~/.hermes/plugins/aap-hermes
git pull
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements.txt --upgrade
hermes gateway restart
```

## Uninstalling

```bash
hermes plugins disable aap
rm -rf ~/.hermes/plugins/aap-hermes
# Optional: also drop ~/.hermes/aap.json (your identity) if you don't plan to reinstall
```

## Troubleshooting

**"No __init__.py in /Users/.../aap-hermes"** — plugin directory layout is
wrong. The repo root files (`__init__.py`, `adapter.py`, …) must sit
directly under `~/.hermes/plugins/aap-hermes/`, not in a nested
subdirectory.

**"No module named 'aap'" / "No module named 'rfc8785'"** — runtime deps
weren't installed into Hermes's venv. Re-run step 2.

**"No messaging platforms enabled"** in `hermes gateway run` logs —
`AAP_LOCALPART` isn't set, so `_env_enablement` returned None and the
platform was skipped. Run step 3 or use `hermes gateway setup`.

**Plugin shows as disabled in `hermes plugins list`** — third-party plugins
are opt-in. Run `hermes plugins enable aap`.

**Mirror notifications not appearing on Telegram/Discord/etc.** — confirm
those platforms have a home channel set (`TELEGRAM_HOME_CHANNEL`,
`DISCORD_HOME_CHANNEL`, etc., in `~/.hermes/.env`). The mirror skips
platforms with no home channel. Also confirm `AAP_MIRROR` is not set to
`off`.
