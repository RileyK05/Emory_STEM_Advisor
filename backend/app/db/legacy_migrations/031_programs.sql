-- Program (major/minor) requirements parsed deterministically from the official
-- catalog PDF. Every requirement line is kept as raw text; course mentions and
-- explicit "choose N" counts are what the parser read off it. Mentions link to
-- courses by code and are re-linked whenever either side is re-imported.
CREATE TABLE programs (
    program_id       uuid PRIMARY KEY,
    collection_id    uuid NOT NULL REFERENCES collections ON DELETE CASCADE,
    title            text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('major', 'minor', 'other')),
    degree           text,
    hours            text,
    courses_required text,
    department       text,
    catalog          text NOT NULL,
    source_url       text NOT NULL,
    first_page       int  NOT NULL,
    last_page        int  NOT NULL,
    parser_version   text NOT NULL,
    UNIQUE (collection_id, title)
);

CREATE TABLE program_items (
    program_id uuid NOT NULL REFERENCES programs ON DELETE CASCADE,
    position   int  NOT NULL,
    section    text,
    text       text NOT NULL,
    choose     int CHECK (choose IS NULL OR choose > 0),
    PRIMARY KEY (program_id, position)
);

CREATE TABLE program_course_mentions (
    program_id       uuid NOT NULL,
    position         int  NOT NULL,
    target_code      text NOT NULL,
    target_course_id uuid REFERENCES courses ON DELETE SET NULL,
    PRIMARY KEY (program_id, position, target_code),
    FOREIGN KEY (program_id, position) REFERENCES program_items ON DELETE CASCADE
);
CREATE INDEX program_course_mentions_code_idx ON program_course_mentions (target_code);
CREATE INDEX program_course_mentions_course_idx ON program_course_mentions (target_course_id);
