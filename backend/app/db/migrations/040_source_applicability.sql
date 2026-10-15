-- Institutional publications need explicit publisher and applicability metadata:
-- an institution's publication does not imply that its rules apply to everyone.
-- Existing verified sources keep unknown applicability until reviewed.

ALTER TABLE public.source_provenance
    DROP CONSTRAINT source_provenance_category_check,
    ADD CONSTRAINT source_provenance_category_check CHECK (source_category IN (
        'institutional_catalog', 'institutional_department', 'institutional_policy',
        'institutional_publication', 'career_reference', 'user_supplied', 'unclassified')),
    ADD COLUMN title text,
    ADD COLUMN institution text,
    ADD COLUMN issuing_unit text,
    ADD COLUMN document_roles jsonb DEFAULT '[]'::jsonb NOT NULL,
    ADD COLUMN applicability jsonb DEFAULT '{}'::jsonb NOT NULL,
    ADD COLUMN applicability_status text DEFAULT 'unknown' NOT NULL,
    ADD COLUMN metadata_revision integer DEFAULT 1 NOT NULL,
    ADD CONSTRAINT source_provenance_roles_check CHECK (
        jsonb_typeof(document_roles) = 'array' AND jsonb_array_length(document_roles) <= 20),
    ADD CONSTRAINT source_provenance_applicability_check CHECK (
        jsonb_typeof(applicability) = 'object'),
    ADD CONSTRAINT source_provenance_applicability_status_check CHECK (
        applicability_status IN ('reviewed_default', 'historical', 'unknown')),
    ADD CONSTRAINT source_provenance_metadata_revision_check CHECK (metadata_revision >= 1);
