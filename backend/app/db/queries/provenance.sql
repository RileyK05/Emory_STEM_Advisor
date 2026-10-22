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
    applicability_status, lifecycle_status
) SELECT
    source_id, %(source_category)s, %(classification_origin)s, %(publisher)s,
    %(source_url)s, %(edition)s, %(geography)s, %(occupation_id)s, %(published_at)s,
    %(acquired_at)s, %(attribution)s, %(content_hash)s, %(title)s, %(institution)s,
    %(issuing_unit)s, %(document_roles)s, %(applicability)s, %(applicability_status)s,
    COALESCE(%(lifecycle_status)s, 'active')
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
    lifecycle_status = EXCLUDED.lifecycle_status,
    metadata_revision = source_provenance.metadata_revision + 1,
    updated_at = now()
WHERE (source_provenance.source_category, source_provenance.classification_origin,
       source_provenance.publisher, source_provenance.source_url, source_provenance.edition,
       source_provenance.geography, source_provenance.occupation_id,
       source_provenance.published_at, source_provenance.acquired_at,
       source_provenance.attribution, source_provenance.content_hash,
       source_provenance.title, source_provenance.institution, source_provenance.issuing_unit,
       source_provenance.document_roles, source_provenance.applicability,
       source_provenance.applicability_status, source_provenance.lifecycle_status)
    IS DISTINCT FROM
      (EXCLUDED.source_category, EXCLUDED.classification_origin, EXCLUDED.publisher,
       EXCLUDED.source_url, EXCLUDED.edition, EXCLUDED.geography, EXCLUDED.occupation_id,
       EXCLUDED.published_at, EXCLUDED.acquired_at, EXCLUDED.attribution, EXCLUDED.content_hash,
       EXCLUDED.title, EXCLUDED.institution, EXCLUDED.issuing_unit, EXCLUDED.document_roles,
       EXCLUDED.applicability, EXCLUDED.applicability_status, EXCLUDED.lifecycle_status);


-- name: get_sources_applicability_metadata
SELECT s.source_id, s.filename, s.is_current, s.origin_key, s.index_revision,
       p.source_category, p.publisher, p.source_url, p.edition, p.published_at,
       p.acquired_at, p.title, p.institution, p.issuing_unit, p.document_roles,
       p.applicability, p.applicability_status, p.metadata_revision,
       COALESCE(p.lifecycle_status, 'active') AS lifecycle_status
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.source_id = ANY(%(source_ids)s::uuid[])
ORDER BY s.source_id;

-- name: get_source_detail_for_review
SELECT s.source_id, s.collection_id, s.filename, s.content_type, s.file_hash,
       s.status, s.workspace_key, s.index_revision, s.is_current, s.origin_key,
       s.created_at, s.updated_at,
       p.source_category, p.classification_origin, p.publisher, p.source_url,
       p.edition, p.geography, p.occupation_id, p.published_at, p.acquired_at,
       p.attribution, p.title, p.institution, p.issuing_unit, p.document_roles,
       p.applicability, p.applicability_status, p.metadata_revision,
       COALESCE(p.lifecycle_status, 'active') AS lifecycle_status
FROM sources s
LEFT JOIN source_provenance p ON p.source_id = s.source_id
WHERE s.source_id = %(source_id)s;

-- name: insert_source_review_proposal
INSERT INTO source_review_proposals (
    proposal_id, source_id, expected_metadata_revision, proposed_by_user_id,
    status, source_category, classification_origin, publisher, source_url,
    edition, geography, occupation_id, published_at, acquired_at, attribution,
    title, institution, issuing_unit, document_roles, applicability,
    applicability_status, lifecycle_status, replacement_intent, review_notes
) VALUES (
    %(proposal_id)s, %(source_id)s, %(expected_metadata_revision)s, %(proposed_by_user_id)s,
    %(status)s, %(source_category)s, %(classification_origin)s, %(publisher)s, %(source_url)s,
    %(edition)s, %(geography)s, %(occupation_id)s, %(published_at)s, %(acquired_at)s, %(attribution)s,
    %(title)s, %(institution)s, %(issuing_unit)s, %(document_roles)s, %(applicability)s,
    %(applicability_status)s, %(lifecycle_status)s, %(replacement_intent)s, %(review_notes)s
) RETURNING *;

-- name: get_source_review_proposal
SELECT * FROM source_review_proposals
WHERE proposal_id = %(proposal_id)s;

-- name: list_source_review_proposals_for_source
SELECT * FROM source_review_proposals
WHERE source_id = %(source_id)s
ORDER BY created_at DESC;

-- name: update_source_review_proposal_status
UPDATE source_review_proposals
SET status = %(status)s,
    reviewed_by_user_id = %(reviewed_by_user_id)s,
    reviewed_at = %(reviewed_at)s,
    rejection_reason = %(rejection_reason)s,
    updated_at = now()
WHERE proposal_id = %(proposal_id)s
RETURNING *;

-- name: update_source_provenance_from_review
UPDATE source_provenance
SET source_category = %(source_category)s,
    classification_origin = %(classification_origin)s,
    publisher = %(publisher)s,
    source_url = %(source_url)s,
    edition = %(edition)s,
    geography = %(geography)s,
    occupation_id = %(occupation_id)s,
    published_at = %(published_at)s,
    acquired_at = %(acquired_at)s,
    attribution = %(attribution)s,
    title = %(title)s,
    institution = %(institution)s,
    issuing_unit = %(issuing_unit)s,
    document_roles = %(document_roles)s,
    applicability = %(applicability)s,
    applicability_status = %(applicability_status)s,
    lifecycle_status = %(lifecycle_status)s,
    metadata_revision = metadata_revision + 1,
    updated_at = now()
WHERE source_id = %(source_id)s
  AND metadata_revision = %(expected_metadata_revision)s
RETURNING *;

-- name: update_source_lifecycle_status
UPDATE source_provenance
SET lifecycle_status = %(lifecycle_status)s,
    metadata_revision = metadata_revision + 1,
    updated_at = now()
WHERE source_id = %(source_id)s
  AND metadata_revision = %(expected_metadata_revision)s
RETURNING *;

-- name: update_source_is_current
UPDATE sources
SET is_current = %(is_current)s,
    updated_at = now()
WHERE source_id = %(source_id)s;

-- name: insert_source_lifecycle_event
INSERT INTO source_lifecycle_events (
    event_id, source_id, event_type, proposal_id, metadata_revision,
    actor_user_id, details
) VALUES (
    %(event_id)s, %(source_id)s, %(event_type)s, %(proposal_id)s, %(metadata_revision)s,
    %(actor_user_id)s, %(details)s
) RETURNING *;

-- name: list_source_lifecycle_events
SELECT * FROM source_lifecycle_events
WHERE source_id = %(source_id)s
ORDER BY created_at DESC;

-- name: insert_source_invalidation_outbox
INSERT INTO source_invalidation_outbox (
    outbox_id, source_id, event_type, payload
) VALUES (
    %(outbox_id)s, %(source_id)s, %(event_type)s, %(payload)s
) RETURNING *;

-- name: get_source_dependency_counts
SELECT
    (SELECT count(*) FROM programs WHERE source_id = %(source_id)s) AS program_count,
    (SELECT count(*) FROM courses WHERE source_id = %(source_id)s) AS course_count,
    (SELECT count(*) FROM retrieval_gaps WHERE %(source_id)s = ANY(scope_source_ids) OR %(source_id)s = ANY(resolution_source_ids)) AS gap_count,
    (SELECT count(*) FROM research_runs WHERE scope_manifest::text LIKE '%%' || %(source_id)s::text || '%%') AS research_run_count;

-- name: list_source_dependent_programs
SELECT program_id, title, kind, binding_status
FROM programs
WHERE source_id = %(source_id)s
ORDER BY title;
