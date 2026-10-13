-- Cross-listed listings are one underlying course for counting, not many.
--
-- The catalog states the relation explicitly. We store each listing's group
-- anchor in courses.canonical_code so retrieval can deduplicate a canonical
-- course once before the embedding quota; otherwise aliases with different
-- headers fill the slots that distinct courses need. Existing rows default to
-- their own code, so behavior is unchanged until the atlas is imported again.

ALTER TABLE courses ADD COLUMN canonical_code text;
UPDATE courses SET canonical_code = code WHERE canonical_code IS NULL;
ALTER TABLE courses ALTER COLUMN canonical_code SET NOT NULL;
CREATE INDEX courses_canonical_code_idx ON courses (collection_id, canonical_code);
