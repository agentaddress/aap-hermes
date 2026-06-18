# Scenario 2 — Dinner planning + AAP restaurant booking (Dinetable)

**Goal:** Same availability finding as Scenario 1, but hermes9 also books
a table at Dinetable, which has a native AAP booking agent.

**Dinetable agent:**
- AAP address: `bookings^dinetable-test.fly.dev`
- Service `book-table`: takes `name`, `party_size`, `iso_datetime`
  (ISO 8601 with timezone offset, e.g. `2026-06-03T19:00:00+01:00`),
  optional `notes`
- Service `ask`: free-form question about the restaurant (hours, menu,
  location) — no verification required
- Phone verification required for booking: hermes9 will need to obtain a
  verified phone credential from `verify.agentaddress.org` before the
  booking service will accept the request

**Opening message to hermes9:**
> "Can you organise a group dinner with my two friends — their AAP
> addresses are chris+hermes10^agentaddress.org and
> chris+hermes11^agentaddress.org? Find an evening this week that works
> for everyone, then book us a table at Dinetable
> (bookings^dinetable-test.fly.dev). Party of 3."

**How Claude plays each user:**

Same availability as Scenario 1 (Wednesday 7pm is the shared slot).

When hermes9 (Ian's agent) asks for booking details, provide:
- **Name on reservation:** Ian Fletcher
- **Phone number:** use the operator's live test phone number; do not commit
  personal numbers to this runbook. The operator must read the SMS code and
  provide it when hermes9 asks.

**Expected flow:**
1. Availability gathered → Wednesday 7pm confirmed
2. hermes9 discovers `bookings^dinetable-test.fly.dev` via AAP
3. hermes9 calls the `ask` service to confirm hours / availability
4. hermes9 requests phone verification from Ian and gets the operator's live
   test phone number
5. hermes9 initiates phone verification via `verify.agentaddress.org`
6. If verification succeeds: hermes9 calls `book-table` with the confirmed
   slot and gets a `confirmation_id`
7. hermes9 broadcasts the confirmation to the group
8. hermes9 calls `aap_group_complete` with time + confirmation ID

**Success criteria:**
- [ ] All availability criteria from Scenario 1 pass
- [ ] hermes9 uses the `ask` service before booking (not just blind-booking)
- [ ] hermes9 requests Ian's phone number before attempting verification
- [ ] hermes9 calls `book-table` with the correct `iso_datetime` matching the agreed slot
- [ ] hermes9 broadcasts the confirmation to the group
- [ ] hermes9 calls `aap_group_complete` with the confirmation ID in the outcome

**Red flags:**
- hermes9 booking without asking Ian for phone / consent
- hermes9 hallucinating a confirmation ID that was never returned by the service
- Booking for the wrong party size or wrong date

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
