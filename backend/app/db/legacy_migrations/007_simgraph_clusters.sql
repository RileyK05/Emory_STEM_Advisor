-- The similarity hierarchy comes from clustering course embeddings (A2), with
-- optional hand-written nudges, not from curated atlas categories. Drops the
-- category tables 006 introduced and adds per-build clusters.

DROP TABLE sim_category_vectors;
DROP TABLE sim_category_members;
DROP TABLE sim_categories;
DROP TABLE course_categories;

-- sha256 of the nudges file the build used (NULL: no nudges file).
ALTER TABLE sim_builds ADD COLUMN nudges_hash text;

-- One node of the cluster tree. `level` is the index of the distance cut that
-- produced it (0 = root). A cluster whose members equal its parent's is not
-- stored, so a child's level can skip past its parent's level + 1.
CREATE TABLE sim_clusters (
    cluster_id        uuid PRIMARY KEY,
    build_id          uuid NOT NULL REFERENCES sim_builds ON DELETE CASCADE,
    parent_cluster_id uuid REFERENCES sim_clusters ON DELETE CASCADE,
    level             int  NOT NULL,
    label             text NOT NULL,
    medoid_course_id  uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    centroid          vector NOT NULL,
    size              int  NOT NULL
);
CREATE INDEX sim_clusters_build ON sim_clusters (build_id);
CREATE INDEX sim_clusters_parent ON sim_clusters (parent_cluster_id);

-- Every course in a cluster's subtree (not only direct members).
CREATE TABLE sim_cluster_members (
    cluster_id uuid NOT NULL REFERENCES sim_clusters ON DELETE CASCADE,
    course_id  uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    PRIMARY KEY (cluster_id, course_id)
);
