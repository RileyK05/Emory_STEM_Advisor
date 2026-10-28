-- Course structure that was implicit in codes and free text.
--
-- campus: Oxford is a separate campus whose displayed Atlas codes carry the
-- _OX subject suffix. Generated from the code, so it can never disagree with it.
-- No Atlanta/Oxford equivalence is stored: the two are not one-to-one, and
-- Emory states equivalence where it applies, in requisite text.
--
-- course_schools: the school (Atlas career) a course is offered under, verbatim
-- from the Atlas, or the Emory College catalog for retained catalog fallbacks.
--
-- course_topic_offerings: a topics course is one course; each term's topics are
-- rows, split from the Atlas title at the course title's ": " boundary.
--
-- course_lab_pairings: a lab and its same-number lecture, only where the parser
-- found one naming the other as a corequisite. A view over prereq_mentions, so
-- pairings exist exactly when the requisite text states them.

ALTER TABLE public.courses
    ADD COLUMN campus text GENERATED ALWAYS AS (
        CASE WHEN subject LIKE '%\_OX' THEN 'oxford' ELSE 'atlanta' END
    ) STORED;

CREATE INDEX courses_campus_idx ON public.courses (collection_id, campus);

CREATE TABLE public.course_schools (
    course_id uuid NOT NULL REFERENCES public.courses(course_id) ON DELETE CASCADE,
    school text NOT NULL,
    source text NOT NULL,
    term text,
    PRIMARY KEY (course_id, school),
    CONSTRAINT course_schools_source_check CHECK (
        source IN ('atlas_offering', 'college_catalog')),
    CONSTRAINT course_schools_term_check CHECK ((source = 'atlas_offering') = (term IS NOT NULL))
);

CREATE INDEX course_schools_school_idx ON public.course_schools (school);

CREATE TABLE public.course_topic_offerings (
    course_id uuid NOT NULL REFERENCES public.courses(course_id) ON DELETE CASCADE,
    term text NOT NULL,
    topic text NOT NULL,
    class_numbers text[] NOT NULL,
    PRIMARY KEY (course_id, term, topic)
);

CREATE VIEW public.course_lab_pairings AS
SELECT DISTINCT lab.collection_id, lab.course_id AS lab_course_id, lab.code AS lab_code,
       lecture.course_id AS lecture_course_id, lecture.code AS lecture_code
FROM public.courses lab
JOIN public.courses lecture
  ON lecture.collection_id = lab.collection_id AND lab.code = lecture.code || 'L'
JOIN public.prereq_mentions mention
  ON mention.kind = 'corequisite'
 AND ((mention.course_id = lab.course_id AND mention.target_code = lecture.code)
   OR (mention.course_id = lecture.course_id AND mention.target_code = lab.code));
