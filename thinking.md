# Part 3 — Thinking Question

**Scenario:** 3am. Guest at Villa B1 messages: *"There is no hot water and we have guests arriving for breakfast in 4 hours. This is unacceptable. I want a refund for tonight."*

---

## A. The Immediate Response

> Hi Vikram — I'm so sorry, this should never have happened, especially with guests on the way. I'm waking our on-call caretaker for Villa B1 right now and he'll be at your door within 30 minutes. I'll stay on this chat with you until it's resolved. About the refund — our manager will call you first thing in the morning so we can make this right properly.

*Why this wording:* It acknowledges and apologises without arguing, commits to a concrete action with a concrete time so the guest stops feeling unheard, and defers the refund to a human — the AI must never promise money. "I'll stay on this chat" buys time before the caretaker arrives without sounding like a bot.

## B. The System Design

The reply is the smallest piece. In parallel:

1. **Escalate.** Complaint + 3am + refund-keyword triggers the highest-priority tier, not the standard one.
2. **Page the on-call caretaker** via WhatsApp + SMS + voice call, escalating channels every 90 seconds until acknowledged.
3. **Notify the property manager** in the ops Slack channel with the full thread, guest CLV, and a "claim" button.
4. **Open a P1 ticket** linked to the reservation, SLA = 30 minutes to on-site arrival.
5. **Log everything** — inbound message, AI draft, action taken, page acknowledgement, every follow-up — into the `messages` table from Part 2.
6. **Watchdog.** If no human acknowledges within 30 minutes, page the head of ops and the founder, and a second AI reply goes out: *"I'm still working on this — our manager is being called now."* Silence is the worst possible outcome.

## C. The Learning

Two complaints is noise; three is a pattern. The system should:

1. **Auto-correlate.** When a complaint is classified, search 90 days of complaints for the same property and sub-type (hot water, AC, WiFi). Three hits in 60 days raises a maintenance flag on the property record.
2. **Block the cause, not the symptom.** Open a recurring maintenance ticket: "Investigate root cause of hot water failures at Villa B1." Track time between recurrences as the key metric.
3. **Pre-empt the next guest.** Until the cause is closed, check-in confirmations for Villa B1 include "the caretaker has personally tested the hot water this morning," and the caretaker gets a 6am checklist nudge.
4. **What I'd build:** a *Property Health* dashboard scoring each villa by complaint recurrence, time-to-resolution, and review sentiment, reviewed weekly. The goal is to make the third complaint impossible because the second one already triggered the fix.
