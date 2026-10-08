-- Retrieval traces: the audit record. Field names mirror the frontend types.

CREATE TABLE retrieval_traces (
    trace_id         uuid PRIMARY KEY,
    collection_id    uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    query            text NOT NULL,
    normalized_query text NOT NULL,
    config_version   int  NOT NULL,
    embed_model      text,
    refused          boolean NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trace_seams (
    trace_id        uuid NOT NULL REFERENCES retrieval_traces ON DELETE CASCADE,
    seam            text NOT NULL,
    enabled         boolean NOT NULL,
    state           text NOT NULL,
    reason          text,
    candidate_count int NOT NULL,
    PRIMARY KEY (trace_id, seam)
);

-- chunk_id has no FK on purpose: a trace must survive re-ingestion.
CREATE TABLE trace_contributions (
    trace_id         uuid NOT NULL REFERENCES retrieval_traces ON DELETE CASCADE,
    chunk_id         uuid NOT NULL,
    seams            text[] NOT NULL,
    normalized_score double precision NOT NULL,
    source_slot      int NOT NULL,
    rank             int NOT NULL,
    PRIMARY KEY (trace_id, chunk_id)
);
