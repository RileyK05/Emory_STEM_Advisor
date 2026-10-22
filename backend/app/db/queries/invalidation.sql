-- name: claim_source_invalidation_outbox
UPDATE source_invalidation_outbox
SET lease_token = %(lease_token)s,
    leased_until = now() + (%(lease_seconds)s * interval '1 second'),
    attempts = attempts + 1
WHERE outbox_id IN (
    SELECT outbox_id
    FROM source_invalidation_outbox
    WHERE NOT published
      AND (leased_until IS NULL OR leased_until < now())
    ORDER BY created_at, outbox_id
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING outbox_id, source_id, event_type, payload, attempts;

-- name: mark_audits_stale_for_source
UPDATE degree_audits
SET is_stale = true
WHERE is_stale = false
  AND rule_release_id IN (
      SELECT release_id FROM program_rule_releases WHERE source_id = %(source_id)s
  )
RETURNING audit_id;

-- name: complete_source_invalidation_outbox
UPDATE source_invalidation_outbox
SET published = true,
    consumed_at = now(),
    lease_token = NULL,
    leased_until = NULL,
    last_error = NULL
WHERE outbox_id = %(outbox_id)s
  AND lease_token = %(lease_token)s
  AND NOT published
RETURNING outbox_id;

-- name: release_source_invalidation_lease
UPDATE source_invalidation_outbox
SET lease_token = NULL,
    leased_until = NULL,
    last_error = %(last_error)s
WHERE outbox_id = %(outbox_id)s
  AND lease_token = %(lease_token)s
  AND NOT published;
