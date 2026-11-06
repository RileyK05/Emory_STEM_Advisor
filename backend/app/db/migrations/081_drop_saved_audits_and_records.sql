-- Saved program audits, saved comparisons and academic records go. A student now
-- states the courses they have taken in the chat, and research checks them with
-- the audit engine, keeping nothing beyond that conversation.
DROP VIEW IF EXISTS degree_audit_current_state;
DROP TABLE IF EXISTS program_comparison_members;
DROP TABLE IF EXISTS program_comparisons;
DROP TABLE IF EXISTS audit_rule_evaluations;
DROP TABLE IF EXISTS degree_audits;
DROP TABLE IF EXISTS academic_record_verification_events;
DROP TABLE IF EXISTS academic_record_import_batches;
DROP TABLE IF EXISTS academic_record_revisions;
DROP TABLE IF EXISTS academic_record_attempts;
DROP TABLE IF EXISTS student_academic_records;
