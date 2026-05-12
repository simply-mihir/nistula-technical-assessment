"""Confidence scorer unit tests."""
from datetime import datetime, timezone

from src.classifier import classify
from src.confidence import score
from src.models import Action, QueryType, UnifiedMessage, Source


def _msg(text: str, query_type: QueryType, property_id: str | None = "villa-b1",
         booking_ref: str | None = "NIS-2024-0891") -> UnifiedMessage:
    return UnifiedMessage(
        source=Source.WHATSAPP,
        guest_name="Test Guest",
        message_text=text,
        timestamp=datetime.now(timezone.utc),
        booking_ref=booking_ref,
        property_id=property_id,
        query_type=query_type,
    )


def test_clear_checkin_scores_high_and_auto_sends():
    text = "What's the WiFi password?"
    cls = classify(text)
    m = _msg(text, cls.query_type)
    b = score(m, cls, claude_self_rating=0.95)
    assert b.final_score >= 0.85
    assert b.action == Action.AUTO_SEND


def test_complaint_is_always_escalated():
    text = "The AC is not working. This is unacceptable."
    cls = classify(text)
    m = _msg(text, cls.query_type)
    b = score(m, cls, claude_self_rating=1.0)
    # Even with perfect Claude rating, complaint must escalate
    assert m.query_type == QueryType.COMPLAINT
    assert b.action == Action.ESCALATE
    assert "complaint_cap" in b.caps_applied
    assert b.final_score <= 0.55


def test_missing_property_context_caps_score():
    text = "Is the villa available next weekend?"
    cls = classify(text)
    m = _msg(text, cls.query_type, property_id="unknown-property-xyz")
    b = score(m, cls, claude_self_rating=0.95)
    assert "missing_context_cap" in b.caps_applied
    assert b.final_score <= 0.75


def test_low_claude_self_rating_caps_score():
    text = "What's the WiFi password?"
    cls = classify(text)
    m = _msg(text, cls.query_type)
    b = score(m, cls, claude_self_rating=0.20)
    assert "low_claude_self_cap" in b.caps_applied
    assert b.final_score <= 0.55


def test_ambiguous_message_goes_to_agent_review():
    # Very short / vague - low classifier margin, low clarity
    text = "Hi, question."
    cls = classify(text)
    m = _msg(text, cls.query_type)
    b = score(m, cls, claude_self_rating=0.7)
    # Should not auto-send something this thin
    assert b.action != Action.AUTO_SEND


def test_breakdown_is_serialisable():
    text = "What's the WiFi password?"
    cls = classify(text)
    m = _msg(text, cls.query_type)
    b = score(m, cls, claude_self_rating=0.9)
    # Should have all expected keys
    from src.confidence import breakdown_to_dict
    d = breakdown_to_dict(b)
    for key in ("classifier_certainty", "context_completeness", "message_clarity",
                "claude_self_rating", "raw_score", "final_score", "action"):
        assert key in d
