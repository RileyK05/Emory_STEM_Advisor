-- Documents: sources, locators, chunks, and the keyword (full-text) index.

CREATE TABLE sources (
    source_id       uuid PRIMARY KEY,
    collection_id   uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    filename        text NOT NULL,
    content_type    text NOT NULL,
    file_hash       text NOT NULL,
    raw_path        text NOT NULL,
    status          text NOT NULL
        CHECK (status IN ('uploaded','parsed','chunked','indexed','failed')),
    error_message   text,
    chunker_version text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (collection_id, file_hash)
);

CREATE TABLE locators (
    locator_id   uuid PRIMARY KEY,
    source_id    uuid NOT NULL REFERENCES sources ON DELETE CASCADE,
    ordinal      int  NOT NULL,
    locator_type text NOT NULL,
    label        text NOT NULL,
    start_char   int  NOT NULL,
    end_char     int  NOT NULL,
    CHECK (end_char > start_char),
    UNIQUE (source_id, ordinal)
);

CREATE TABLE chunks (
    chunk_id      uuid PRIMARY KEY,
    source_id     uuid NOT NULL REFERENCES sources ON DELETE CASCADE,
    locator_id    uuid NOT NULL REFERENCES locators ON DELETE CASCADE,
    chunk_index   int  NOT NULL,
    start_char    int  NOT NULL,
    end_char      int  NOT NULL,
    token_count   int  NOT NULL,
    text          text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (source_id, chunk_index)
);
CREATE INDEX chunks_search_idx ON chunks USING gin (search_vector);
CREATE INDEX chunks_locator_idx ON chunks (locator_id);
