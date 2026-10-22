-- These previously inert defaults did not affect execution. Preserve process
-- opt-ins; explicit rollout overrides and kill switches still take precedence.
UPDATE feature_flags SET default_enabled = true
WHERE flag_name IN ('query_planning', 'advising_research', 'tools', 'citation_repair');

-- Canonicalize the two formerly accepted global aliases deterministically.
DELETE FROM feature_flag_overrides a USING feature_flag_overrides b
WHERE a.flag_name = b.flag_name AND a.scope_type = 'global' AND b.scope_type = 'global'
  AND a.scope_id = 'global' AND b.scope_id = '*';
UPDATE feature_flag_overrides SET scope_id = '*'
WHERE scope_type = 'global' AND scope_id = 'global';
DELETE FROM feature_flag_overrides
WHERE scope_type = 'global' AND scope_id <> '*';
ALTER TABLE feature_flag_overrides ADD CONSTRAINT feature_flag_global_scope
    CHECK (scope_type <> 'global' OR scope_id = '*');
