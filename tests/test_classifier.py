"""Classifier unit tests."""
from src.classifier import classify
from src.models import QueryType


def test_availability():
    r = classify("Is the villa available from April 20 to 24?")
    assert r.query_type == QueryType.PRE_SALES_AVAILABILITY


def test_pricing():
    r = classify("What is the rate for 2 adults for 3 nights?")
    assert r.query_type == QueryType.PRE_SALES_PRICING


def test_checkin():
    r = classify("What time can we check in? Also, WiFi password please.")
    assert r.query_type == QueryType.POST_SALES_CHECKIN


def test_special_request():
    r = classify("Can you arrange airport pickup for 4 people at 6pm?")
    assert r.query_type == QueryType.SPECIAL_REQUEST


def test_complaint():
    r = classify("There is no hot water. This is unacceptable. I want a refund.")
    assert r.query_type == QueryType.COMPLAINT
    # Complaints should match strongly - margin > 1
    assert r.margin > 1.0


def test_general():
    r = classify("Do you allow pets? Is there parking?")
    assert r.query_type == QueryType.GENERAL_ENQUIRY


def test_empty_message_falls_back():
    r = classify("xyz random gibberish")
    assert r.query_type == QueryType.GENERAL_ENQUIRY
    assert r.matched_terms == 0
