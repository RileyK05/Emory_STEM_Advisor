-- Feature flags, cohort assignments, overrides, and kill-switches (Item 17).

CREATE TABLE IF NOT EXISTS feature_flags (
    flag_name text PRIMARY KEY,
    description text NOT NULL,
    env_ceiling_key text,
    default_enabled boolean NOT NULL DEFAULT false,
    kill_switch_active boolean NOT NULL DEFAULT false,
    kill_reason text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_cohorts (
    user_id uuid NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    cohort_name text NOT NULL,
    assigned_by uuid REFERENCES app_users(user_id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, cohort_name)
);

CREATE INDEX IF NOT EXISTS idx_user_cohorts_name
    ON user_cohorts(cohort_name);

CREATE TABLE IF NOT EXISTS feature_flag_overrides (
    override_id uuid PRIMARY KEY,
    flag_name text NOT NULL REFERENCES feature_flags(flag_name) ON DELETE CASCADE,
    scope_type text NOT NULL CHECK (scope_type IN ('global', 'cohort', 'workspace')),
    scope_id text NOT NULL,
    enabled boolean NOT NULL,
    reason text,
    actor_user_id uuid REFERENCES app_users(user_id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (flag_name, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_flag_overrides_lookup
    ON feature_flag_overrides(flag_name, scope_type, scope_id);

-- Prepopulate initial feature flags
INSERT INTO feature_flags (flag_name, description, env_ceiling_key, default_enabled)
VALUES
    ('query_planning', 'Plan multi-step retrieval queries', 'QUERY_PLANNING_ENABLED', false),
    ('advising_research', 'Research controller and multi-program comparison', 'ADVISING_RESEARCH_ENABLED', false),
    ('tools', 'Sandbox and calculation tools', 'TOOLS_ENABLED', false),
    ('citation_repair', 'Answer citation repair', 'ANSWER_CITATION_REPAIR_ENABLED', false),
    ('conversation_compaction', 'Compacting long conversations', 'CONVERSATION_COMPACTION_ENABLED', true),
    ('advising_summary_export', 'Advising plan and summary export', NULL, true),
    ('memory_inspector', 'Conversation memory inspector and reset', NULL, true)
ON CONFLICT (flag_name) DO NOTHING;

