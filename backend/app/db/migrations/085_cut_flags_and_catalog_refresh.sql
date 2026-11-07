-- Feature rollouts and catalog refresh lost their panels in P1.1 and no longer run.
-- Each flag read became its config.py setting.
DROP TABLE feature_flag_overrides;
DROP TABLE feature_flags;
DROP TABLE user_cohorts;
DROP TABLE catalog_refresh_batches;
DROP TABLE catalog_editions;
