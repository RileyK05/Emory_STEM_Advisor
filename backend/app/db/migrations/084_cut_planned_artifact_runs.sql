-- Dependent artifact plans could only start from POST /api/runs, which nothing called.
DROP TABLE artifact_run_unit_attempts;
DROP TABLE artifact_run_unit_sources;
DROP TABLE artifact_run_unit_dependencies;
DROP TABLE artifact_run_unit_calls;
DROP TABLE artifact_run_units;
DROP TABLE artifact_run_plan_excerpts;
DROP TABLE artifact_run_plan_sources;
DROP TABLE artifact_run_plans;
DROP FUNCTION protect_artifact_run_attempt();
DROP FUNCTION protect_artifact_run_excerpt();
DROP FUNCTION protect_artifact_run_plan_snapshot();
DROP FUNCTION protect_artifact_run_unit_snapshot();
