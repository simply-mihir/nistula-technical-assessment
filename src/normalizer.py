"""
Normalisation layer.

Every channel (WhatsApp, Booking.com, Airbnb, Instagram, direct) eventually
sends us slightly different payloads. For this assessment the inbound shape
is already uniform, so normalisation mostly means:
  - generating the message_id
  - copying fields onto the unified schema
  - attaching the query_type

In production this is also where channel-specific quirks get smoothed out
(e.g. Booking.com nesting guest under a 'customer' object, Instagram using
'sender_id' instead of 'guest_name', etc.).
"""
from __future__ import annotations

from .classifier import classify
from .models import InboundMessage, UnifiedMessage


def normalize(inbound: InboundMessage) -> tuple[UnifiedMessage, "ClassificationResult"]:
    """Turn an InboundMessage into a UnifiedMessage and return the classifier result."""
    classification = classify(inbound.message)

    unified = UnifiedMessage(
        source=inbound.source,
        guest_name=inbound.guest_name,
        message_text=inbound.message,
        timestamp=inbound.timestamp,
        booking_ref=inbound.booking_ref,
        property_id=inbound.property_id,
        query_type=classification.query_type,
    )
    return unified, classification
