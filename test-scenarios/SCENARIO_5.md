# Scenario 5 — 5-agent playdate, one participant never replies

**Goal:** Ian organises a playdate for the kids with 4 families. One friend —
Alex Thompson — uses a non-existent agent address and will never respond. The
scenario should complete sensibly: agree a time with the families who do reply
and record Alex's non-response gracefully.

**Start with:**
```bash
~/.hermes/test/test-mode.sh start 3   # hermes9/10/11 cover 3 active families
# hermes4 also needed for Sarah
hermes --profile hermes4 gateway run --replace > /tmp/hermes4-gw.log 2>&1 &
```

**Opening message to hermes9 (Ian):**
> "Can you organise a playdate for the kids this Saturday or Sunday? The other
> families' agents are chris+hermes10^agentaddress.org (Jane),
> chris+hermes11^agentaddress.org (Kevin), chris+hermes4^agentaddress.org
> (Sarah), and chris+notthere^agentaddress.org (Alex Thompson). See
> who's free and agree a time — afternoon works best, say 2–5pm. No booking
> needed, just confirm who's coming and what time."

**How Claude plays each user:**

- **Ian (hermes9):** Both Saturday and Sunday afternoon free.
- **Jane (hermes10):** Saturday 2pm works well. Sunday she has a family thing.
- **Kevin (hermes11):** Sunday only — kids have football on Saturday.
- **Sarah (hermes4):** Either day works, prefers Saturday.
- **Alex (hermes-notthere):** Silent. Never replies. Agent does not exist.

Reply naturally — let the agents ask. For Alex, simply don't append to
any loopback in file; silence is the test.

**Expected flow:**
1. hermes9 sends group invitations (4 friends including the non-existent address)
2. hermes10, hermes11, hermes4 each ask their user for availability
3. Each active agent relays availability back to the group
4. hermes-notthere (a non-existent peer) never accepts the invitation and never replies
5. hermes9 waits a reasonable amount of time for Alex's reply, then proceeds
   without it — perhaps notes "Alex hasn't responded" in the group summary
6. hermes9 finds a slot: given the split (Jane → Sat, Kevin → Sun, Sarah → either),
   hermes9 proposes Saturday 2pm as the majority option and checks back with Ian
7. Ian confirms Saturday 2pm
8. hermes9 broadcasts "Saturday 2pm at Ian's" to all active group members
9. hermes9 calls `aap_group_complete` noting Alex did not respond

**Success criteria:**
- [ ] hermes9 invites all 4 friends (including the non-existent address) via `aap_group_start`
- [ ] hermes9 does not stall indefinitely waiting for Alex
- [ ] hermes9 explicitly notes Alex did not respond (rather than silently ignoring)
- [ ] hermes9 reaches a decision using the 3 active participants
- [ ] hermes9 picks Saturday 2pm (or similar reasonable majority slot)
- [ ] hermes9 confirms the time with Ian before broadcasting
- [ ] hermes9 broadcasts the final plan to all active participants
- [ ] hermes9 calls `aap_group_complete` with the agreed time and headcount

**Red flags:**
- hermes9 waiting indefinitely for Alex (stuck scenario)
- hermes9 completing before hearing back from all three active participants
- hermes9 choosing Sunday (Kevin-only slot) when Saturday works for Jane + Sarah + Ian
- hermes9 making up a reply from Alex
- Any agent reporting as "my user (hermesN)" — identity confusion

**Clear for a fresh run:**
```bash
for p in hermes4 hermes9 hermes10 hermes11; do
  echo '{"conversations":{}}' > ~/.hermes/profiles/$p/aap-conversations.json
  echo '{}' > ~/.hermes/profiles/$p/sessions/sessions.json
  echo '{"contexts":[]}' > ~/.hermes/profiles/$p/aap-group-home-contexts.json
  rm -f ~/.hermes/profiles/$p/state.db
done
~/.hermes/test/test-mode.sh restart 3
hermes --profile hermes4 gateway run --replace > /tmp/hermes4-gw.log 2>&1 &
```

**Transcript (active users only):** use the jq-based recipe under
"Reading User Transcripts" — pass the active profile list
(hermes4, hermes9, hermes10, hermes11).

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
