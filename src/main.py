"""
FastAPI app for the Nistula message handler.

Endpoints:
  GET  /              - minimal in-browser tester (dark mode)
  GET  /health        - liveness probe
  POST /webhook/message - the actual handler

Run locally:
  uvicorn src.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from anthropic import APIError, APITimeoutError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from .claude_client import ClaudeClient
from .confidence import breakdown_to_dict, score
from .models import (
    Action,
    ErrorResponse,
    HandlerResponse,
    InboundMessage,
)
from .normalizer import normalize

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("nistula")

app = FastAPI(
    title="Nistula Guest Message Handler",
    description="Webhook that normalises, classifies, and AI-drafts guest replies.",
    version="1.0.0",
)

# Lazy client - so tests can patch the env without crashing import.
_claude: ClaudeClient | None = None


def get_claude() -> ClaudeClient:
    global _claude
    if _claude is None:
        _claude = ClaudeClient()
    return _claude


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "nistula-message-handler"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Minimal dark-mode tester. Loaded from static/index.html."""
    here = Path(__file__).resolve().parent.parent / "static" / "index.html"
    if not here.exists():
        return "<h1>Nistula Message Handler</h1><p>POST /webhook/message</p>"
    return here.read_text(encoding="utf-8")


@app.post(
    "/webhook/message",
    response_model=HandlerResponse,
    responses={
        400: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def handle_message(inbound: InboundMessage) -> HandlerResponse:
    """
    1. Normalise + classify
    2. Send to Claude with property context
    3. Score confidence
    4. Map score -> action
    """
    logger.info(
        "Inbound | source=%s guest=%s property=%s msg=%r",
        inbound.source.value,
        inbound.guest_name,
        inbound.property_id,
        inbound.message[:80],
    )

    unified, cls_result = normalize(inbound)
    logger.info(
        "Classified | id=%s query_type=%s margin=%.2f matches=%d",
        unified.message_id,
        unified.query_type.value,
        cls_result.margin,
        cls_result.matched_terms,
    )

    try:
        reply, self_rating = await get_claude().draft_reply(unified)
    except APITimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Claude API timed out. Please retry.",
        )
    except APIError as e:
        # Upstream issue - return a clean 502 and log details server-side.
        logger.exception("Claude API failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude API error: {type(e).__name__}",
        )
    except RuntimeError as e:
        # e.g. missing API key
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    breakdown = score(unified, cls_result, self_rating)
    logger.info(
        "Scored | id=%s final=%.2f action=%s caps=%s",
        unified.message_id,
        breakdown.final_score,
        breakdown.action.value,
        breakdown.caps_applied,
    )

    return HandlerResponse(
        message_id=unified.message_id,
        query_type=unified.query_type,
        drafted_reply=reply,
        confidence_score=breakdown.final_score,
        action=breakdown.action,
        confidence_breakdown=breakdown_to_dict(breakdown),
    )


# ---------------------------------------------------------------------------
# Generic exception handler (so unexpected errors don't leak stack traces)
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": str(exc)},
    )
