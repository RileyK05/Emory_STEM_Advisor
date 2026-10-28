-- The reviewed guard now uses identity repair for codes present in another
-- supplied block. The environment ceiling still permits an operator kill switch.
UPDATE feature_flags
SET default_enabled = true, updated_at = now()
WHERE flag_name = 'citation_repair';
