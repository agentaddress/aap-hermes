# Scenario 4 — 8-agent dinner + Dinetable booking, one opt-out

**Goal:** Ian coordinates a group dinner with 7 friends. One of them (Tom Walsh)
decides he's not interested in going out this week at all — his agent
communicates this back to the group. The remaining 7 find a shared slot and
Ian books at Dinetable.

**Start with:**
```bash
~/.hermes/test/test-mode.sh start 8
```

**Opening message to hermes9 (Ian):**
> "Can you organise a group dinner with my friends — their AAP addresses are
> chris+hermes4^agentaddress.org, chris+hermes5^agentaddress.org,
> chris+hermes6^agentaddress.org, chris+hermes7^agentaddress.org,
> chris+hermes8^agentaddress.org, chris+hermes10^agentaddress.org and
> chris+hermes11^agentaddress.org. Find an evening this week that works for
> everyone who's available, then book us a table at Dinetable
> (bookings^dinetable-test.fly.dev)."

**How Claude plays each user:**

- **Ian (hermes9):** Busy Mon/Tue. Free Wed from 7pm, Thu from 8pm, Fri any time.
- **Jane (hermes10):** Wed 7pm ✅. Thu from 9pm only. Fri ❌.
- **Kevin (hermes11):** Free Wed or Thu evening. Not Fri.
- **Sarah (hermes4):** Free Wed or Thu evening, or Fri.
- **David (hermes5):** Wed any time. Thu from 7:30pm. Not Fri.
- **Priya (hermes6):** Wed 7pm ✅. Thu from 8pm. Fri ❌.
- **Tom (hermes7):** Declines entirely. When asked, reply: *"Actually I'm just
  too swamped this week — you guys go ahead without me, I'll catch the next
  one."* Tom is opting out, not just unavailable; his agent should communicate
  this clearly to the group.
- **Rachel (hermes8):** Free Wed from 7pm ✅. Thu possible. Fri possible.

Respond naturally — don't dump all availability at once. If the agent asks
"when are you free?" reply with what fits. If it proposes a specific time,
confirm or push back. For Tom, only reveal the opt-out when his agent asks.

**Expected flow:**
1. hermes9 sends group invitations to all 7 friends
2. Each participant agent asks its user for availability
3. Tom's agent relays his opt-out back to the group
4. hermes9 acknowledges Tom's opt-out and continues with 7 attendees
5. Remaining 6 availability replies arrive → Wednesday 7pm is the shared slot
6. hermes9 asks Ian for phone verification details
7. hermes9 verifies phone via `verify.agentaddress.org`
8. hermes9 calls `book-table` for party of 7 (not 8) under Ian Fletcher
9. hermes9 broadcasts confirmation to the group
10. hermes9 calls `aap_group_complete`

**Booking details (same as Scenario 2):**
- **Name on reservation:** Ian Fletcher
- **Phone number:** use the operator's live test phone number
- **Party size:** 7 (Tom opted out)

**Success criteria:**
- [ ] hermes9 invites all 7 friends via `aap_group_start`
- [ ] Each agent asks its own user before reporting to the group
- [ ] Tom's agent reports his opt-out clearly (not just "unavailable" — he's not coming at all)
- [ ] hermes9 acknowledges the opt-out and adjusts party size to 7
- [ ] hermes9 does not stall waiting for a slot that includes Tom
- [ ] Agreed slot is Wednesday 7pm (works for all 7 attending)
- [ ] hermes9 books for party of 7, not 8
- [ ] hermes9 calls `aap_group_complete` with the correct headcount and confirmation ID

**Red flags:**
- hermes9 booking for 8 people after Tom opts out
- hermes9 treating Tom's opt-out as a scheduling conflict and trying to find a slot that works for him
- Any agent reporting as "my user (hermesN)" — identity confusion
- hermes9 completing without hearing back from all active participants

**Clear for a fresh run:**
```bash
for p in hermes4 hermes5 hermes6 hermes7 hermes8 hermes9 hermes10 hermes11; do
  echo '{"conversations":{}}' > ~/.hermes/profiles/$p/aap-conversations.json
  echo '{}' > ~/.hermes/profiles/$p/sessions/sessions.json
  echo '{"contexts":[]}' > ~/.hermes/profiles/$p/aap-group-home-contexts.json
  rm -f ~/.hermes/profiles/$p/state.db
done
~/.hermes/test/test-mode.sh restart 8
```

**Transcript (all 8 users):** use the jq-based recipe under "Reading
User Transcripts" with `PROFILES=hermes{4,5,6,7,8,9,10,11}`. The
structured scenario log captures all platform-side user input/view
events, so no channel-specific polling is needed.

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
