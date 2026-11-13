-- Offering lookups: expression indexes for the two per-answer offering queries that
-- sequentially scanned whole tables. `research_course_offering_variants` matches
-- an offering row's stem (campus mark and suffix removed) and
-- `research_course_offering_chunks` matches a locator label's first two words
-- (its course code). Each index is on exactly the expression its query filters by.

CREATE INDEX atlas_offering_rows_eligible_stem_idx
    ON public.atlas_offering_rows
    (regexp_replace(replace(raw_course_code, '_OX'::text, ''::text), '[A-Z]+$'::text, ''::text))
    WHERE (review_status = 'eligible'::text);

CREATE INDEX locators_label_code_idx
    ON public.locators
    ((split_part(label, ' '::text, 1) || ' '::text || split_part(label, ' '::text, 2)));
