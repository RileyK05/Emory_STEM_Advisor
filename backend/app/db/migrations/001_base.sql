-- Base: the pgvector extension and the collections table.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE collections (
    collection_id uuid PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now()
);
