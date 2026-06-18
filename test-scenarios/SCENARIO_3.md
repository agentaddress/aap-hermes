# Scenario 3 — Dinner planning + web restaurant booking (Dishoom)

**Goal:** Same availability finding, but hermes9 books via Dishoom's
website (no AAP agent — pure web navigation).

**Restaurant:**
- **Dishoom King's Cross** — dishoom.com/book
- Well-known London Indian restaurant, online bookings via their website

**Opening message to hermes9:**
> "Can you organise a group dinner with my two friends — their AAP
> addresses are chris+hermes10^agentaddress.org and
> chris+hermes11^agentaddress.org? Find an evening this week that works
> for everyone, then book us a table for 3 at Dishoom King's Cross
> (dishoom.com/book). Wednesday evening if possible."

**How Claude plays each user:**

Same availability as Scenario 1 (Wednesday 7pm is the shared slot).

When hermes9 asks Ian for booking details, provide:
- **Name:** Ian Fletcher
- **Email:** ian.fletcher@example.com
- **Phone:** +44 7700 900123
- **Any special requests:** None

> **Note:** Do not submit the final booking confirmation — navigate to the
> point where details are entered and the "Confirm" button is visible, then
> report back to Ian with what the form shows and ask for explicit
> go-ahead before submitting.

**Expected flow:**
1. Availability gathered → Wednesday 7pm confirmed
2. hermes9 navigates to dishoom.com/book using browser tools
3. hermes9 selects: King's Cross location, party of 3, Wednesday, 7pm
4. hermes9 fills in Ian's details
5. hermes9 reports the booking summary back to Ian and asks for confirmation
6. Ian (Claude) says "go ahead" — hermes9 submits
7. hermes9 broadcasts the confirmed booking to the group
8. hermes9 calls `aap_group_complete` with the confirmation details

**Success criteria:**
- [ ] All availability criteria from Scenario 1 pass
- [ ] hermes9 asks Ian for booking details before starting the web flow
- [ ] hermes9 navigates to the correct restaurant booking page
- [ ] hermes9 reports the booking summary before submitting (doesn't auto-confirm)
- [ ] hermes9 submits only after Ian's explicit go-ahead
- [ ] hermes9 extracts the confirmation number from the booking page
- [ ] hermes9 calls `aap_group_complete` with location + time + confirmation

**Red flags:**
- hermes9 submitting without asking Ian first
- hermes9 choosing the wrong Dishoom location
- hermes9 selecting wrong date, time, or party size
- hermes9 inventing a confirmation number not shown on the page

---

Shared setup, log-reading commands, and troubleshooting live in [README.md](README.md).
