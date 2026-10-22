-- Migration 044: Conversation context epoch and memory reset boundary (Item 6).

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS context_epoch integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS context_boundary_sequence bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS context_reset_at timestamp with time zone;

ALTER TABLE conversation_summaries
    ADD COLUMN IF NOT EXISTS context_epoch integer NOT NULL DEFAULT 0;

