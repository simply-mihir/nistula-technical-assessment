"""
Pydantic models for the Nistula message handler.

Two layers of schemas:
1. InboundMessage  -> what arrives at the webhook (raw, channel-specific).
2. UnifiedMessage  -> the normalised, internal representation we hand to the AI.
3. HandlerResponse -> what we return to the caller.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums - keeping the universe of valid values explicit and self-documenting.
# ---------------------------------------------------------------------------
class Source(str, Enum):
    WHATSAPP = "whatsapp"
    BOOKING_COM = "booking_com"
    AIRBNB = "airbnb"
    INSTAGRAM = "instagram"
    DIRECT = "direct"


class QueryType(str, Enum):
    PRE_SALES_AVAILABILITY = "pre_sales_availability"
    PRE_SALES_PRICING = "pre_sales_pricing"
    POST_SALES_CHECKIN = "post_sales_checkin"
    SPECIAL_REQUEST = "special_request"
    COMPLAINT = "complaint"
    GENERAL_ENQUIRY = "general_enquiry"


class Action(str, Enum):
    AUTO_SEND = "auto_send"
    AGENT_REVIEW = "agent_review"
    ESCALATE = "escalate"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class InboundMessage(BaseModel):
    """Payload accepted by POST /webhook/message."""
    source: Source
    guest_name: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    timestamp: datetime
    booking_ref: Optional[str] = Field(default=None, max_length=100)
    property_id: Optional[str] = Field(default=None, max_length=100)

    @field_validator("message")
    @classmethod
    def strip_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty after stripping whitespace")
        return v


class UnifiedMessage(BaseModel):
    """Normalised internal representation - one shape regardless of channel."""
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    source: Source
    guest_name: str
    message_text: str
    timestamp: datetime
    booking_ref: Optional[str] = None
    property_id: Optional[str] = None
    query_type: QueryType


class HandlerResponse(BaseModel):
    """Final API response shape."""
    message_id: str
    query_type: QueryType
    drafted_reply: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    action: Action
    # Helpful debug payload (kept tiny - reviewer can inspect why a score landed where it did)
    confidence_breakdown: Optional[dict] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
