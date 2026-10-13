-- A live course deletion must not erase a saved topic build.
--
-- The build now owns an immutable snapshot of the course identity it grouped
-- (member and representative code/title) and of its vectors, so the historical
-- containment tree stays readable after a course leaves the catalog. The live
-- course pointer becomes informational: no foreign key cascade, and the medoid
-- may dangle. Builds are bound to the catalog revision they were made from and
-- are retired (not served as current) when that catalog changes.

-- 1. Immutable identity snapshots.
ALTER TABLE sim_cluster_members ADD COLUMN code text;
ALTER TABLE sim_cluster_members ADD COLUMN title text;
ALTER TABLE sim_clusters ADD COLUMN medoid_code text;
ALTER TABLE sim_clusters ADD COLUMN medoid_title text;
UPDATE sim_cluster_members m
   SET code = co.code, title = co.title
  FROM courses co
 WHERE co.course_id = m.course_id AND (m.code IS NULL OR m.title IS NULL);
UPDATE sim_clusters c
   SET medoid_code = co.code, medoid_title = co.title
  FROM courses co
 WHERE co.course_id = c.medoid_course_id AND (c.medoid_code IS NULL OR c.medoid_title IS NULL);

-- 2. Live-course deletion is a no-op for saved builds: drop the cascading FKs.
ALTER TABLE sim_clusters DROP CONSTRAINT sim_clusters_medoid_course_id_fkey;
ALTER TABLE sim_clusters ALTER COLUMN medoid_course_id DROP NOT NULL;
ALTER TABLE sim_cluster_members DROP CONSTRAINT sim_cluster_members_course_id_fkey;
ALTER TABLE sim_courses DROP CONSTRAINT sim_courses_course_id_fkey;

-- 3. Bind a build to the catalog revision it was made from.
ALTER TABLE sim_builds ADD COLUMN content_hash text;
-- Existing current builds are bound to the currently published atlas, so a
-- byte-identical re-import keeps them current instead of retiring them.
UPDATE sim_builds b
   SET content_hash = s.file_hash
  FROM sources s
 WHERE b.is_current AND b.graph_version = 2
   AND s.collection_id = b.collection_id
   AND s.content_type = 'application/x-course-atlas+jsonl'
   AND s.is_current;
