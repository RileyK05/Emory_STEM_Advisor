-- name: insert_artifact
INSERT INTO artifacts (
    artifact_id, workspace_key, kind, head_revision_id,
    original_filename, original_sha256, original_path
) VALUES (
    %(artifact_id)s, %(workspace_key)s, %(kind)s, %(revision_id)s,
    %(original_filename)s, %(original_sha256)s, %(original_path)s
);
-- name: insert_artifact_revision
INSERT INTO artifact_revisions (
    revision_id, artifact_id, parent_revision_id, draft_id, spec
) VALUES (
    %(revision_id)s, %(artifact_id)s, %(parent_revision_id)s, %(draft_id)s, %(spec)s
);
-- name: get_artifact_revision
SELECT a.*, r.revision_id, r.parent_revision_id, r.spec, r.created_at AS revision_created_at
FROM artifacts a
JOIN artifact_revisions r ON r.artifact_id = a.artifact_id
WHERE a.artifact_id = %(artifact_id)s AND a.workspace_key = %(workspace_key)s
  AND r.revision_id = COALESCE(%(revision_id)s::uuid, a.head_revision_id);
-- name: get_artifact_draft_revision
SELECT a.*, r.revision_id, r.parent_revision_id, r.spec, r.created_at AS revision_created_at
FROM artifacts a
JOIN artifact_revisions r ON r.artifact_id = a.artifact_id
WHERE a.artifact_id = %(artifact_id)s AND a.workspace_key = %(workspace_key)s
  AND r.draft_id = %(draft_id)s;
-- name: advance_artifact_head
UPDATE artifacts
SET head_revision_id = %(revision_id)s
WHERE artifact_id = %(artifact_id)s AND workspace_key = %(workspace_key)s
  AND head_revision_id = %(expected_revision_id)s
RETURNING artifact_id;
-- name: list_artifact_revisions
SELECT r.revision_id, r.parent_revision_id, r.spec->>'title' AS title, r.created_at
FROM artifact_revisions r JOIN artifacts a ON a.artifact_id = r.artifact_id
WHERE a.artifact_id = %(artifact_id)s AND a.workspace_key = %(workspace_key)s
ORDER BY r.created_at, r.revision_id;
-- name: list_artifacts
SELECT a.*, r.revision_id, r.parent_revision_id, r.spec, r.created_at AS revision_created_at
FROM artifacts a
JOIN artifact_revisions r ON r.revision_id = a.head_revision_id
WHERE a.workspace_key = %(workspace_key)s
ORDER BY a.created_at DESC, a.artifact_id
LIMIT 200;

-- name: count_artifacts_for_workspace
SELECT count(*) AS count FROM artifacts WHERE workspace_key = %(workspace_key)s;

-- name: count_artifact_revisions_for_artifact
SELECT count(*) AS count
FROM artifact_revisions r JOIN artifacts a ON a.artifact_id = r.artifact_id
WHERE a.artifact_id = %(artifact_id)s AND a.workspace_key = %(workspace_key)s;
