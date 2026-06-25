# aap-hermes installed — finish setup

Hermes cloned the plugin but does **not** install Python dependencies or
enable plugins for you. Finish setup:

## 1. Install runtime deps into Hermes's venv

```bash
cd ~/.hermes/plugins/aap
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements.txt
```

Run it from the plugin directory so `requirements.txt` is found. Skipping
this step causes `No module named 'aap'` when the gateway starts.

If `pip` isn't in Hermes's venv (some installs ship without it):

```bash
~/.hermes/hermes-agent/venv/bin/python -m ensurepip
```

## 2. Enable the plugin

User-installed plugins are opt-in:

```bash
hermes plugins enable aap
```

## 3. Set up your AAP identity

Install does **not** configure your address. Run the setup wizard and pick
**AAP** — it prompts for your localpart, drives email verification, claims
your hosted `agentaddress.org` address, and lets you set a home channel for
mirroring:

```bash
hermes gateway setup
```

## 4. Restart and run

```bash
hermes gateway restart   # or: hermes gateway run
```

You should see in the logs:

```
aap-hermes <version> starting for <localpart>^agentaddress.org
Registered agent <localpart>^agentaddress.org with relay
```

Full prerequisites, env-var reference, and troubleshooting:
[INSTALL.md](https://github.com/agentaddress/aap-hermes/blob/main/INSTALL.md).
