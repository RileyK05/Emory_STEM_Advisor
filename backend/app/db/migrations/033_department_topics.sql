-- Department -> learned topic -> course containment replaces lateral similarity links.
-- Preserve old build metadata/hierarchies for provenance, but never serve them as v2.
ALTER TABLE sim_builds ADD COLUMN graph_version int NOT NULL DEFAULT 1;
ALTER TABLE sim_builds ALTER COLUMN graph_version SET DEFAULT 2;
ALTER TABLE sim_builds ADD COLUMN departments_hash text;
UPDATE sim_builds SET is_current = false WHERE graph_version = 1;

ALTER TABLE sim_clusters ADD COLUMN kind text NOT NULL DEFAULT 'topic'
    CHECK (kind IN ('root', 'department', 'topic'));
ALTER TABLE sim_clusters ADD COLUMN department_key text;
UPDATE sim_clusters SET kind = 'root' WHERE parent_cluster_id IS NULL;
CREATE INDEX sim_clusters_department ON sim_clusters (build_id, department_key, level);

-- No consumer needs course-to-course cosine scores. Existing backups retain them.
DROP TABLE sim_edges;
