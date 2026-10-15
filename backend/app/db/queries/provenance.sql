-- Controlled source provenance (migration 037). Written only by verified
-- importers, in the same transaction that publishes the source version.

-- name: upsert_source_provenance
-- Serialize metadata changes with source publication and research scope checks.
WITH locked_source AS MATERIALIZED (
    SELECT source_id FROM sources WHERE source_id = %(source_id)s FOR UPDATE
)
INSERT INTO source_provenance (
    source_id, source_category, classification_origin, publisher, source_url,
    edition, geography, occupation_id, published_at, acquired_at, attribution,
    content_hash, title, institution, issuing_unit, document_roles, applicability,
    applicability_status
) SELECT
    source_id, %(source_category)s, %(classification_origin)s, %(publisher)s,
    %(source_url)s, %(edition)s, %(geography)s, %(occupation_id)s, %(published_at)s,
    %(acquired_at)s, %(attribution)s, %(content_hash)s, %(title)s, %(institution)s,
    %(issuing_unit)s, %(document_roles)s, %(applicability)s, %(applicability_status)s
FROM locked_source
WHERE true
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
    title = EXCLUDED.title,
    institution = EXCLUDED.institution,
    issuing_unit = EXCLUDED.issuing_unit,
    document_roles = EXCLUDED.document_roles,
    applicability = EXCLUDED.applicability,
    applicability_status = EXCLUDED.applicability_status,
    metadata_revision = source_provenance.metadata_revision + 1,
    updated_at = now()
WHERE (source_provenance.source_category, source_provenance.classification_origin,
       source_provenance.publisher, source_provenance.source_url, source_provenance.edition,
       source_provenance.geography, source_provenance.occupation_id,
       source_provenance.published_at, source_provenance.acquired_at,
       source_provenance.attribution, source_provenance.content_hash,
       source_provenance.title, source_provenance.institution, source_provenance.issuing_unit,
       source_provenance.document_roles, source_provenance.applicability,
       source_provenance.applicability_status)
    IS DISTINCT FROM
      (EXCLUDED.source_category, EXCLUDED.classification_origin, EXCLUDED.publisher,
       EXCLUDED.source_url, EXCLUDED.edition, EXCLUDED.geography, EXCLUDED.occupation_id,
       EXCLUDED.published_at, EXCLUDED.acquired_at, EXCLUDED.attribution, EXCLUDED.content_hash,
       EXCLUDED.title, EXCLUDED.institution, EXCLUDED.issuing_unit, EXCLUDED.document_roles,
       EXCLUDED.applicability, EXCLUDED.applicability_status);
