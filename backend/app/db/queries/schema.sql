-- name: schema_catalog
SELECT 'column' AS kind, c.relname || '.' || a.attname AS name,
       jsonb_build_object('position', a.attnum, 'type', format_type(a.atttypid, a.atttypmod),
           'not_null', a.attnotnull, 'generated', a.attgenerated,
           'default', pg_get_expr(d.adbin, d.adrelid))::text AS definition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'schema_migrations'
  AND a.attnum > 0 AND NOT a.attisdropped
UNION ALL
SELECT 'constraint', c.relname || '.' || k.conname,
       jsonb_build_object('definition', pg_get_constraintdef(k.oid),
           'validated', k.convalidated)::text
FROM pg_constraint k
JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname <> 'schema_migrations'
UNION ALL
SELECT 'index', c.relname, pg_get_indexdef(c.oid)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_index i ON i.indexrelid = c.oid
JOIN pg_class t ON t.oid = i.indrelid
WHERE n.nspname = 'public' AND t.relname <> 'schema_migrations'
UNION ALL
SELECT 'trigger', c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid)
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
UNION ALL
SELECT 'function', p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
       pg_get_functiondef(p.oid)
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
ORDER BY kind, name;

-- name: schema_create_baseline_check_database
CREATE DATABASE baseline_check;

-- name: schema_drop_baseline_check_database
DROP DATABASE baseline_check;

-- name: schema_source_lifecycle
SELECT source_id, origin_key, is_current FROM sources ORDER BY source_id;

-- name: schema_retire_copy_sources
UPDATE sources SET is_current = false;

-- name: schema_restore_source_lifecycle
UPDATE sources SET origin_key = %(origin_key)s, is_current = %(is_current)s
WHERE source_id = %(source_id)s;

-- name: schema_legacy_program_sources
SELECT source_id, collection_id, file_hash, raw_path, created_at
FROM sources WHERE workspace_key IS NULL AND status = 'indexed'
  AND content_type = 'text/markdown' AND origin_key IS NULL
ORDER BY created_at, source_id;

-- name: schema_prepare_fingerprints
CREATE OR REPLACE FUNCTION pg_temp.table_fingerprints()
RETURNS TABLE(table_name text, row_count bigint, fingerprint text)
LANGUAGE plpgsql AS $$
DECLARE r record; ignored_columns text[];
BEGIN
    FOR r IN SELECT c.relname
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
               AND c.relname <> 'schema_migrations' ORDER BY c.relname
    LOOP
        ignored_columns := CASE WHEN r.relname = 'sources'
                           THEN ARRAY['is_current', 'origin_key'] ELSE ARRAY[]::text[] END;
        RETURN QUERY EXECUTE format(
            'SELECT %%L::text, count(*)::bigint, md5(coalesce(string_agg('
            'md5((to_jsonb(t) - %%L::text[])::text), '''' '
            'ORDER BY md5((to_jsonb(t) - %%L::text[])::text)), '''')) '
            'FROM public.%%I t', r.relname, ignored_columns, ignored_columns, r.relname);
    END LOOP;
END $$;

-- name: schema_table_fingerprints
SELECT * FROM pg_temp.table_fingerprints() ORDER BY table_name;

-- name: schema_prepare_non_graph_fingerprints
CREATE OR REPLACE FUNCTION pg_temp.non_graph_fingerprints()
RETURNS TABLE(table_name text, row_count bigint, fingerprint text)
LANGUAGE plpgsql AS $$
DECLARE r record; ignored_columns text[];
BEGIN
    FOR r IN SELECT c.relname FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
               AND c.relname <> 'schema_migrations' AND left(c.relname, 4) <> 'sim_'
             ORDER BY c.relname
    LOOP
        ignored_columns := CASE WHEN r.relname = 'sources'
                           THEN ARRAY['is_current', 'origin_key'] ELSE ARRAY[]::text[] END;
        RETURN QUERY EXECUTE format(
            'SELECT %%L::text, count(*)::bigint, md5(coalesce(string_agg('
            'md5((to_jsonb(t) - %%L::text[])::text), '''' '
            'ORDER BY md5((to_jsonb(t) - %%L::text[])::text)), '''')) '
            'FROM public.%%I t', r.relname, ignored_columns, ignored_columns, r.relname);
    END LOOP;
END $$;

-- name: schema_non_graph_fingerprints
SELECT * FROM pg_temp.non_graph_fingerprints() ORDER BY table_name;

-- name: schema_source_content_fingerprint
SELECT count(*) AS row_count,
       md5(coalesce(string_agg(md5((to_jsonb(s) - ARRAY['is_current','origin_key','updated_at'])::text),
                  '' ORDER BY md5((to_jsonb(s) - ARRAY['is_current','origin_key','updated_at'])::text)), ''))
           AS fingerprint
FROM sources s;

-- name: schema_sim_build_fingerprint
SELECT count(*) AS row_count,
       md5(coalesce(string_agg(md5(to_jsonb(s)::text), '' ORDER BY md5(to_jsonb(s)::text)), ''))
           AS fingerprint
FROM sim_builds s;

-- name: schema_foreign_key_violations
DO $$
DECLARE k record; joins text; nonnulls text; present text; broken boolean;
BEGIN
    FOR k IN
        SELECT oid, conname, conrelid, confrelid, conkey, confkey, confmatchtype
        FROM pg_constraint WHERE contype = 'f'
          AND connamespace = 'public'::regnamespace
    LOOP
        SELECT string_agg(format('s.%%I = t.%%I', a.attname, b.attname), ' AND ' ORDER BY x.n),
               string_agg(format('s.%%I IS NOT NULL', a.attname), ' AND ' ORDER BY x.n),
               string_agg(format('s.%%I IS NOT NULL', a.attname), ' OR ' ORDER BY x.n)
        INTO joins, nonnulls, present
        FROM unnest(k.conkey, k.confkey) WITH ORDINALITY AS x(a, b, n)
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = x.a
        JOIN pg_attribute b ON b.attrelid = k.confrelid AND b.attnum = x.b;
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %%s s WHERE (%%s) AND NOT EXISTS '
                       '(SELECT 1 FROM %%s t WHERE %%s))',
                       k.conrelid::regclass,
                       CASE WHEN k.confmatchtype = 'f' THEN present ELSE nonnulls END,
                       k.confrelid::regclass, joins) INTO broken;
        IF broken THEN
            RAISE EXCEPTION 'restored foreign key data failed validation: %%', k.conname;
        END IF;
    END LOOP;
END $$;
