-- name: insert_trace
INSERT INTO retrieval_traces (
    trace_id, collection_id, query, normalized_query, config_version,
    embed_model, refused
)
VALUES (
    %(trace_id)s, %(collection_id)s, %(query)s, %(normalized_query)s,
    %(config_version)s, %(embed_model)s, %(refused)s
);

-- name: insert_trace_seam
INSERT INTO trace_seams (
    trace_id, seam, enabled, state, reason, candidate_count
)
VALUES (
    %(trace_id)s, %(seam)s, %(enabled)s, %(state)s, %(reason)s,
    %(candidate_count)s
);

-- name: insert_trace_contribution
INSERT INTO trace_contributions (
    trace_id, chunk_id, seams, normalized_score, source_slot, rank
)
VALUES (
    %(trace_id)s, %(chunk_id)s, %(seams)s, %(normalized_score)s,
    %(source_slot)s, %(rank)s
);
