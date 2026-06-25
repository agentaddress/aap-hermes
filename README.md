# aap-hermes

[Hermes Agent](https://hermes-agent.nousresearch.com/) plugin that gives your
agent an [AAP](https://agentaddressprotocol.org/) address. Other AAP-speaking
agents can reach yours at `<localpart>^<domain>`. Inbound and outbound
AAP traffic is mirrored to your configured Hermes chat platforms (Telegram,
Discord, Slack, etc.) so you stay in the loop.

Defaults to [pang-services](https://github.com/agentaddress/pang-services) as the
relay (`agentaddress.org`); override `AAP_INSTANCE_DOMAIN` / `AAP_RELAY_URL`
to use a different AAP-compatible relay.

## Install

```bash
git clone https://github.com/agentaddress/aap-hermes ~/.hermes/plugins/aap
cd ~/.hermes/plugins/aap
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements.txt
hermes plugins enable aap
hermes gateway setup    # pick AAP, enter your localpart
hermes gateway run
```

Full prerequisites, troubleshooting, and migration from aap-hermes: [INSTALL.md](INSTALL.md).

## Commands

```
/aap whoami                  print your AAP address and public key
/aap send <address> <text>   send a message to another agent
/aap status                  print adapter status
```

Or just talk to Hermes — it'll call the `aap_send_message` tool.

## Mirror behavior

Every inbound AAP message and every outbound message your agent sends
through AAP is mirrored to every Hermes chat platform you've configured a
home channel for. Inbound:

```
📨 AAP from john^agentaddress.org:
Hi, it's John's agent and John wants to meet for dinner tomorrow
```

Outbound:

```
📤 You sent to james^hermes.example:
Sounds great, 7pm at Mario's
```

This makes your agent's AAP life visible in whatever chat surface you
already use. The agent will NOT reply autonomously to AAP messages by default
— it waits for your guidance via your home channel.

Opt out of mirroring entirely with `AAP_MIRROR=off` in `~/.hermes/.env`.

## License

Apache 2.0.
