-- Immutable prompt versions. Promotion records a human decision only.

-- name: get_prompt_version_by_digest
SELECT version_id, name, template_text, content_digest, review_state, created_at
FROM prompt_versions
WHERE content_digest = %(content_digest)s;

-- name: get_prompt_version
SELECT version_id, name, template_text, content_digest, review_state, created_at
FROM prompt_versions
WHERE version_id = %(version_id)s;

-- name: insert_prompt_version
INSERT INTO prompt_versions (version_id, name, template_text, content_digest, review_state)
VALUES (%(version_id)s, %(name)s, %(template_text)s, %(content_digest)s, 'draft')
RETURNING version_id, name, template_text, content_digest, review_state, created_at;

-- name: approve_prompt_version
UPDATE prompt_versions
SET review_state = 'approved'
WHERE version_id = %(version_id)s AND review_state = 'draft'
RETURNING version_id, name, template_text, content_digest, review_state, created_at;

-- name: get_current_eval_fixture
SELECT fixture_id, suite_name, fixture_digest, fixture_text, present, created_at
FROM eval_suite_fixtures
WHERE suite_name = %(suite_name)s AND present;

-- name: insert_eval_fixture
INSERT INTO eval_suite_fixtures (fixture_id, suite_name, fixture_digest, fixture_text, present)
VALUES (%(fixture_id)s, %(suite_name)s, %(fixture_digest)s, %(fixture_text)s, true)
RETURNING fixture_id, suite_name, fixture_digest, fixture_text, present, created_at;

-- name: clear_current_eval_fixture
UPDATE eval_suite_fixtures
SET present = false
WHERE suite_name = %(suite_name)s AND present;

-- name: get_prompt_promotion
SELECT promotion_id, version_id, suite_name, fixture_digest, status, created_at
FROM prompt_promotions
WHERE version_id = %(version_id)s AND suite_name = %(suite_name)s
  AND fixture_digest = %(fixture_digest)s;

-- name: insert_prompt_promotion
INSERT INTO prompt_promotions (
    promotion_id, version_id, suite_name, fixture_digest, status
) VALUES (
    %(promotion_id)s, %(version_id)s, %(suite_name)s, %(fixture_digest)s, 'published'
)
RETURNING promotion_id, version_id, suite_name, fixture_digest, status, created_at;
