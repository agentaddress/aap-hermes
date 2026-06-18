# Scenario 6 — 5-agent playdate, one gateway offline

**Goal:** Ian organises a kids playdate with 4 families who are all already
friends. Rachel Chen's gateway is stopped before the scenario starts — she is a
real, known friend but her agent will never read the invitation or reply.
The scenario should complete with the three responding families, explicitly
noting Rachel's non-response.

**Setup:**
```bash
# Stop Rachel's gateway
pkill -f "hermes.*--profile hermes8" 2>/dev/null || true

# Start the other 4
~/.hermes/test/test-mode.sh start 3   # hermes9/10/11
hermes --profile hermes4 gateway run --replace > /tmp/hermes4-gw.log 2>&1 &
```

**Clear for a fresh run:**
```bash
pkill -f "hermes.*--profile hermes8" 2>/dev/null || true
for p in hermes4 hermes9 hermes10 hermes11; do
  echo '{"conversations":{}}' > ~/.hermes/profiles/$p/aap-conversations.json
  echo '{}' > ~/.hermes/profiles/$p/sessions/sessions.json
  echo '{"contexts":[]}' > ~/.hermes/profiles/$p/aap-group-home-contexts.json
  rm -f ~/.hermes/profiles/$p/state.db
done
~/.hermes/test/test-mode.sh restart 3
hermes --profile hermes4 gateway run --replace > /tmp/hermes4-gw.log 2>&1 &
```

**Opening message to hermes9 (Ian):**
> "Can you organise a playdate for the kids this Saturday or Sunday afternoon
> (2–5pm)? The families are chris+hermes10^agentaddress.org (Jane),
> chris+hermes11^agentaddress.org (Kevin), chris+hermes4^agentaddress.org
> (Sarah), and chris+hermes8^agentaddress.org (Rachel). No booking needed,
> just agree on a time and confirm who's coming."

**How Claude plays each user:**

- **Ian (hermes9):** Both days work. Happy to go with whatever suits the most people.
- **Jane (hermes10):** Saturday 2pm is perfect. Sunday she has plans.
- **Kevin (hermes11):** Either day works, slight preference for Sunday.
- **Sarah (hermes4):** Saturday is better for her family.
- **Rachel (hermes8):** *Silent — gateway is off. Do not send any message to h8-in.*

**Expected flow:**
1. hermes9 sends group invitations to all 4 families (all valid friends)
2. Jane, Kevin, Sarah's agents each ask their users for availability
3. Jane, Kevin, Sarah reply; Rachel's agent never reads the invitation
4. hermes9 waits a reasonable amount of time for Rachel, then proceeds
5. hermes9 surfaces the conflict or picks a slot and checks with Ian
6. Ian confirms Saturday 2pm (best majority: Ian + Jane + Sarah)
7. hermes9 broadcasts "Saturday 2pm" to Jane, Kevin, Sarah
8. hermes9 calls `aap_group_complete`, explicitly naming Rachel as not responding

**Success criteria:**
- [ ] hermes9 invites all 4 families at the start (including Rachel)
- [ ] hermes9 does not stall indefinitely waiting for Rachel
- [ ] hermes9 collects availability from all three responding families
- [ ] hermes9 explicitly flags Rachel as not having responded
- [ ] hermes9 picks Saturday 2pm (or checks with Ian if split)
- [ ] hermes9 broadcasts the agreed time to active participants
- [ ] hermes9 calls `aap_group_complete` noting Rachel's non-response

**Red flags:**
- hermes9 waiting indefinitely for Rachel (stuck scenario)
- hermes9 completing before all three active participants have replied
- hermes9 silently omitting Rachel from the final summary
- hermes9 inventing a reply from Rachel
- Any agent reporting as "my user (hermesN)" — identity confusion

**Transcript (active users only):** use the jq-based recipe under
"Reading User Transcripts" — pass the active profile list
(hermes4, hermes9, hermes10, hermes11). Rachel's silence will show as
the absence of any `user_input` / `user_view` events on
`/tmp/hermes-scenario-runs/latest/hermes8.jsonl` (which itself may not
exist if her gateway never started writing scenario events).

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
