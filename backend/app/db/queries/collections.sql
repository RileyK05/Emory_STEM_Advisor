-- name: insert_collection
INSERT INTO collections (collection_id, name)
VALUES (%(collection_id)s, %(name)s)
ON CONFLICT (name) DO NOTHING;

-- name: get_collection_by_name
SELECT collection_id, name, created_at
FROM collections
WHERE name = %(name)s;

-- name: list_collections
SELECT collection_id, name, created_at
FROM collections
ORDER BY name;
