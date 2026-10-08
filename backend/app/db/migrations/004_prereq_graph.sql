-- Prerequisite graph: parsed deterministically from the course atlas.
-- An LLM never writes anything in these tables.

CREATE TABLE courses (
    course_id      uuid PRIMARY KEY,
    collection_id  uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    source_id      uuid NOT NULL REFERENCES sources ON DELETE CASCADE,
    locator_id     uuid NOT NULL REFERENCES locators ON DELETE CASCADE,
    code           text NOT NULL,
    subject        text NOT NULL,
    title          text NOT NULL,
    requisites_raw text,
    parse_status   text NOT NULL
        CHECK (parse_status IN ('none','parsed','ambiguous','unparsed')),
    parser_version text NOT NULL,
    UNIQUE (collection_id, code)
);

CREATE TABLE prereq_mentions (
    course_id        uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    kind             text NOT NULL
        CHECK (kind IN ('prerequisite','corequisite','recommended')),
    target_code      text NOT NULL,
    target_course_id uuid REFERENCES courses ON DELETE SET NULL,
    position         int  NOT NULL,
    PRIMARY KEY (course_id, kind, target_code)
);
CREATE INDEX prereq_mentions_target_idx ON prereq_mentions (target_course_id);

CREATE TABLE prereq_nodes (
    node_id        uuid PRIMARY KEY,
    course_id      uuid NOT NULL REFERENCES courses ON DELETE CASCADE,
    kind           text NOT NULL,
    parent_node_id uuid REFERENCES prereq_nodes ON DELETE CASCADE,
    position       int  NOT NULL,
    node_type      text NOT NULL CHECK (node_type IN ('all','any','course','condition')),
    target_code    text,
    condition_text text,
    CHECK ((node_type = 'course')    = (target_code IS NOT NULL)),
    CHECK ((node_type = 'condition') = (condition_text IS NOT NULL))
);
