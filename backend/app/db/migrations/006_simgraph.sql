-- Similarity graph: a curated department -> category -> course hierarchy plus
-- embedding-derived lateral course edges. Courses are the nodes.

-- Curated category names as they appeared in the atlas, per course.
CREATE TABLE course_categories (
    course_id uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    name      text NOT NULL,
    position  int  NOT NULL,
    PRIMARY KEY (course_id, name)
);

CREATE TABLE sim_builds (
    build_id       uuid PRIMARY KEY,
    collection_id  uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    model          text NOT NULL,
    config_version int  NOT NULL,
    is_current     boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX sim_builds_current ON sim_builds (collection_id) WHERE is_current;

CREATE TABLE sim_courses (
    build_id  uuid NOT NULL REFERENCES sim_builds ON DELETE CASCADE,
    course_id uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    embedding vector NOT NULL,
    PRIMARY KEY (build_id, course_id)
);

CREATE TABLE sim_edges (
    build_id uuid NOT NULL REFERENCES sim_builds ON DELETE CASCADE,
    course_a uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    course_b uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    weight   double precision NOT NULL,
    PRIMARY KEY (build_id, course_a, course_b),
    CHECK (course_a < course_b)
);

-- Curated hierarchy, collection-scoped (not per build). Level 0 = department,
-- level 1 = category. A course may belong to several categories (a DAG).
CREATE TABLE sim_categories (
    category_id        uuid PRIMARY KEY,
    collection_id      uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    parent_category_id uuid REFERENCES sim_categories ON DELETE CASCADE,
    level              int  NOT NULL,
    name               text NOT NULL,
    label              text NOT NULL,
    UNIQUE (collection_id, parent_category_id, name)
);

CREATE TABLE sim_category_members (
    category_id uuid NOT NULL REFERENCES sim_categories ON DELETE CASCADE,
    course_id   uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    PRIMARY KEY (category_id, course_id)
);

-- Per-build routing vectors for the hierarchy nodes.
CREATE TABLE sim_category_vectors (
    build_id         uuid NOT NULL REFERENCES sim_builds ON DELETE CASCADE,
    category_id      uuid NOT NULL REFERENCES sim_categories ON DELETE CASCADE,
    centroid         vector NOT NULL,
    medoid_course_id uuid REFERENCES courses ON DELETE SET NULL,
    size             int NOT NULL,
    PRIMARY KEY (build_id, category_id)
);
