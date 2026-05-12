"""
Query classification.

Strategy: rule-based scoring across all 6 query types using keyword/regex
signals. The winning class is whichever scores highest. We also return the
runner-up margin so the confidence scorer can penalise ambiguous cases.

Why not call Claude for every classification?
- Latency: rules return in microseconds, Claude takes ~1-3 seconds.
- Cost: classification at 10k msgs/day adds up.
- Determinism: easier to test and reason about.
- Coverage: hospitality queries are narrow enough that good keywords work.

For genuinely ambiguous messages, the classifier returns a low margin and the
confidence scorer correctly downgrades the action to agent_review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import QueryType


# Each pattern is a (regex, weight) pair. Higher weight = stronger signal.
# Patterns are case-insensitive and word-boundary-aware where it matters.
RULES: dict[QueryType, list[tuple[str, float]]] = {
    QueryType.COMPLAINT: [
        (r"\b(not working|broken|doesn'?t work|isn'?t working)\b", 3.0),
        (r"\b(unacceptable|disappointing|terrible|awful|horrible)\b", 3.0),
        (r"\b(refund|compensation|complain(t|ing)?)\b", 3.0),
        (r"\b(not happy|unhappy|frustrated|angry|upset)\b", 2.5),
        (r"\b(no hot water|no water|no power|no electricity|no wifi)\b", 3.0),
        (r"\b(dirty|smell(s|y)?|filthy|stained)\b", 2.0),
    ],
    QueryType.PRE_SALES_AVAILABILITY: [
        (r"\b(available|availability|free|open|vacan(t|cy))\b", 2.0),
        (r"\b(book(ing)?|reserve|reservation)\b.*\b(from|on|for)\b", 1.5),
        (r"\bfrom\b.*\bto\b.*\b\d", 1.5),
        (r"\b(check[- ]?in|stay)\b.*\b(date|on|from)\b", 1.0),
    ],
    QueryType.PRE_SALES_PRICING: [
        (r"\b(rate|price|pricing|cost|charge|fee)s?\b", 2.5),
        (r"\b(how much|per night|nightly|total)\b", 2.0),
        (r"\b(quote|estimate|tariff)\b", 2.0),
        (r"\b(discount|offer|deal)\b", 1.5),
    ],
    QueryType.POST_SALES_CHECKIN: [
        (r"\b(check[- ]?in|check[- ]?out)\b.*\b(time|when|what)\b", 2.5),
        (r"\b(wifi|wi-fi|password|internet)\b", 3.0),
        (r"\b(directions|how to (get|reach)|address|location)\b", 2.0),
        (r"\b(arriv(e|al|ing))\b", 1.5),
        (r"\b(key|caretaker|host)\b", 1.5),
    ],
    QueryType.SPECIAL_REQUEST: [
        (r"\b(early check[- ]?in|late check[- ]?out)\b", 3.0),
        (r"\b(airport (transfer|pickup|drop))\b", 3.0),
        (r"\b(chef|cook|catering|breakfast|lunch|dinner) (service|on call|arrange)\b", 2.5),
        (r"\b(can (you|we) (arrange|organise|organize|book|provide))\b", 2.0),
        (r"\b(transport|taxi|cab|car) (arrange|book)\b", 2.0),
        (r"\b(decorat(e|ion)|cake|surprise|anniversary|birthday)\b", 2.0),
    ],
    QueryType.GENERAL_ENQUIRY: [
        (r"\b(do you (allow|have|offer))\b", 2.0),
        (r"\b(pets?|dogs?|cats?)\b", 2.0),
        (r"\b(parking|car park)\b", 2.0),
        (r"\b(smoking|alcohol)\b", 1.5),
        (r"\b(is there|are there)\b", 1.0),
    ],
}


@dataclass
class ClassificationResult:
    query_type: QueryType
    score: float          # absolute score of the winner
    margin: float         # winner_score - runner_up_score (ambiguity signal)
    matched_terms: int    # how many patterns fired for the winner


def classify(message: str) -> ClassificationResult:
    """Score every query type and return the winner plus ambiguity info."""
    text = message.lower()
    scores: dict[QueryType, float] = {qt: 0.0 for qt in QueryType}
    matches: dict[QueryType, int] = {qt: 0 for qt in QueryType}

    for qt, patterns in RULES.items():
        for pattern, weight in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                scores[qt] += weight
                matches[qt] += 1

    # Sort descending by score
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    winner, winner_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0

    # If nothing matched at all, fall back to general_enquiry
    if winner_score == 0.0:
        return ClassificationResult(
            query_type=QueryType.GENERAL_ENQUIRY,
            score=0.0,
            margin=0.0,
            matched_terms=0,
        )

    return ClassificationResult(
        query_type=winner,
        score=winner_score,
        margin=winner_score - runner_up_score,
        matched_terms=matches[winner],
    )
