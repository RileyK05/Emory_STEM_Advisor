-- Owner-controlled conversation archive frees the active-conversation quota
-- without destroying immutable turns or dependent evidence.
ALTER TABLE conversations ADD COLUMN archived_at timestamptz;

CREATE INDEX conversations_workspace_active_idx
    ON conversations (workspace_key, updated_at DESC, conversation_id)
    WHERE archived_at IS NULL;

CREATE INDEX conversations_workspace_archived_idx
    ON conversations (workspace_key, archived_at DESC, conversation_id)
    WHERE archived_at IS NOT NULL;
