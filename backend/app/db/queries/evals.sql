-- name: exists_chunk_label
SELECT 1
FROM chunks c
JOIN sources s  ON s.source_id = c.source_id
JOIN locators l ON l.locator_id = c.locator_id
WHERE s.collection_id = %(collection_id)s
  AND s.filename = %(filename)s
  AND l.label = %(locator_label)s
LIMIT 1;

-- name: chunk_labels
SELECT c.chunk_id, s.filename AS source_name, l.label AS locator_label
FROM chunks c
JOIN sources s  ON s.source_id = c.source_id
JOIN locators l ON l.locator_id = c.locator_id
WHERE c.chunk_id = ANY(%(chunk_ids)s);
