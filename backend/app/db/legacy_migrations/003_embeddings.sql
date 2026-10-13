-- Embeddings: one vector per chunk per model. Unsized vector column so
-- different models can coexist; no ANN index (exact scan is fine at this size).

CREATE TABLE chunk_embeddings (
    chunk_id   uuid NOT NULL REFERENCES chunks ON DELETE CASCADE,
    model      text NOT NULL,
    dim        int  NOT NULL,
    embedding  vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model),
    CHECK (vector_dims(embedding) = dim)
);
CREATE INDEX chunk_embeddings_model_idx ON chunk_embeddings (model, dim);
