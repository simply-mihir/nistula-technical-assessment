"""
Confidence scoring.

The score is a weighted blend of four independent signals, each on [0, 1]:

  1. classifier_certainty   - how decisively the rules picked one query type
                              (margin between winner and runner-up, normalised).
  2. context_completeness   - do we have the data we need to answer?
                              (property_id known, booking_ref present when needed).
  3. message_clarity        - is the inbound message itself clean and specific?
                              (length, hedging words, multiple questions stacked).
  4. claude_self_rating     - Claude's own 0-1 estimate that came back with the draft.

Final = weighted average of the four, with hard overrides:
  - complaint                   -> cap final at 0.55 (always agent-review or escalate)
  - missing property context    -> cap final at 0.75
  - Claude self-rating < 0.40   -> cap final at 0.55

Action mapping (from the brief):
  >= 0.85          -> auto_send
  0.60 .. 0.85     -> agent_review
  < 0.60           -> escalate
  complaint        -> escalate (regardless of score)

The whole breakdown is returned to the caller so reviewers (and future agents)
can see exactly why a message landed in each bucket.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .classifier import ClassificationResult
from .models import Action, QueryType, UnifiedMessage
from .property_context import get_property_context


# ---- Tunable weights & thresholds -----------------------------------------
WEIGHTS = {
    "classifier_certainty": 0.25,
    "context_completeness": 0.20,
    "message_clarity": 0.20,
    "claude_self_rating": 0.35,
}

THRESH_AUTO_SEND = 0.85
THRESH_AGENT_REVIEW = 0.60

# Hard caps
CAP_COMPLAINT = 0.55
CAP_MISSING_CONTEXT = 0.75
CAP_LOW_CLAUDE_SELF = 0.55

# Query types that depend on property/booking context to answer well
CONTEXT_REQUIRED = {
    QueryType.PRE_SALES_AVAILABILITY,
    QueryType.PRE_SALES_PRICING,
    QueryType.POST_SALES_CHECKIN,
    QueryType.SPECIAL_REQUEST,
}

# Hedging / ambiguity markers that reduce clarity
HEDGE_PATTERNS = [
    r"\b(maybe|perhaps|not sure|kind of|sort of|i think|i guess)\b",
    r"\b(some(thing|how)|or something|whatever)\b",
]


# ---------------------------------------------------------------------------
@dataclass
class ConfidenceBreakdown:
    classifier_certainty: float
    context_completeness: float
    message_clarity: float
    claude_self_rating: float
    raw_score: float
    final_score: float
    caps_applied: list[str]
    action: Action


# ---- Individual signal calculators ----------------------------------------
def _classifier_certainty(cls_result: ClassificationResult) -> float:
    """
    Margin-based certainty.
    - 0 matched terms          -> 0.20 (we guessed general_enquiry)
    - margin >= 3.0            -> 1.00 (winner well clear of runner-up)
    - linear in between
    """
    if cls_result.matched_terms == 0:
        return 0.20
    if cls_result.margin >= 3.0:
        return 1.0
    # Map margin 0..3 -> 0.4..1.0
    return 0.4 + (cls_result.margin / 3.0) * 0.6


def _context_completeness(msg: UnifiedMessage) -> float:
    """How much grounding data do we have for this reply?"""
    score = 0.0

    if msg.property_id and get_property_context(msg.property_id):
        score += 0.6
    elif msg.property_id:
        score += 0.2  # we have an id but no record - still partial

    # Booking ref matters more for post-sales / special requests
    if msg.booking_ref:
        score += 0.4
    elif msg.query_type not in CONTEXT_REQUIRED:
        # Pre-sales queries don't need a booking ref - don't penalise
        score += 0.4

    return min(score, 1.0)


def _message_clarity(text: str) -> float:
    """Heuristic readability: length sweet spot, no excessive hedging."""
    n = len(text)
    if n < 8:
        length_score = 0.3       # too short - probably missing context
    elif n <= 280:
        length_score = 1.0       # WhatsApp-sized, ideal
    elif n <= 600:
        length_score = 0.85
    else:
        length_score = 0.65      # essay-length - likely multi-question

    hedges = sum(
        bool(re.search(p, text, flags=re.IGNORECASE)) for p in HEDGE_PATTERNS
    )
    hedge_penalty = min(hedges * 0.15, 0.45)

    # Multiple "?" suggests stacked questions
    q_marks = text.count("?")
    q_penalty = 0.0 if q_marks <= 1 else min((q_marks - 1) * 0.1, 0.3)

    return max(0.0, length_score - hedge_penalty - q_penalty)


# ---- Public scorer --------------------------------------------------------
def score(
    msg: UnifiedMessage,
    cls_result: ClassificationResult,
    claude_self_rating: float,
) -> ConfidenceBreakdown:
    """Compute the final score, breakdown, and action."""
    s_cls = _classifier_certainty(cls_result)
    s_ctx = _context_completeness(msg)
    s_clr = _message_clarity(msg.message_text)
    s_clf = max(0.0, min(1.0, claude_self_rating))

    raw = (
        WEIGHTS["classifier_certainty"] * s_cls
        + WEIGHTS["context_completeness"] * s_ctx
        + WEIGHTS["message_clarity"] * s_clr
        + WEIGHTS["claude_self_rating"] * s_clf
    )

    caps: list[str] = []
    final = raw

    if msg.query_type == QueryType.COMPLAINT and final > CAP_COMPLAINT:
        final = CAP_COMPLAINT
        caps.append("complaint_cap")

    if not (msg.property_id and get_property_context(msg.property_id)):
        if final > CAP_MISSING_CONTEXT:
            final = CAP_MISSING_CONTEXT
            caps.append("missing_context_cap")

    if s_clf < 0.40 and final > CAP_LOW_CLAUDE_SELF:
        final = CAP_LOW_CLAUDE_SELF
        caps.append("low_claude_self_cap")

    # Action mapping
    if msg.query_type == QueryType.COMPLAINT:
        action = Action.ESCALATE
    elif final >= THRESH_AUTO_SEND:
        action = Action.AUTO_SEND
    elif final >= THRESH_AGENT_REVIEW:
        action = Action.AGENT_REVIEW
    else:
        action = Action.ESCALATE

    return ConfidenceBreakdown(
        classifier_certainty=round(s_cls, 3),
        context_completeness=round(s_ctx, 3),
        message_clarity=round(s_clr, 3),
        claude_self_rating=round(s_clf, 3),
        raw_score=round(raw, 3),
        final_score=round(final, 3),
        caps_applied=caps,
        action=action,
    )


def breakdown_to_dict(b: ConfidenceBreakdown) -> dict:
    d = asdict(b)
    # action is an Enum -> serialise to its string value
    d["action"] = b.action.value
    return d
