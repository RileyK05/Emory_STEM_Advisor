-- Prerequisite graph and cascade indexes. A schema audit found foreign keys and
-- per-request filters with no index whose leading columns cover them:
--
-- prereq_nodes(course_id): the upstream prerequisite seam, the planner and the
--   admin views read a course's nodes by course_id, and every Atlas reparse
--   deletes them by course_id; each was a scan of the whole node table.
-- prereq_nodes(parent_node_id): the self-referencing ON DELETE CASCADE looks up
--   each deleted node's children, a scan per deleted row without this index.
-- prereq_nodes(target_code): the downstream seam ("what requires CS 253?") finds
--   the edges that name a course; only course nodes carry a target code.
-- courses(source_id), courses(locator_id): an Atlas source and its locators
--   cascade to courses, and the importer lists a source's courses.
-- gap_observations(trace_id): deleting a workspace's retrieval traces cascades
--   here; the primary key leads with workspace_key, so it cannot serve the lookup.

CREATE INDEX prereq_nodes_course_idx ON public.prereq_nodes (course_id, kind, position);

CREATE INDEX prereq_nodes_parent_idx ON public.prereq_nodes (parent_node_id)
    WHERE parent_node_id IS NOT NULL;

CREATE INDEX prereq_nodes_target_code_idx ON public.prereq_nodes (target_code)
    WHERE node_type = 'course';

CREATE INDEX courses_source_idx ON public.courses (source_id);

CREATE INDEX courses_locator_idx ON public.courses (locator_id);

CREATE INDEX gap_observations_trace_idx ON public.gap_observations (trace_id);
