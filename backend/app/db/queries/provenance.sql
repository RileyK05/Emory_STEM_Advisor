-- Controlled source provenance (migration 037). Written only by verified
-- importers, in the same transaction that publishes the source version.

-- name: upsert_source_provenance
INSERT INTO source_provenance (
    source_id, source_category, classification_origin, publisher, source_url,
    edition, geography, occupation_id, published_at, acquired_at, attribution,
    content_hash
) VALUES (
    %(source_id)s, %(source_category)s, %(classification_origin)s, %(publisher)s,
    %(source_url)s, %(edition)s, %(geography)s, %(occupation_id)s, %(published_at)s,
    %(acquired_at)s, %(attribution)s, %(content_hash)s
)
ON CONFLICT (source_id) DO UPDATE SET
    source_category = EXCLUDED.source_category,
    classification_origin = EXCLUDED.classification_origin,
    publisher = EXCLUDED.publisher,
    source_url = EXCLUDED.source_url,
    edition = EXCLUDED.edition,
    geography = EXCLUDED.geography,
    occupation_id = EXCLUDED.occupation_id,
    published_at = EXCLUDED.published_at,
    acquired_at = EXCLUDED.acquired_at,
    attribution = EXCLUDED.attribution,
    content_hash = EXCLUDED.content_hash,
    updated_at = now();
