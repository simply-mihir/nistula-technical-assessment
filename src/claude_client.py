"""
Claude API client.

Thin wrapper that:
  - Builds a system prompt with property context and query type.
  - Asks Claude to draft a reply (no JSON parsing needed - we want plain text).
  - Returns the reply plus a "self-rated" certainty score we extract from
    Claude's own assessment in a structured tail.

Keeping this in its own module makes it easy to swap providers later
(OpenAI, Gemini, in-house model) without touching the webhook.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from anthropic import AsyncAnthropic, APIError, APITimeoutError

from .models import QueryType, UnifiedMessage
from .property_context import format_property_for_prompt

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """You are the AI concierge for Nistula, a luxury villa rental company in Goa, India. You draft reply messages on behalf of human hosts.

VOICE & STYLE
- Warm, professional, concise (2-4 sentences typical, longer only when truly needed).
- Address the guest by first name.
- Use plain prose. No bullet points unless listing 3+ items the guest explicitly asked for.
- Match the channel: WhatsApp = friendly + brief; booking_com / airbnb = polite + slightly more formal; direct = warmest.
- Never invent facts. If the property context doesn't have an answer, say you'll check with the team.
- Never quote a price or availability that contradicts the property context.
- For complaints: acknowledge, apologise sincerely, never argue, never deflect, never promise refunds without human approval.

PROPERTY CONTEXT
{property_block}

INCOMING MESSAGE
Channel: {source}
Guest: {guest_name}
Classified as: {query_type}
Booking reference: {booking_ref}

GUEST MESSAGE
\"\"\"
{message_text}
\"\"\"

OUTPUT FORMAT
Reply with ONLY the message you would send to the guest. No preamble like "Here is the reply:". No sign-off other than a short courteous line if natural. At the very end, on its own line, append exactly:

[SELF_RATING: X.XX]

where X.XX is your own 0.00-1.00 estimate of how complete and on-policy your reply is. Use 0.9+ only when every fact in your reply comes directly from the property context. Use 0.5 or lower if you had to be vague or defer to a human."""


class ClaudeClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    async def draft_reply(self, msg: UnifiedMessage) -> tuple[str, float]:
        """
        Draft a guest reply. Returns (reply_text, self_rating).

        self_rating is Claude's own estimate of reply quality, extracted from the
        trailing [SELF_RATING: X.XX] tag. If we can't parse it we return 0.5 -
        a neutral value that the confidence scorer can downgrade further.
        """
        prompt = SYSTEM_PROMPT_TEMPLATE.format(
            property_block=format_property_for_prompt(msg.property_id),
            source=msg.source.value,
            guest_name=msg.guest_name,
            query_type=msg.query_type.value,
            booking_ref=msg.booking_ref or "(none provided)",
            message_text=msg.message_text,
        )

        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=512,
                system=prompt,
                messages=[{
                    "role": "user",
                    "content": "Draft the reply now.",
                }],
            )
        except APITimeoutError as e:
            logger.error("Claude API timeout: %s", e)
            raise
        except APIError as e:
            logger.error("Claude API error: %s", e)
            raise

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()

        reply, self_rating = _split_reply_and_rating(text)
        return reply, self_rating


def _split_reply_and_rating(text: str) -> tuple[str, float]:
    """
    Pull the [SELF_RATING: X.XX] tag off the end, return (reply, rating).
    Defaults to 0.5 if the tag is missing or malformed.
    """
    match = re.search(r"\[SELF_RATING:\s*([0-9]*\.?[0-9]+)\s*\]\s*$", text)
    if not match:
        return text.strip(), 0.5

    try:
        rating = float(match.group(1))
        rating = max(0.0, min(1.0, rating))
    except ValueError:
        rating = 0.5

    reply = text[: match.start()].strip()
    return reply, rating
