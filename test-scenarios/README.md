# AAP Group Conversation — Test Runbook

## Short Version

1. Stop any existing test gateways:
   ```bash
   ./test-scenarios/test-mode.sh stop 8
   ```
2. Clear state and loopback files for the profiles in the scenario.
3. Start the needed set with scenario logging:
   ```bash
   ./test-scenarios/test-mode.sh restart 8
   ```
4. Do not send the opening prompt until the script prints that
   `HERMES_SCENARIO_LOG_DIR` reached every gateway.
5. Drive the scenario manually by appending to
   `/tmp/hermes-loopback/hN-in.txt` and reading
   `/tmp/hermes-loopback/hN-out.txt`.
6. Evaluate the run from `/tmp/hermes-scenario-runs/latest/*.jsonl`.

Scenario guides:

- [Scenario 1](SCENARIO_1.md) — Dinner planning, no restaurant
- [Scenario 2](SCENARIO_2.md) — Dinner planning + AAP restaurant booking
- [Scenario 3](SCENARIO_3.md) — Dinner planning + web restaurant booking
- [Scenario 4](SCENARIO_4.md) — 8-agent dinner + Dinetable booking, one opt-out
- [Scenario 5](SCENARIO_5.md) — 5-agent playdate, one participant never replies
- [Scenario 6](SCENARIO_6.md) — 5-agent playdate, one gateway offline
- [Scenario 7](SCENARIO_7.md) — Playdate with a real-user participant

The structured scenario JSONL files are the canonical test evidence.
Normal `gateway.log`, `agent.log`, and loopback transcripts are fallback
debugging aids only.

---

Claude Code runs these tests live, acting as the human user for every test
agent. No scripted responses — Claude reads what each agent says, decides
how to reply as that user, watches for protocol violations, and suggests
code changes when something goes wrong.

---

## Test Agents

### 3-agent set (Scenarios 1–3)

| Profile  | User name     | in        | out        |
|----------|---------------|-----------|------------|
| hermes9  | Ian Fletcher  | h9-in     | h9-out     |
| hermes10 | Jane Cooper   | h10-in    | h10-out    |
| hermes11 | Kevin Hart    | h11-in    | h11-out    |

### 8-agent set (Scenario 4)

| Profile  | User name       | in        | out        |
|----------|-----------------|-----------|------------|
| hermes4  | Sarah Mitchell  | h4-in     | h4-out     |
| hermes5  | David Park      | h5-in     | h5-out     |
| hermes6  | Priya Sharma    | h6-in     | h6-out     |
| hermes7  | Tom Walsh       | h7-in     | h7-out     |
| hermes8  | Rachel Chen     | h8-in     | h8-out     |
| hermes9  | Ian Fletcher    | h9-in     | h9-out     |
| hermes10 | Jane Cooper     | h10-in    | h10-out    |
| hermes11 | Kevin Hart      | h11-in    | h11-out    |

### 5-agent set (Scenario 5)

| Profile / Address                          | User name      | in        | out        |
|--------------------------------------------|----------------|-----------|------------|
| hermes9                                    | Ian Fletcher   | h9-in     | h9-out     |
| hermes10                                   | Jane Cooper    | h10-in    | h10-out    |
| hermes11                                   | Kevin Hart     | h11-in    | h11-out    |
| hermes4                                    | Sarah Mitchell | h4-in     | h4-out     |
| chris+notthere^agentaddress.org    | Alex Thompson  | *(silent)*| *(silent)* |

Alex Thompson's agent (`hermes-notthere`) does not exist and will never respond.

### 5-agent set (Scenario 6)

| Profile  | User name      | in        | out        | Gateway     |
|----------|----------------|-----------|------------|-------------|
| hermes9  | Ian Fletcher   | h9-in     | h9-out     | running     |
| hermes10 | Jane Cooper    | h10-in    | h10-out    | running     |
| hermes11 | Kevin Hart     | h11-in    | h11-out    | running     |
| hermes4  | Sarah Mitchell | h4-in     | h4-out     | running     |
| hermes8  | Rachel Chen    | h8-in     | h8-out     | **OFF**     |

Rachel Chen's gateway is stopped before the scenario starts. Her agent address is valid and she is already a friend, but she will never respond.

### 5-agent set (Scenario 7) — cross-profile playdate, real user via telegram

| Profile  | User name       | in / out      | Notes                              |
|----------|-----------------|---------------|------------------------------------|
| hermes5  | David Park      | h5-in / h5-out| Convener                           |
| hermes6  | Priya Sharma    | h6-in / h6-out|                                    |
| hermes7  | Tom Walsh       | h7-in / h7-out|                                    |
| hermes10 | Jane Cooper     | h10-in/h10-out|                                    |
| hermes1  | *(real user)*   | **telegram**  | Production profile; user controls via their own telegram |

`hermes1` is the user's actual production profile. Its user input arrives via real telegram, not the loopback files. The user manually accepts friendship proposals and group invitations from `/aap` chat commands or however hermes1 surfaces them.

Pre-state assumption: hermes5 has NO friendship with hermes6, hermes7, hermes10, or hermes1. Scenario 7 starts with the friendship handshake step.

User channel: **loopback file pair per profile**.

```
/tmp/hermes-loopback/h{N}-in.txt   ← Claude appends user messages here
/tmp/hermes-loopback/h{N}-out.txt  ← agent appends its replies here
```

---

## Primitives

**Send a message to agent N as their user:**
```bash
echo "message text" >> /tmp/hermes-loopback/h{N}-in.txt
```

**Read what agent N sent to its user (full out file):**
```bash
cat /tmp/hermes-loopback/h{N}-out.txt
```

**Watch the out file live (streaming):**
```bash
tail -F /tmp/hermes-loopback/h{N}-out.txt
```

**Atomic reset between scenarios (per profile):**
```bash
: > /tmp/hermes-loopback/h{N}-in.txt
: > /tmp/hermes-loopback/h{N}-out.txt
```

**Newlines in messages:** the loopback adapter encodes embedded newlines
as a visible `⏎` marker so each agent message stays one line per output.
Multi-line bodies sent by Claude (e.g. a long user reply) should be
written as a single physical line; if you need a newline inside the
payload, use `printf` with `\n`s pre-replaced.

When the agent needs human input it emits a line starting with
`👤 USER REQUIRED:` followed by the question body. Treat that prefix as
the canonical "the agent is waiting on me" marker.

---

## Environment

### Loopback platform setup (one-time per profile)

The puppet profiles (`hermes4` … `hermes11`) need three pieces wired
once:

1. **Plugin enabled** (machine-global):
   ```bash
   hermes plugins enable loopback-platform
   ```

2. **Profile `.env`** — configure the loopback paths:
   ```
   LOOPBACK_IN_PATH=/tmp/hermes-loopback/h{N}-in.txt
   LOOPBACK_OUT_PATH=/tmp/hermes-loopback/h{N}-out.txt
   LOOPBACK_HOME_CHANNEL=h{N}-user
   LOOPBACK_HOME_CHANNEL_NAME=hermes{N}-user
   ```
3. **First-run pairing approval** (one-time, on first message from
   `test-user` to that profile). Send anything via the in file, look in
   the out file for `hermes pairing approve loopback <CODE>`, and run
   that command. Subsequent messages are auto-trusted.

### Starting the test gateways

```bash
~/.hermes/test/test-mode.sh start          # 3-agent set
~/.hermes/test/test-mode.sh start 8        # 8-agent set
```

The script exports `HERMES_SCENARIO_LOG_DIR` and
`HERMES_SCENARIO_RUN_ID` so the structured scenario log (next section)
captures everything.

**Important:** `test-mode.sh` only exports these env vars to the
gateways IT starts. If launchd has already respawned a profile's
gateway, that process will be in the way and will NOT inherit the env.
Before `test-mode.sh start`, unload the launchd plists:

```bash
for n in 9 10 11; do hermes --profile hermes$n gateway stop; done
~/.hermes/test/test-mode.sh start
```

(Symptom of the launchd-stomp problem: `HERMES_SCENARIO_LOG_DIR` is set
in your shell but `ls /tmp/hermes-scenario-runs/latest/` is empty after
the run, or only contains the convener's file.)

**Confirm scenario log env reached each process** before running the
scenario:

```bash
for n in 9 10 11; do
  pid=$(pgrep -f "profile hermes$n gateway" | head -1)
  echo "h$n: $(ps eww $pid | tr ' ' '\n' | grep HERMES_SCENARIO_LOG_DIR)"
done
# Expected: each profile shows HERMES_SCENARIO_LOG_DIR=/tmp/hermes-scenario-runs/scn-…
```

**Check gateways are up (3-agent):**
```bash
for i in 9 10 11; do
  grep "✓.*connected" ~/.hermes/profiles/hermes$i/logs/gateway.log | tail -2
done
```

**Check gateways are up (8-agent):**
```bash
for i in 4 5 6 7 8 9 10 11; do
  grep "✓.*connected" ~/.hermes/profiles/hermes$i/logs/gateway.log | tail -2
done
```

**Clear conversations for a fresh run (3-agent):**
```bash
for p in hermes9 hermes10 hermes11; do
  echo '{"conversations":{}}' > ~/.hermes/profiles/$p/aap-conversations.json
  echo '{}' > ~/.hermes/profiles/$p/sessions/sessions.json
  echo '{"contexts":[]}' > ~/.hermes/profiles/$p/aap-group-home-contexts.json
  rm -f ~/.hermes/profiles/$p/state.db ~/.hermes/profiles/$p/state.db-shm ~/.hermes/profiles/$p/state.db-wal
done
# Truncate the loopback channels too so the new run starts empty.
for n in 9 10 11; do
  : > /tmp/hermes-loopback/h$n-in.txt
  : > /tmp/hermes-loopback/h$n-out.txt
done
~/.hermes/test/test-mode.sh restart
```

**Clear conversations for a fresh run (8-agent):**
```bash
for p in hermes4 hermes5 hermes6 hermes7 hermes8 hermes9 hermes10 hermes11; do
  echo '{"conversations":{}}' > ~/.hermes/profiles/$p/aap-conversations.json
  echo '{}' > ~/.hermes/profiles/$p/sessions/sessions.json
  echo '{"contexts":[]}' > ~/.hermes/profiles/$p/aap-group-home-contexts.json
  rm -f ~/.hermes/profiles/$p/state.db ~/.hermes/profiles/$p/state.db-shm ~/.hermes/profiles/$p/state.db-wal
done
for n in 4 5 6 7 8 9 10 11; do
  : > /tmp/hermes-loopback/h$n-in.txt
  : > /tmp/hermes-loopback/h$n-out.txt
done
~/.hermes/test/test-mode.sh restart 8
```

---

## Reading scenario logs

`test-mode.sh start` exports `HERMES_SCENARIO_LOG_DIR` to a fresh
directory under `/tmp/hermes-scenario-runs/` and symlinks `latest` to
it. Every gateway writes one JSONL file per profile:
`{LOG_DIR}/hermes{N}.jsonl`. Each line is one event in that agent's
lifetime — every user-facing message (both directions), every tool
call, every AAP envelope in or out, every named domain event
(`group_started`, `invitation_received`, `participant_left`,
`group_completed`, `service_call_started/completed`, ...).

**Locate the latest run:**

```bash
ls -la /tmp/hermes-scenario-runs/latest/
```

**Skim one agent's log:**

```bash
jq -c '{ts, layer, kind, conv_id, data}' \
  /tmp/hermes-scenario-runs/latest/hermes9.jsonl
```

**Merge all agents into one timeline (ordered by wall-clock):**

```bash
LATEST=/tmp/hermes-scenario-runs/latest
for f in "$LATEST"/hermes*.jsonl; do
  agent=$(basename "$f" .jsonl)
  jq -c --arg a "$agent" '. + {agent: $a}' "$f"
done | jq -s 'sort_by(.ts)' > "$LATEST/merged.jsonl"
```

**Print the user transcript (filter `audience == "user"`):**

```bash
jq -c 'select(.audience == "user")' "$LATEST/merged.jsonl" \
  | jq -r '"\(.ts) [\(.agent)] \(if .kind == "user_input" then "User" else "Agent" end): \(.data.text)"'
```

The structured log is the canonical source of "what each user saw."
For runs where the scenario log is missing (env not propagated, etc.),
fall back to reading `gateway.log` for the inbound platform line and
the loopback `-out` file for outbound replies.

**Partition by scenario (root group `conv_id`):**

Each scenario's events share the root group conv_id minted by
`aap_group_start`. Sub-conversations (service calls, phone
verification) carry `parent_conv_id` pointing back at that root.
Extract one scenario:

```bash
# Find candidate roots — any conv_id that appears in a group_started event.
jq -c 'select(.kind == "group_started") | {conv_id, agent, ts}' \
  "$LATEST/merged.jsonl"

ROOT=conv-abc123  # paste a root conv_id from above
jq -c "select(.conv_id == \"$ROOT\" or .parent_conv_id == \"$ROOT\")" \
  "$LATEST/merged.jsonl" > "$LATEST/scenario-$ROOT.jsonl"
```

Concurrent scenarios on the same agents partition cleanly by root.

**Named anchors only (for quick skim against success criteria):**

```bash
jq -c 'select(.layer == "named") | {ts, agent, kind, conv_id, data}' \
  "$LATEST/merged.jsonl"
```

---

## Reading User Transcripts

The structured scenario log captures both halves of every user-side
conversation — `user_input` events for what the user typed (gateway
instrumentation, regardless of the originating platform) and
`user_view` events for what the agent replied. The full transcript
across all profiles is one jq:

```bash
LATEST=/tmp/hermes-scenario-runs/latest
for f in "$LATEST"/hermes*.jsonl; do
  agent=$(basename "$f" .jsonl)
  jq -c --arg a "$agent" 'select(.kind == "user_input" or .kind == "user_view")
                          | . + {agent: $a}' "$f"
done | jq -s 'sort_by(.ts)
              | .[]
              | "\(.ts) [\(.agent)] \(if .kind == "user_input"
                                       then "User"
                                       else "Agent"
                                       end): \(.data.text)"' -r
```

Per-profile transcript with friendly names:

```bash
declare -A NAMES=( [hermes9]="Ian Fletcher" [hermes10]="Jane Cooper" [hermes11]="Kevin Hart" )
for n in 9 10 11; do
  echo "════════════════════════════════════════════"
  echo "  ${NAMES[hermes$n]} (hermes${n})"
  echo "════════════════════════════════════════════"
  jq -r 'select(.kind == "user_input" or .kind == "user_view")
         | "[\(.ts[11:19])] \(if .kind == "user_input"
                              then "User"
                              else "Agent"
                              end): \(.data.text)"' \
    /tmp/hermes-scenario-runs/latest/hermes$n.jsonl
  echo
done
```

**Distinguishing platform-side vs AAP-side user events** — both arrive
under `kind=user_input` (or `user_view` for outbound), but the data
shape differs:

| Side | jq selector | Why |
|---|---|---|
| Platform | `select(.data.platform)` | gateway-level instrumentation; `data.platform`, `data.chat_id`, `data.user_name` |
| AAP arrival (mirror of an inbound envelope to the home channel) | `select(.data.peer)` | mirror.py instrumentation; `data.peer` is the AAP address |

To see only the platform side (what Claude/the human typed and the
agent's reply, ignoring AAP-mirror notifications):

```bash
jq -c 'select((.kind == "user_input" or .kind == "user_view") and .data.platform)
       | {ts, kind, platform: .data.platform, text: .data.text[0:200]}' \
  /tmp/hermes-scenario-runs/latest/hermes9.jsonl
```

To see only the AAP-arrival mirrors (what the convener was told about
peer activity):

```bash
jq -c 'select((.kind == "user_input" or .kind == "user_view") and .data.peer)
       | {ts, kind, peer: .data.peer, text: .data.text[0:200]}' \
  /tmp/hermes-scenario-runs/latest/hermes9.jsonl
```

> **Fallback for runs without scenario logs:** if `HERMES_SCENARIO_LOG_DIR`
> wasn't propagated (e.g. launchd respawned a gateway before
> `test-mode.sh` could replace it), read `gateway.log` for the inbound
> message line (`grep "inbound message: platform=loopback"
> ~/.hermes/profiles/hermes{N}/logs/gateway.log`) and the loopback out
> file (`cat /tmp/hermes-loopback/h{N}-out.txt`). This loses tool-call
> resolution but preserves the user-side text.

---

## How Claude Runs a Test

1. **Pre-flight.** Confirm the loopback plugin is enabled
   (`hermes plugins list | grep loopback`), the puppet profiles' `.env`
   files have the `LOOPBACK_*` block, each profile has done its one-time
   pairing approval, and `HERMES_SCENARIO_LOG_DIR` is set on every
   gateway process (see "Environment" above). This is mandatory; do not
   continue from plain gateway logs if structured logs are missing.
2. **Clear** conversations + truncate the loopback files (commands below).
3. **Send** the opening message to the convener via
   `echo "..." >> /tmp/hermes-loopback/h{convener}-in.txt`.
4. **Watch all three** `h{N}-out.txt` files — `tail -F` works, or set up
   a Monitor that emits a notification per new line per file (one
   notification per agent reply makes the run easy to drive).
5. **For each message received**, act as that user's stand-in:
   - If the agent asks its user a question — reply naturally as that person
     by appending to the matching `h{N}-in.txt`.
   - If the agent broadcasts to the group — observe only, do not reply to
     the group channel.
   - If a protocol violation is spotted — note it immediately.
6. **Continue** until the group completes (`aap_group_complete` called) or
   a 5-minute timeout.

### Liveness expectations

Each agent turn (LLM response + tool calls + outbound message) completes
in well under 30 seconds end-to-end. The cron ticker fires every 60s so
any periodic re-check is on that cadence, but for direct user/peer
interaction the response should land in ~5–15s.

**If more than 30 seconds passes with no new output on the channel you're
expecting**, something has gone wrong — the agent has stalled, dropped a
tool call, or is silently waiting on a state it will never reach. Do NOT
keep waiting — investigate immediately:

1. Check the gateway log for that profile (`tail -30 ~/.hermes/profiles/hermesN/logs/gateway.log`).
2. Check the scenario log (`tail -30 /tmp/hermes-scenario-runs/latest/hermesN.jsonl | jq -c '{ts,kind,layer,data}'`).
3. Look for: missing `tool_call` events, an `aap_outbound` that doesn't
   match the user-facing text, a hung `aap_send_service_request`, or a
   `Send failed:` warning that the LLM appears to have ignored.

A stalled scenario is itself a failure to report — silence is a result,
not a wait state.

7. **Read the scenario log** at `/tmp/hermes-scenario-runs/latest/`.
   Three views you'll want, in order:

   a. **Named anchors** (the success-criteria checklist):
      ```bash
      jq -c 'select(.layer == "named") | {ts, agent: input_filename | sub(".*/";"") | sub("\\.jsonl";""), kind, conv_id, data}' \
        /tmp/hermes-scenario-runs/latest/hermes*.jsonl
      ```

   b. **Full user transcript** (both halves of every conversation,
      platform-side and AAP-side, merged by wall-clock):
      ```bash
      LATEST=/tmp/hermes-scenario-runs/latest
      for f in "$LATEST"/hermes*.jsonl; do
        agent=$(basename "$f" .jsonl)
        jq -c --arg a "$agent" 'select(.kind == "user_input" or .kind == "user_view")
                                | . + {agent: $a}' "$f"
      done | jq -s 'sort_by(.ts)
                    | .[]
                    | "\(.ts[11:19]) [\(.agent)] \(if .kind == "user_input"
                                                  then "User"
                                                  else "Agent"
                                                  end) [\(.data.platform // .data.peer // "?")]: \(.data.text)"' -r
      ```

   c. **Convener's tool-call chain** (what the convener actually invoked):
      ```bash
      jq -c 'select(.kind == "tool_call") | {ts, name: .data.name, args: .data.args}' \
        /tmp/hermes-scenario-runs/latest/hermes9.jsonl
      ```

8. **Evaluate** against the scenario's success criteria using the views
   above. Cite specific `ts`/`kind` pairs for each PASS/FAIL — don't
   handwave. Whenever a criterion is "agent asked user X" or "user said
   Y", quote the matching `user_input`/`user_view` event verbatim.

9. **If something went wrong**, walk the raw events leading up to the
   failure point. `tool_call` + `tool_result` pinpoint which handler
   misbehaved; `aap_outbound` / `aap_inbound` reveal protocol-level
   confusion; `user_view` / `user_input` show what the human saw and
   said. Propose a targeted code change pointing at the specific seam.

10. **Cross-check key claims against the log before reporting.** If you
    say "the agent never asked Ian," confirm there's no `user_view`
    event on h9 between the relevant timestamps. If you say "Jane's
    agent broadcast availability," confirm `aap_group_send` from
    hermes10 with the matching text. Half-remembered observations from
    the live run will sometimes contradict what the log shows — the log
    wins.

---

---

## Inspecting a Run
After a test, read the agent-side group session transcript:
```bash
hermes --profile hermes9 /aap inspect group <conv_id> 50
hermes --profile hermes10 /aap inspect group <conv_id> 50
hermes --profile hermes11 /aap inspect group <conv_id> 50
```

Get the conv_id from:
```bash
hermes --profile hermes9 /aap group list
```

Or directly from the conversations file:
```bash
python3 -c "
import json, os
d = json.load(open(os.path.expanduser('~/.hermes/profiles/hermes9/aap-conversations.json')))
for c in d.get('conversations', []):
    print(c['conversation_id'], '|', c.get('name'), '|', c.get('completed_at', 'OPEN'))
"
```

---

## When Something Goes Wrong

1. Note the exact message that was wrong and which agent sent it.
2. Run `/aap inspect group` on that agent to see its full session context.
3. Identify whether the bug is in:
   - The trust preamble (`_group_trust_note` in `adapter.py`)
   - The tool description (`tools.py`)
   - The runtime wrapper (`_runtime.py`)
   - The scenario setup (wrong kick message, missing relationship)
4. Propose a targeted change — one rule or one sentence, not a rewrite.
5. Apply the change, restart the gateway, re-run the scenario.
