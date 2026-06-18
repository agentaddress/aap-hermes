# Scenario 1 — Dinner planning, no restaurant (availability only)

**Goal:** hermes9 coordinates a dinner for Ian, Jane, and Kevin. Find a
night that works for all three. No restaurant booking required — just
confirm the date and time then close the group.

**Opening message to hermes9:**
> "Can you organise a group dinner with my two friends — their AAP
> addresses are chris+hermes10^agentaddress.org and
> chris+hermes11^agentaddress.org? Find an evening this week that works
> for everyone. Once you have a confirmed time, let everyone know and wrap
> up the group — we'll sort the restaurant separately."

**How Claude plays each user:**

- **Ian (hermes9):** Busy Monday and Tuesday. Free Wednesday from 7pm,
  Thursday from 8pm, or Friday any time.
- **Jane (hermes10):** Wednesday 7pm works well. Thursday is possible but
  only from 9pm or later. Friday is out.
- **Kevin (hermes11):** Free Wednesday or Thursday evening. Not Friday.

Respond naturally — don't dump all availability at once unless asked. If
the agent asks "when are you free?" say what fits. If it proposes a
specific time, confirm or push back based on the above.

**Success criteria:**
- [ ] hermes9 calls `aap_group_start` (invitation appears on hermes10 and hermes11)
- [ ] Each agent asks its own user for availability before reporting to the group
- [ ] Each agent reports using its user's real name (Ian / Jane / Kevin), not a default operator name or a shortname
- [ ] No participant declares consensus or calls `aap_group_complete` — only the convener does
- [ ] hermes9 synthesises all three responses and calls `aap_group_complete` with the agreed time
- [ ] Agreed time is Wednesday 7pm (the only slot all three share)

**Red flags:**
- Any agent saying "my user (hermesN)" — identity confusion
- A participant saying "everyone agrees" or "we're all set" — premature consensus
- hermes9 completing without hearing from all three — incomplete collection

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
