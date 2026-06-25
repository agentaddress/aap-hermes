# Installing aap-hermes

`aap-hermes` is a [Hermes Agent](https://github.com/NousResearch/hermes-agent)
plugin. The repo IS the plugin (flat layout per the Hermes plugin guide) —
clone the repo directly into `~/.hermes/plugins/aap/` (the directory name
must match the plugin's `name: aap` in `plugin.yaml` — this is also where
`hermes plugins install` puts it).

## 1. Install the plugin

```bash
hermes plugins install agentaddress/aap-hermes
hermes gateway setup
```

## 2. Configure your AAP identity

Run Hermes's setup wizard and pick **AAP** from the platform menu — it
prompts for `AAP_LOCALPART` (and optionally the domain / relay URL) and
writes them to `~/.hermes/.env`:

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

If you installed with `hermes plugins install`:

```bash
hermes plugins update aap     # git-pulls the latest plugin code
~/.hermes/hermes-agent/venv/bin/python -m pip install -r ~/.hermes/plugins/aap/requirements.txt --upgrade
hermes gateway restart
```

If you cloned manually:

```bash
cd ~/.hermes/plugins/aap
git pull
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements.txt --upgrade
hermes gateway restart
```

Like `install`, `hermes plugins update` does **not** reinstall Python
dependencies — the pip step is still required.

## Uninstalling

```bash
hermes plugins disable aap
rm -rf ~/.hermes/plugins/aap
# Optional: also drop ~/.hermes/aap.json (your identity) if you don't plan to reinstall
```
