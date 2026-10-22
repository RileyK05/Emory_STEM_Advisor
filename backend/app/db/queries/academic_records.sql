-- Academic records, course attempts, import batches, and verification events

-- name: get_or_create_academic_record
INSERT INTO public.student_academic_records (record_id, workspace_key, record_revision)
VALUES (%(record_id)s, %(workspace_key)s, 1)
ON CONFLICT (workspace_key) DO UPDATE
SET updated_at = public.student_academic_records.updated_at
RETURNING record_id, workspace_key, record_revision, created_at, updated_at;

-- name: get_academic_record_by_workspace
SELECT record_id, workspace_key, record_revision, created_at, updated_at
FROM public.student_academic_records
WHERE workspace_key = %(workspace_key)s;

-- name: lock_academic_record_by_workspace
SELECT record_id, workspace_key, record_revision, created_at, updated_at
FROM public.student_academic_records
WHERE workspace_key = %(workspace_key)s
FOR UPDATE;

-- name: get_academic_record_by_id
SELECT record_id, workspace_key, record_revision, created_at, updated_at
FROM public.student_academic_records
WHERE record_id = %(record_id)s;

-- name: increment_record_revision
UPDATE public.student_academic_records
SET record_revision = record_revision + 1,
    updated_at = now()
WHERE record_id = %(record_id)s AND record_revision = %(expected_revision)s
RETURNING record_revision, updated_at;

-- name: bump_record_revision
UPDATE public.student_academic_records
SET record_revision = record_revision + 1,
    updated_at = now()
WHERE record_id = %(record_id)s
RETURNING record_revision, updated_at;

-- name: get_academic_record_attempts
SELECT attempt_id, record_id, workspace_key, institution, submitted_course_code,
       resolved_course_code, course_title, term, attempted_credits, earned_credits,
       grade, status, provenance, is_verified, verification_method, verified_by_user_id,
       verified_at, verification_notes, created_at, updated_at
FROM public.academic_record_attempts
WHERE record_id = %(record_id)s
ORDER BY term ASC, submitted_course_code ASC;

-- name: get_academic_record_attempt_by_id
SELECT attempt_id, record_id, workspace_key, institution, submitted_course_code,
       resolved_course_code, course_title, term, attempted_credits, earned_credits,
       grade, status, provenance, is_verified, verification_method, verified_by_user_id,
       verified_at, verification_notes, created_at, updated_at
FROM public.academic_record_attempts
WHERE attempt_id = %(attempt_id)s;

-- name: insert_academic_record_attempt
INSERT INTO public.academic_record_attempts (
    attempt_id, record_id, workspace_key, institution, submitted_course_code,
    resolved_course_code, course_title, term, attempted_credits, earned_credits,
    grade, status, provenance, is_verified, verification_method, verified_by_user_id,
    verified_at, verification_notes
) VALUES (
    %(attempt_id)s, %(record_id)s, %(workspace_key)s, %(institution)s, %(submitted_course_code)s,
    %(resolved_course_code)s, %(course_title)s, %(term)s, %(attempted_credits)s, %(earned_credits)s,
    %(grade)s, %(status)s, %(provenance)s, %(is_verified)s, %(verification_method)s, %(verified_by_user_id)s,
    %(verified_at)s, %(verification_notes)s
)
RETURNING attempt_id, record_id, workspace_key, institution, submitted_course_code,
          resolved_course_code, course_title, term, attempted_credits, earned_credits,
          grade, status, provenance, is_verified, verification_method, verified_by_user_id,
          verified_at, verification_notes, created_at, updated_at;

-- name: update_academic_record_attempt
UPDATE public.academic_record_attempts
SET institution = %(institution)s,
    submitted_course_code = %(submitted_course_code)s,
    resolved_course_code = %(resolved_course_code)s,
    course_title = %(course_title)s,
    term = %(term)s,
    attempted_credits = %(attempted_credits)s,
    earned_credits = %(earned_credits)s,
    grade = %(grade)s,
    status = %(status)s,
    provenance = %(provenance)s,
    updated_at = now()
WHERE attempt_id = %(attempt_id)s
RETURNING attempt_id, record_id, workspace_key, institution, submitted_course_code,
          resolved_course_code, course_title, term, attempted_credits, earned_credits,
          grade, status, provenance, is_verified, verification_method, verified_by_user_id,
          verified_at, verification_notes, created_at, updated_at;

-- name: invalidate_attempt_verification
UPDATE public.academic_record_attempts
SET is_verified = false,
    verification_method = NULL,
    verified_by_user_id = NULL,
    verified_at = NULL,
    verification_notes = NULL,
    updated_at = now()
WHERE attempt_id = %(attempt_id)s
RETURNING attempt_id, record_id, workspace_key, institution, submitted_course_code,
          resolved_course_code, course_title, term, attempted_credits, earned_credits,
          grade, status, provenance, is_verified, verification_method, verified_by_user_id,
          verified_at, verification_notes, created_at, updated_at;

-- name: verify_academic_record_attempt
UPDATE public.academic_record_attempts
SET is_verified = true,
    verification_method = %(verification_method)s,
    verified_by_user_id = %(verified_by_user_id)s,
    verified_at = now(),
    verification_notes = %(verification_notes)s,
    updated_at = now()
WHERE attempt_id = %(attempt_id)s
RETURNING attempt_id, record_id, workspace_key, institution, submitted_course_code,
          resolved_course_code, course_title, term, attempted_credits, earned_credits,
          grade, status, provenance, is_verified, verification_method, verified_by_user_id,
          verified_at, verification_notes, created_at, updated_at;

-- name: delete_academic_record_attempt
DELETE FROM public.academic_record_attempts
WHERE attempt_id = %(attempt_id)s
RETURNING attempt_id, record_id, workspace_key;

-- name: insert_import_batch
INSERT INTO public.academic_record_import_batches (
    batch_id, record_id, workspace_key, expected_record_revision, status,
    filename, raw_content, parsed_rows, row_errors, duplicate_candidates,
    preview_digest, row_decisions
) VALUES (
    %(batch_id)s, %(record_id)s, %(workspace_key)s, %(expected_record_revision)s, %(status)s,
    %(filename)s, %(raw_content)s, %(parsed_rows)s, %(row_errors)s, %(duplicate_candidates)s,
    %(preview_digest)s, %(row_decisions)s
)
RETURNING batch_id, record_id, workspace_key, expected_record_revision, status,
          filename, raw_content, parsed_rows, row_errors, duplicate_candidates,
          preview_digest, row_decisions, committed_count, created_at, committed_at;

-- name: get_import_batch_by_id
SELECT batch_id, record_id, workspace_key, expected_record_revision, status,
       filename, raw_content, parsed_rows, row_errors, duplicate_candidates,
       preview_digest, row_decisions, committed_count, created_at, committed_at
FROM public.academic_record_import_batches
WHERE batch_id = %(batch_id)s;

-- name: update_import_batch_preview
UPDATE public.academic_record_import_batches
SET parsed_rows = %(parsed_rows)s,
    row_decisions = %(row_decisions)s,
    preview_digest = %(preview_digest)s
WHERE batch_id = %(batch_id)s AND status = 'preview'
RETURNING batch_id, record_id, workspace_key, expected_record_revision, status,
          filename, raw_content, parsed_rows, row_errors, duplicate_candidates,
          preview_digest, row_decisions, committed_count, created_at, committed_at;

-- name: update_import_batch_status
UPDATE public.academic_record_import_batches
SET status = %(status)s,
    committed_count = %(committed_count)s,
    committed_at = CASE WHEN %(status)s = 'committed' THEN now() ELSE committed_at END
WHERE batch_id = %(batch_id)s
RETURNING batch_id, record_id, workspace_key, expected_record_revision, status,
          filename, raw_content, parsed_rows, row_errors, duplicate_candidates,
          preview_digest, row_decisions, committed_count, created_at, committed_at;

-- name: insert_academic_record_revision
INSERT INTO public.academic_record_revisions (record_id, revision, attempts)
VALUES (%(record_id)s, %(revision)s, %(attempts)s)
ON CONFLICT (record_id, revision) DO NOTHING;

-- name: get_academic_record_revision
SELECT record_id, revision, attempts, created_at
FROM public.academic_record_revisions
WHERE record_id = %(record_id)s AND revision = %(revision)s;

-- name: insert_academic_record_verification_event
INSERT INTO public.academic_record_verification_events (
    event_id, record_id, attempt_id, event_type, actor_user_id, actor_role, details
) VALUES (
    %(event_id)s, %(record_id)s, %(attempt_id)s, %(event_type)s, %(actor_user_id)s, %(actor_role)s, %(details)s
)
RETURNING event_id, record_id, attempt_id, event_type, actor_user_id, actor_role, details, created_at;

-- name: list_academic_record_verification_events
SELECT event_id, record_id, attempt_id, event_type, actor_user_id, actor_role, details, created_at
FROM public.academic_record_verification_events
WHERE record_id = %(record_id)s
ORDER BY created_at DESC;

