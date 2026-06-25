# aap-hermes installed — two steps left

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

## 3. Restart and run

```bash
hermes gateway restart   # or: hermes gateway run
```

You should see in the logs:

```
aap-hermes <version> starting for <localpart>^agentaddress.org
Registered agent <localpart>^agentaddress.org with relay
```

Your `AAP_LOCALPART` was saved during install. To set a home channel for
mirroring, or to change your identity, run `hermes gateway setup` and pick
**AAP**.

Full prerequisites, env-var reference, and troubleshooting:
[INSTALL.md](https://github.com/agentaddress/aap-hermes/blob/main/INSTALL.md).
