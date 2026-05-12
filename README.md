# Nistula Technical Assessment

A backend system that receives guest messages from multiple channels, normalises them, classifies the intent, drafts a reply via the Claude API, and returns the reply with a confidence-scored action recommendation.

Built with FastAPI + Anthropic Python SDK. The repository is organised as three parts, matching the brief:

- **Part 1** — webhook + Claude integration (this README, `src/`, `tests/`)
- **Part 2** — PostgreSQL schema (`schema.sql`)
- **Part 3** — written thinking answers (`thinking.md`)

---

## Quick start

```bash
# 1. Clone and enter
git clone https://github.com/<your-user>/nistula-technical-assessment.git
cd nistula-technical-assessment

# 2. Set up a virtualenv (recommended)
python3 -m venv .venv
source .venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Open .env and paste your Anthropic API key:
#   ANTHROPIC_API_KEY=sk-ant-...

# 5. Run the server
uvicorn src.main:app --reload --port 8000

# 6. Open the in-browser tester
open http://localhost:8000
```

The minimal tester at `/` has six preset payloads (one per query type). Click a preset, then **Send to /webhook/message** to see the normalised response.

Run the test suite (no API key required — Claude is mocked):

```bash
pytest -v
```

---

## API

### `POST /webhook/message`

**Request**

```json
{
  "source": "whatsapp",
  "guest_name": "Rahul Sharma",
  "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
  "timestamp": "2026-05-05T10:30:00Z",
  "booking_ref": "NIS-2024-0891",
  "property_id": "villa-b1"
}
```

`source` accepts: `whatsapp` · `booking_com` · `airbnb` · `instagram` · `direct`.
`booking_ref` and `property_id` are optional.

**Response**

```json
{
  "message_id": "8c0d1a4d-2c0e-4f7d-9c0d-...",
  "query_type": "pre_sales_availability",
  "drafted_reply": "Hi Rahul! Great news — Villa B1 is available from April 20 to 24. For 2 adults the rate is INR 18,000 per night (the base rate covers up to 4 guests), so 4 nights would be INR 72,000. Let me know if you'd like to confirm and I'll send the booking link.",
  "confidence_score": 0.91,
  "action": "auto_send",
  "confidence_breakdown": {
    "classifier_certainty": 0.92,
    "context_completeness": 1.0,
    "message_clarity": 0.95,
    "claude_self_rating": 0.95,
    "raw_score": 0.94,
    "final_score": 0.91,
    "caps_applied": [],
    "action": "auto_send"
  }
}
```

### `GET /health`
Liveness probe: returns `{"status": "ok", "service": "nistula-message-handler"}`.

### `GET /` and `GET /docs`
Minimal in-browser tester and auto-generated OpenAPI docs.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  POST /webhook/message  (FastAPI)                         │
│  ┌────────────────────────────────────────────────────┐  │
│  │ 1. Validate     →  Pydantic InboundMessage          │  │
│  │ 2. Normalise    →  src/normalizer.py                │  │
│  │ 3. Classify     →  src/classifier.py  (rule-based)  │  │
│  │ 4. Draft reply  →  src/claude_client.py             │  │
│  │ 5. Score        →  src/confidence.py                │  │
│  │ 6. Decide action → auto_send / agent_review / escalate │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

Every step is in its own module — small, testable, easy to swap. The Claude wrapper sits behind a thin interface, so a future move to a different model (or an in-house one) only changes one file.

---

## Confidence scoring logic

The brief asks us to design our own logic and explain it. The score is a **weighted blend of four independent signals**, each on `[0, 1]`, plus three hard caps for high-risk cases.

### Signals

| Signal | Weight | What it measures |
|---|---|---|
| `classifier_certainty` | 0.25 | Margin between the winning query type and the runner-up. A clear winner (margin ≥ 3) scores 1.0; a tie scores 0.4; no match at all scores 0.2. |
| `context_completeness` | 0.20 | Do we actually have the data to answer? Known `property_id` is worth 0.6; a `booking_ref` (when relevant for the query type) is worth 0.4. Pre-sales queries don't get penalised for a missing booking ref. |
| `message_clarity` | 0.20 | Heuristic readability of the inbound message: WhatsApp-length text scores best, very short or 600+-char essays score lower. Hedge words (`maybe`, `not sure`, `kind of`) and multiple stacked `?`s each reduce the score. |
| `claude_self_rating` | 0.35 | Claude's own 0–1 estimate appended to its draft as `[SELF_RATING: X.XX]`. The system prompt instructs it to use 0.9+ only when every fact comes from the supplied property context. This is the single strongest signal because it also captures hallucination risk. |

`raw_score = Σ (weight × signal)`

### Hard caps (always applied after the raw score)

| Trigger | Cap | Reason |
|---|---|---|
| `query_type == complaint` | 0.55 | A complaint is never auto-sendable. Capping forces escalation regardless of any other signal. |
| `property_id` unknown / missing | 0.75 | Without property context, even a confident-sounding reply could be wrong about a fact. Force at least agent review. |
| `claude_self_rating < 0.40` | 0.55 | Claude itself thinks the reply is thin or hedged — don't push it past agent review. |

### Action mapping

| `final_score` | `action` |
|---|---|
| ≥ 0.85 | `auto_send` |
| 0.60 – 0.85 | `agent_review` |
| < 0.60 | `escalate` |
| Any complaint | `escalate` (regardless of score) |

### Why this shape

- **Multi-signal beats a single number.** A reply that *sounds* confident can still be wrong about facts (hallucination) or about the underlying intent (misclassification). Each signal catches a different failure mode.
- **Caps express policy, not probability.** A complaint is a business-policy escalation, not a low-confidence answer. Caps keep that policy out of the weight-tuning loop.
- **The breakdown is returned to the caller.** Every response includes `confidence_breakdown`, so reviewers (and on-call engineers) can see *why* something landed where it did. This is the difference between a black-box score and a debuggable one.

---

## Channel & query-type coverage

| Source | Tested |
|---|---|
| `whatsapp` | ✓ (availability, complaint, check-in) |
| `booking_com` | ✓ (pricing) |
| `airbnb` | schema-accepted |
| `instagram` | ✓ (general enquiry) |
| `direct` | ✓ (special request) |

All six query types from the brief have at least one positive test in `tests/test_classifier.py` plus an end-to-end webhook test in `tests/test_webhook.py`.

---

## Error handling

| Failure | HTTP code | Behaviour |
|---|---|---|
| Malformed/invalid payload | 422 | Pydantic validation message |
| Empty / whitespace-only message | 422 | Validator rejects |
| Unknown `source` value | 422 | Enum rejection |
| Claude API timeout | 504 | Logged, generic message to caller |
| Claude API error | 502 | Logged, error type returned without leaking internals |
| Missing `ANTHROPIC_API_KEY` | 500 | Clear actionable error |
| Anything else | 500 | Caught by `unhandled_exception_handler`; stack trace logged server-side, not leaked |

---

## File layout

```
.
├── README.md                  # this file
├── .env.example               # required env vars (no real keys)
├── .gitignore
├── requirements.txt
├── schema.sql                 # Part 2
├── thinking.md                # Part 3
├── src/
│   ├── __init__.py
│   ├── main.py                # FastAPI app + routes
│   ├── models.py              # Pydantic schemas + enums
│   ├── normalizer.py          # inbound -> unified
│   ├── classifier.py          # rule-based query classification
│   ├── claude_client.py       # async wrapper around Anthropic SDK
│   ├── confidence.py          # multi-signal scorer + action mapper
│   └── property_context.py    # mock property data for the prompt
├── static/
│   └── index.html             # minimal in-browser tester
└── tests/
    ├── test_classifier.py
    ├── test_confidence.py
    └── test_webhook.py
```

---

## Design decisions worth calling out

**Hybrid classifier (rules first, Claude as fallback).** I deliberately did not route every classification through Claude. For 10k messages/day, rule-based classification is two orders of magnitude cheaper and faster, and hospitality queries are narrow enough that good keywords cover ~95% of cases. The classifier exposes a `margin` signal, so genuinely ambiguous messages are caught by the confidence scorer downstream and routed to a human anyway.

**The `[SELF_RATING: X.XX]` tail.** I asked Claude to self-rate its draft and parsed the rating off the end of the reply. This adds essentially zero latency (single token), no extra model call, and gives us a quality signal the rule-based scorer can't produce on its own. The system prompt is explicit about when 0.9+ is and isn't earned (every fact must come from supplied context).

**Hard caps instead of soft weights for policy.** Complaints, missing context, and low self-rating are not probability events — they're business rules. Encoding them as caps keeps the weight-tuning surface small and makes the policy easy to audit.

**The `confidence_breakdown` is in every response.** It would have been cleaner to hide it. I left it in because it's the difference between a black-box auto-send decision and one a human can sanity-check at 3am.

---

## What I'd do with more time

- Persist messages + decisions to Postgres (Part 2 already designs the schema; wiring is the next step).
- Add a `/messages/{id}/feedback` endpoint so when an agent edits a draft, we capture the diff and use it as training signal for prompt tuning.
- Replace the static `PROPERTIES` dict with a real `Property` repository so multi-property support is a config change.
- Add structured logging (JSON) + a Prometheus `/metrics` endpoint for action-distribution dashboards.
- Add a retry policy with exponential backoff on Claude transient errors.
- Introduce a real classifier eval set (~200 hand-labelled messages) and a `make eval` target so changes to the rules can be measured, not vibed.
