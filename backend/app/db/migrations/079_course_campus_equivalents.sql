-- Campus equivalence: an Oxford course is the other campus's offering of a course.
--
-- Emory College and Oxford College publish the same course under different
-- codes: MATH 112 is "Calculus II" in Atlanta and MATH_OX 112 is "Calculus II"
-- at Oxford. A student who took one has taken the other, and a requirement
-- that names either accepts either. The database has to say so.
--
-- Migration 078 deliberately stored no Atlanta/Oxford equivalence, because the
-- two are not one-to-one and Emory states the relation in requisite text where
-- it applies. That left the relation stated nowhere the product could use it.
-- This table states it, derived from the Atlas itself:
--
--   catalog_requirement - a requirement offers an Atlanta course and an Oxford
--     course with the same course number in one alternative list, which is how
--     Emory states that they are one course at two campuses.
--
--   course_record - two records with the same course number and compatible
--     titles are the same course at the two campuses. The title is required:
--     PE_OX 101 is "Weight Training/Conditioning" and PE 101 is "Personal
--     Health", so the number alone proves nothing.
--
-- The relation is NOT courses.canonical_code. That column is the retrieval
-- deduplication key, and sharing it between the campuses would delete one
-- campus's passage from the candidate set, relabel the survivor, blind the
-- answer's course-identity check to the difference between the campuses, and
-- leave the research search unable to open the Oxford record at all. Here the
-- Oxford code may name a course the master has no row for, which is the point:
-- the catalog names it and the master does not carry it.

CREATE TABLE public.course_campus_equivalents (
    collection_id uuid NOT NULL REFERENCES public.collections(collection_id) ON DELETE CASCADE,
    oxford_code text NOT NULL,
    atlanta_code text NOT NULL,
    basis text NOT NULL,
    PRIMARY KEY (collection_id, oxford_code, atlanta_code),
    CONSTRAINT course_campus_equivalents_basis_check
        CHECK (basis IN ('catalog_requirement', 'course_record'))
);

CREATE INDEX course_campus_equivalents_atlanta_idx
    ON public.course_campus_equivalents (collection_id, atlanta_code);
