CREATE TABLE retrieval_gaps (
    gap_id uuid PRIMARY KEY,
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    collection_id uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    query text NOT NULL CHECK (length(query) BETWEEN 1 AND 20000),
    normalized_query text NOT NULL CHECK (length(normalized_query) BETWEEN 1 AND 20000),
    query_key char(64) NOT NULL CHECK (query_key ~ '^[a-f0-9]{64}$'),
    scope_source_ids uuid[],
    state text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    occurrence_count integer NOT NULL DEFAULT 0 CHECK (occurrence_count >= 0),
    recurrence_count integer NOT NULL DEFAULT 0 CHECK (recurrence_count >= 0),
    successful_recurrence_count integer NOT NULL DEFAULT 0
        CHECK (successful_recurrence_count >= 0),
    first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolution_source_ids uuid[],
    resolution_note text CHECK (resolution_note IS NULL OR length(resolution_note) <= 1000),
    resolved_by text CHECK (resolved_by IS NULL OR length(resolved_by) BETWEEN 1 AND 200),
    resolved_at timestamptz,
    CHECK (
        (state = 'open' AND resolution_source_ids IS NULL AND resolution_note IS NULL
         AND resolved_by IS NULL AND resolved_at IS NULL)
        OR
        (state = 'resolved' AND resolution_source_ids IS NOT NULL
         AND cardinality(resolution_source_ids) > 0 AND resolved_by IS NOT NULL
         AND resolved_at IS NOT NULL)
    )
);
CREATE UNIQUE INDEX retrieval_gaps_identity_uq
    ON retrieval_gaps (workspace_key, collection_id, query_key);
CREATE INDEX retrieval_gaps_workspace_oldest_idx
    ON retrieval_gaps (workspace_key, state, first_seen_at, gap_id);

CREATE TABLE gap_resolutions (
    gap_id uuid NOT NULL REFERENCES retrieval_gaps ON DELETE CASCADE,
    revision integer NOT NULL CHECK (revision >= 1),
    source_ids uuid[] NOT NULL CHECK (cardinality(source_ids) > 0),
    note text NOT NULL CHECK (length(note) <= 1000),
    resolved_by text NOT NULL CHECK (length(resolved_by) BETWEEN 1 AND 200),
    resolved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (gap_id, revision)
);

CREATE TABLE gap_observations (
    workspace_key text NOT NULL CHECK (length(workspace_key) BETWEEN 1 AND 200),
    trace_id uuid NOT NULL REFERENCES retrieval_traces ON DELETE CASCADE,
    gap_id uuid NOT NULL REFERENCES retrieval_gaps ON DELETE CASCADE,
    event_kind text NOT NULL CHECK (
        event_kind IN ('retrieval_refusal', 'generation_withheld', 'retrieval_success')
    ),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (workspace_key, trace_id, event_kind)
);
CREATE INDEX gap_observations_gap_idx ON gap_observations (gap_id, created_at);
