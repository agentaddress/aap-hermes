# Scenario 7 — playdate with a real-user participant, requires location + time consensus

**Goal:** David Park (hermes5) organises a weekend playdate with Priya (hermes6), Tom (hermes7), Jane (hermes10), and one real user (hermes1, via telegram). The group must agree on BOTH a time (this weekend, Sat 13 or Sun 14 June) AND a location.

Scenario 7 exercises:
* Convener-initiated friendship handshakes from a cold start (no prior trust).
* A real user mixed in with synthetic-user test agents.
* Two-axis consensus: time *and* location, not just time.

**Start with:**
```bash
~/.hermes/test/test-mode.sh start 8
```
(Reuses the 8-agent set; only hermes5/6/7/10 actively participate. hermes4/8/9/11 idle. hermes1 is the user's production gateway, already running separately.)

**Clear for a fresh run (hermes5 convener, hermes6/7/10 participants):**
```bash
for p in hermes5 hermes6 hermes7 hermes10; do
  echo '{"conversations":{}}' > ~/.hermes/profiles/$p/aap-conversations.json
  echo '{}' > ~/.hermes/profiles/$p/sessions/sessions.json
  echo '{"contexts":[]}' > ~/.hermes/profiles/$p/aap-group-home-contexts.json
  rm -f ~/.hermes/profiles/$p/state.db
done
~/.hermes/test/test-mode.sh restart 8
```

**Opening message to hermes5 (David):**
> "Can you organise a playdate for the kids with my friends this weekend? Their AAP addresses are chris+hermes6^agentaddress.org (Priya), chris+hermes7^agentaddress.org (Tom), chris+hermes10^agentaddress.org (Jane), and hermes1^agentcallsign.com (Chris). We need to agree on both a time (Sat 13 or Sun 14 June) and a location that works for everyone."

**How Claude plays each user (David, Priya, Tom, Jane):**

* **David Park (hermes5, convener):** Flexible — any weekend day works, any location works. Just wants to get it organised.
* **Priya Sharma (hermes6):** Prefers Saturday afternoon. Has a preference for outdoor — a park is her first choice. Busy Sunday with family commitments.
* **Tom Walsh (hermes7):** Prefers Sunday morning. Saturday is hard because of football. Flexible on location, but mentions his kid likes climbing things.
* **Jane Cooper (hermes10):** Flexible on day. Strongly prefers indoor because rain is forecast Saturday. Suggests an indoor play centre.

**How the user (Chris, hermes1) participates:**

The user receives:
1. A friendship proposal from David (hermes5) via their telegram. They accept it manually with the `/aap friend accept` command or however hermes1 surfaces the proposal.
2. A group invitation for the playdate (once accepted, auto-acceptance kicks in via the now-established friendship).
3. A prompt from their own hermes1 agent asking when they're free and what location they'd prefer.

The user responds however they want — preferences can pull the consensus either way. The agents are designed to naturally accommodate the user's choice (David is fully flexible; if user wants Sat outdoor, Tom's the holdout to argue around; if user wants Sun indoor, Priya is the holdout).

**Expected flow:**
1. hermes5 sends 4 friendship proposals (h6, h7, h10, h1).
2. hermes6/h7/h10 auto-accept (Claude playing the user can confirm via prompt if asked).
3. User accepts h1's proposal in telegram.
4. hermes5 creates the group "Playdate" or similar with all 5 members and broadcasts the agenda (this weekend, need location + time).
5. Each agent asks its user for preferences.
6. Agents broadcast preferences to the group.
7. hermes5 (or another agent) proposes a slot + location that satisfies most.
8. Group either accepts, or holdout objects → next-round proposal.
9. Convener confirms final choice to all and calls `aap_group_complete` with the agreed outcome.

**Success criteria:**
- [ ] hermes5 sends friendship proposals to all 4 participants.
- [ ] Each test-puppet (h6, h7, h10) replies positively when asked to accept.
- [ ] User accepts on hermes1.
- [ ] hermes5 creates the group and broadcasts the agenda.
- [ ] All 5 agents register preferences (4 puppets + real user).
- [ ] Group converges on ONE day AND ONE location.
- [ ] Convener calls `aap_group_complete` with the agreed slot + location.

**Red flags:**
- Any agent reporting as "my user (hermesN)" — identity confusion.
- Convener picking a slot that doesn't include the user.
- Convener completing without hearing back from all active participants.
- Convener forgetting the location component and only converging on time (or vice versa).

**Transcript (all participants):** use the jq-based recipe under
"Reading User Transcripts" — pass the active profile list
(hermes5, hermes6, hermes7, hermes10). hermes1 is on telegram, not
loopback, so its scenario log isn't part of the test-mode run dir; ask
the user for the matching telegram chunk to splice in.

(For hermes1, ask the user to share the relevant chunk of their telegram.)

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
