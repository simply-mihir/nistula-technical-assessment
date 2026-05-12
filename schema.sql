-- ============================================================================
-- Nistula Unified Messaging Platform — PostgreSQL Schema
-- ============================================================================
-- Designed for PostgreSQL 14+ (uses gen_random_uuid, JSONB, GENERATED columns).
--
-- Reading order:
--   1. properties            — villas in the portfolio
--   2. guests                — one logical person across all channels
--   3. guest_channel_identities — maps each channel handle to a guest
--   4. reservations          — bookings tied to a property + guest
--   5. conversations         — message threads (guest × channel × reservation)
--   6. messages              — every inbound and outbound message
--   7. message_edits         — audit trail when an agent edits an AI draft
--   8. (views and indexes follow)
--
-- Design philosophy:
--   - Strong typing via enums and FKs over free-text fields.
--   - JSONB only where the shape is genuinely open-ended (channel metadata,
--     confidence breakdown) — everything queryable gets a real column.
--   - Soft-delete via deleted_at; never DELETE from operational tables.
--   - All timestamps are TIMESTAMPTZ in UTC.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive emails


-- ============================================================================
-- ENUMS
-- ============================================================================
-- Keeping these as ENUMs (not lookup tables) because:
--   1. The set of values is small, stable, and code-coupled (validators use them).
--   2. They're constrained at the type level — no orphan strings in the table.
--   3. Adding a new value is a one-line migration: ALTER TYPE ... ADD VALUE.

CREATE TYPE channel_source AS ENUM (
    'whatsapp',
    'booking_com',
    'airbnb',
    'instagram',
    'direct'
);

CREATE TYPE message_direction AS ENUM ('inbound', 'outbound');

-- How an outbound message was produced.
--   ai_auto_sent     — confidence > 0.85, no human touched it
--   ai_agent_sent    — AI drafted, agent reviewed (possibly edited), then sent
--   agent_authored   — agent wrote it from scratch, no AI involvement
CREATE TYPE outbound_origin AS ENUM (
    'ai_auto_sent',
    'ai_agent_sent',
    'agent_authored'
);

CREATE TYPE query_type AS ENUM (
    'pre_sales_availability',
    'pre_sales_pricing',
    'post_sales_checkin',
    'special_request',
    'complaint',
    'general_enquiry'
);

CREATE TYPE handler_action AS ENUM (
    'auto_send',
    'agent_review',
    'escalate'
);

CREATE TYPE reservation_status AS ENUM (
    'enquiry',
    'confirmed',
    'checked_in',
    'completed',
    'cancelled'
);


-- ============================================================================
-- 1. PROPERTIES
-- ============================================================================
-- A small portfolio today, but the system is designed to scale to many
-- properties. property_code is the human-readable id used in URLs and emails;
-- the UUID is the stable internal id used by every FK.

CREATE TABLE properties (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_code    TEXT NOT NULL UNIQUE,           -- e.g. 'villa-b1'
    name             TEXT NOT NULL,                  -- e.g. 'Villa B1'
    location         TEXT NOT NULL,                  -- e.g. 'Assagao, North Goa'
    bedrooms         INT  NOT NULL CHECK (bedrooms > 0),
    max_guests       INT  NOT NULL CHECK (max_guests > 0),
    base_rate_inr    INT  NOT NULL CHECK (base_rate_inr >= 0),
    base_rate_covers_guests INT NOT NULL CHECK (base_rate_covers_guests > 0),
    extra_guest_rate_inr    INT NOT NULL CHECK (extra_guest_rate_inr >= 0),
    check_in_time    TIME NOT NULL,
    check_out_time   TIME NOT NULL,
    -- Free-form facts (wifi, caretaker, chef, cancellation, etc.) — JSONB
    -- because the shape varies per property and grows over time.
    facts            JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ
);


-- ============================================================================
-- 2. GUESTS
-- ============================================================================
-- ONE row per logical person, regardless of how many channels they message
-- from. This is the cross-channel identity. Channel-specific handles
-- (WhatsApp phone, Airbnb id, etc.) live in guest_channel_identities so we
-- never have to alter this table to support a new channel.
--
-- Email and phone are nullable because a first-time Instagram DM gives us
-- neither — we still create a guest row so the conversation has a home.

CREATE TABLE guests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name     TEXT NOT NULL,                  -- best-known name
    email            CITEXT,                         -- case-insensitive
    phone_e164       TEXT,                           -- e.g. '+919876543210'
    preferred_language TEXT NOT NULL DEFAULT 'en',   -- ISO 639-1
    notes            TEXT,                           -- agent free-text
    -- Aggregate signals used by AI prompt assembly and by ops dashboards.
    -- Updated by triggers or async jobs — never written by the app directly.
    total_messages_received INT NOT NULL DEFAULT 0,
    total_reservations      INT NOT NULL DEFAULT 0,
    last_message_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ
);

-- Partial unique indexes: email/phone only have to be unique among the rows
-- where they actually exist (otherwise nullable+unique blocks new anon guests).
CREATE UNIQUE INDEX guests_email_unique
    ON guests (email) WHERE email IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX guests_phone_unique
    ON guests (phone_e164) WHERE phone_e164 IS NOT NULL AND deleted_at IS NULL;


-- ============================================================================
-- 3. GUEST_CHANNEL_IDENTITIES
-- ============================================================================
-- The map from (channel, channel_handle) → guest_id. This is what lets us
-- collapse 'Rahul on WhatsApp' and 'Rahul on Booking.com' into one guest
-- profile without baking channel-specific columns into the guests table.
--
-- The handle is whatever uniquely identifies the guest on that channel:
--   whatsapp     → E.164 phone number ('+919876543210')
--   booking_com  → Booking.com guest id
--   airbnb       → Airbnb user id (their public hash)
--   instagram    → Instagram username or scoped id
--   direct       → email address

CREATE TABLE guest_channel_identities (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_id         UUID NOT NULL REFERENCES guests(id) ON DELETE CASCADE,
    channel          channel_source NOT NULL,
    channel_handle   TEXT NOT NULL,
    -- Per-channel metadata (display name on that channel, profile picture
    -- url, locale) — varies wildly between channels.
    metadata         JSONB NOT NULL DEFAULT '{}'::JSONB,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel, channel_handle)
);

CREATE INDEX guest_channel_identities_guest_id_idx
    ON guest_channel_identities (guest_id);


-- ============================================================================
-- 4. RESERVATIONS
-- ============================================================================
-- A booking. booking_ref is the human-facing id (the one printed on emails);
-- the UUID is the stable internal one.

CREATE TABLE reservations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_ref      TEXT NOT NULL UNIQUE,           -- e.g. 'NIS-2024-0891'
    guest_id         UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    property_id      UUID NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,
    status           reservation_status NOT NULL DEFAULT 'enquiry',
    check_in_date    DATE NOT NULL,
    check_out_date   DATE NOT NULL,
    adults           INT  NOT NULL DEFAULT 2 CHECK (adults > 0),
    children         INT  NOT NULL DEFAULT 0 CHECK (children >= 0),
    total_amount_inr INT  CHECK (total_amount_inr >= 0),
    source_channel   channel_source,                 -- where the booking came in
    booked_at        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (check_out_date > check_in_date)
);

CREATE INDEX reservations_guest_id_idx     ON reservations (guest_id);
CREATE INDEX reservations_property_id_idx  ON reservations (property_id);
CREATE INDEX reservations_dates_idx        ON reservations (property_id, check_in_date, check_out_date);


-- ============================================================================
-- 5. CONVERSATIONS
-- ============================================================================
-- A thread groups all messages between a guest and the business on a single
-- channel. We deliberately scope by (guest_id, channel) rather than per
-- reservation, because:
--   - A guest's pre-sales enquiry on WhatsApp and their post-stay follow-up
--     on the same number are the same conversation to the agent.
--   - If a guest books two stays via the same WhatsApp number, you still
--     want a single conversational thread; reservation_id on the message
--     row is what disambiguates context.

CREATE TABLE conversations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_id         UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    channel          channel_source NOT NULL,
    property_id      UUID REFERENCES properties(id) ON DELETE SET NULL,
    -- The reservation the conversation is "primarily about" right now —
    -- nullable for pre-sales and general enquiries.
    active_reservation_id UUID REFERENCES reservations(id) ON DELETE SET NULL,
    -- Status helps the agent dashboard surface what needs attention.
    is_open          BOOLEAN NOT NULL DEFAULT TRUE,
    last_message_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_inbound_at  TIMESTAMPTZ,
    last_outbound_at TIMESTAMPTZ,
    unread_count     INT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guest_id, channel)
);

CREATE INDEX conversations_open_idx
    ON conversations (last_message_at DESC) WHERE is_open;


-- ============================================================================
-- 6. MESSAGES
-- ============================================================================
-- Every inbound and outbound message lives here. Channel-agnostic by design;
-- channel-specific quirks go into raw_payload (JSONB).
--
-- The AI columns (query_type, confidence_score, confidence_breakdown,
-- handler_action, ai_drafted_reply) are populated for INBOUND messages —
-- they capture what the AI thought of the message and what reply it drafted.
--
-- The outbound_origin column on OUTBOUND messages records whether the
-- delivered text was the AI draft auto-sent, an AI draft an agent edited
-- and sent, or something the agent wrote from scratch.

CREATE TABLE messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    guest_id         UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    reservation_id   UUID REFERENCES reservations(id) ON DELETE SET NULL,
    direction        message_direction NOT NULL,
    channel          channel_source NOT NULL,
    -- The actual text the guest sent or the agent/AI delivered.
    body             TEXT NOT NULL,
    -- The raw provider payload (whatsapp webhook envelope, booking.com xml,
    -- etc.) so we can replay/debug without re-fetching upstream.
    raw_payload      JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- ---- AI metadata: only set on INBOUND rows --------------------------
    query_type             query_type,
    confidence_score       NUMERIC(4,3) CHECK (confidence_score BETWEEN 0 AND 1),
    confidence_breakdown   JSONB,                    -- full signal breakdown
    handler_action         handler_action,           -- what we decided to do
    ai_drafted_reply       TEXT,                     -- the draft we generated
    ai_model               TEXT,                     -- e.g. 'claude-sonnet-4-20250514'
    ai_latency_ms          INT,

    -- ---- Outbound metadata: only set on OUTBOUND rows --------------------
    outbound_origin        outbound_origin,
    -- If this outbound row was sent in response to a specific inbound row.
    in_reply_to_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    -- Which agent sent or approved it (NULL for ai_auto_sent).
    sent_by_agent_id       UUID,                     -- FK to agents table (not modelled here)

    -- ---- Timestamps -----------------------------------------------------
    -- occurred_at = when the guest sent it / when we sent it. May differ
    -- from created_at when ingesting historical data.
    occurred_at      TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at     TIMESTAMPTZ,                    -- channel ack
    read_at          TIMESTAMPTZ,                    -- channel read receipt

    -- Direction-conditional consistency: AI fields belong only on inbound
    -- rows; outbound_origin belongs only on outbound rows. Enforced here
    -- so bad data can never sneak in.
    CONSTRAINT ai_fields_only_inbound CHECK (
        direction = 'inbound' OR (
            query_type IS NULL AND confidence_score IS NULL
            AND confidence_breakdown IS NULL AND handler_action IS NULL
        )
    ),
    CONSTRAINT outbound_origin_only_outbound CHECK (
        direction = 'outbound' OR outbound_origin IS NULL
    )
);

-- Hot read paths:
--   - "show me this conversation in order" → (conversation_id, occurred_at)
--   - "all complaints in the last 24h"     → (query_type, occurred_at)
--   - "everything escalated"               → (handler_action) partial
CREATE INDEX messages_conversation_time_idx
    ON messages (conversation_id, occurred_at DESC);

CREATE INDEX messages_guest_time_idx
    ON messages (guest_id, occurred_at DESC);

CREATE INDEX messages_query_type_time_idx
    ON messages (query_type, occurred_at DESC)
    WHERE direction = 'inbound';

CREATE INDEX messages_escalated_idx
    ON messages (occurred_at DESC)
    WHERE handler_action = 'escalate';

CREATE INDEX messages_reservation_id_idx
    ON messages (reservation_id) WHERE reservation_id IS NOT NULL;


-- ============================================================================
-- 7. MESSAGE_EDITS
-- ============================================================================
-- When an agent edits an AI draft before sending, we keep the before/after
-- so we can:
--   - Show the diff in the agent UI
--   - Mine edits as a training signal for prompt tuning
--   - Audit who changed what
--
-- One inbound message can have many drafts (re-generations), and the final
-- sent text lives on the outbound message row. This table records the
-- intermediate edits.

CREATE TABLE message_edits (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The outbound message whose body is the result of this edit.
    outbound_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    -- The AI draft this edit started from (lives on the inbound message row).
    inbound_message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    agent_id         UUID NOT NULL,                  -- FK to agents (not modelled)
    original_text    TEXT NOT NULL,                  -- AI's draft
    edited_text      TEXT NOT NULL,                  -- what was actually sent
    edit_reason      TEXT,                           -- optional agent note
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX message_edits_outbound_idx ON message_edits (outbound_message_id);


-- ============================================================================
-- VIEWS for common ops queries
-- ============================================================================
-- A single view that gives the agent dashboard everything it needs to render
-- the "needs attention" inbox row — guest name, last message, action, and
-- whether the SLA clock is running.

CREATE OR REPLACE VIEW v_inbox_pending AS
SELECT
    m.id                           AS message_id,
    m.occurred_at,
    c.id                           AS conversation_id,
    g.id                           AS guest_id,
    g.display_name                 AS guest_name,
    p.property_code,
    m.channel,
    m.query_type,
    m.confidence_score,
    m.handler_action,
    m.body                         AS guest_message,
    m.ai_drafted_reply,
    -- Minutes since the guest sent this (for SLA badge in the UI)
    EXTRACT(EPOCH FROM (now() - m.occurred_at)) / 60.0 AS minutes_waiting
FROM messages m
JOIN conversations c  ON c.id = m.conversation_id
JOIN guests g         ON g.id = m.guest_id
LEFT JOIN properties p ON p.id = c.property_id
WHERE m.direction = 'inbound'
  AND m.handler_action IN ('agent_review', 'escalate')
  -- The reply hasn't gone out yet
  AND NOT EXISTS (
      SELECT 1 FROM messages out_m
      WHERE out_m.in_reply_to_message_id = m.id
        AND out_m.direction = 'outbound'
  )
ORDER BY
    (m.handler_action = 'escalate') DESC,   -- escalations first
    m.occurred_at ASC;                      -- oldest waiting first


-- ============================================================================
-- DESIGN NOTE — Hardest decision
-- ============================================================================
-- The hardest call was the split between `guests` and `guest_channel_identities`.
--
-- The naive design puts whatsapp_number, booking_com_id, airbnb_id and so on
-- as columns on `guests`. It's faster to write, faster to read (no JOIN),
-- and feels obvious — until the day product wants to add Telegram, or until
-- a guest changes their WhatsApp number, or until you discover the same
-- person has been messaging from three different channels and you have
-- three orphan guest rows that should have been one.
--
-- Splitting identity from the channel handle costs a JOIN on every lookup
-- and a small write amplification, but it buys three things that matter
-- more in the long run:
--   1. New channels are config + a migration, never a schema rewrite.
--   2. Guest merging is a single UPDATE on guest_channel_identities,
--      not a destructive UPDATE across half the schema.
--   3. The cross-channel `guests.total_messages_received` aggregate is
--      meaningful — you can answer "how many times have we heard from
--      this person across everywhere?" with one COUNT.
--
-- The decision is reversible if proven wrong, but the reverse would be
-- much more painful, so I optimised for the harder migration not happening.
