"""
End-to-end webhook tests.

The Claude client is patched so tests don't make real API calls and don't
need an API key. We verify routing, normalisation, and action mapping for
the 5 input shapes the brief calls out (plus a malformed payload).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import main as main_module
from src.main import app


class FakeClaude:
    """Mock that returns a canned reply + self-rating, no network calls."""
    def __init__(self, reply="Hi Rahul! Yes, the villa is available.", self_rating=0.92):
        self._reply = reply
        self._rating = self_rating

    async def draft_reply(self, msg):
        return self._reply, self._rating


@pytest.fixture(autouse=True)
def patch_claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(main_module, "get_claude", lambda: fake)
    yield fake


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Required: at least 3 different input tests
# ---------------------------------------------------------------------------
def test_availability_payload_returns_200_and_auto_sends(client, patch_claude):
    r = client.post("/webhook/message", json={
        "source": "whatsapp",
        "guest_name": "Rahul Sharma",
        "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
        "timestamp": "2026-05-05T10:30:00Z",
        "booking_ref": "NIS-2024-0891",
        "property_id": "villa-b1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["query_type"] in {"pre_sales_availability", "pre_sales_pricing"}
    assert "drafted_reply" in body and body["drafted_reply"]
    assert 0.0 <= body["confidence_score"] <= 1.0
    assert body["action"] in {"auto_send", "agent_review"}


def test_checkin_payload(client):
    r = client.post("/webhook/message", json={
        "source": "whatsapp",
        "guest_name": "David Kim",
        "message": "What time can we check in tomorrow and what's the WiFi password?",
        "timestamp": "2026-05-05T11:00:00Z",
        "property_id": "villa-b1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["query_type"] == "post_sales_checkin"


def test_complaint_payload_always_escalates(client, patch_claude):
    r = client.post("/webhook/message", json={
        "source": "whatsapp",
        "guest_name": "Vikram Bose",
        "message": "There is no hot water and we have guests arriving for breakfast in 4 hours. This is unacceptable. I want a refund for tonight.",
        "timestamp": "2026-05-05T03:00:00Z",
        "booking_ref": "NIS-2024-1150",
        "property_id": "villa-b1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["query_type"] == "complaint"
    assert body["action"] == "escalate"
    assert body["confidence_score"] <= 0.55


def test_special_request_payload(client):
    r = client.post("/webhook/message", json={
        "source": "direct",
        "guest_name": "Priya Iyer",
        "message": "Can you arrange an airport transfer from GOI for 4 people at 6pm on Friday?",
        "timestamp": "2026-05-05T09:00:00Z",
        "booking_ref": "NIS-2024-1131",
        "property_id": "villa-b1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["query_type"] == "special_request"


def test_general_enquiry_payload(client):
    r = client.post("/webhook/message", json={
        "source": "instagram",
        "guest_name": "Sneha Rao",
        "message": "Do you allow pets? Is there parking?",
        "timestamp": "2026-05-05T13:00:00Z",
        "property_id": "villa-b1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["query_type"] == "general_enquiry"


# ---------------------------------------------------------------------------
# Validation + error handling
# ---------------------------------------------------------------------------
def test_invalid_source_rejected(client):
    r = client.post("/webhook/message", json={
        "source": "carrier_pigeon",   # not in enum
        "guest_name": "Test",
        "message": "Hi",
        "timestamp": "2026-05-05T10:30:00Z",
    })
    assert r.status_code == 422  # FastAPI validation error


def test_empty_message_rejected(client):
    r = client.post("/webhook/message", json={
        "source": "whatsapp",
        "guest_name": "Test",
        "message": "   ",     # only whitespace
        "timestamp": "2026-05-05T10:30:00Z",
    })
    assert r.status_code == 422


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
