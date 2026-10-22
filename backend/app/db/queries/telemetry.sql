-- name: insert_price_schedule
INSERT INTO model_price_schedules (
    schedule_id, provider, model_id, currency, billing_mode,
    input_rate_per_million, cached_read_rate_per_million, output_rate_per_million,
    effective_from, effective_to, provenance
) VALUES (
    %(schedule_id)s, %(provider)s, %(model_id)s, %(currency)s, %(billing_mode)s,
    %(input_rate_per_million)s, %(cached_read_rate_per_million)s, %(output_rate_per_million)s,
    %(effective_from)s, %(effective_to)s, %(provenance)s
) RETURNING *;

-- name: list_price_schedules
SELECT schedule_id, provider, model_id, currency, billing_mode,
       input_rate_per_million, cached_read_rate_per_million, output_rate_per_million,
       effective_from, effective_to, provenance, created_at
FROM model_price_schedules
ORDER BY effective_from DESC, created_at DESC;

-- name: find_active_price_schedule
SELECT schedule_id, provider, model_id, currency, billing_mode,
       input_rate_per_million, cached_read_rate_per_million, output_rate_per_million,
       effective_from, effective_to, provenance
FROM model_price_schedules
WHERE provider = %(provider)s
  AND billing_mode = %(billing_mode)s
  AND (model_id = %(model_id)s OR model_id = '*')
  AND effective_from <= %(timestamp)s
  AND (effective_to IS NULL OR effective_to > %(timestamp)s)
ORDER BY (CASE WHEN model_id = %(model_id)s THEN 0 ELSE 1 END), effective_from DESC,
         created_at DESC, schedule_id DESC
LIMIT 1;

-- name: update_model_call_cost
UPDATE model_calls
SET schedule_id = %(schedule_id)s,
    estimated_cost = %(estimated_cost)s,
    cost_status = %(cost_status)s
WHERE model_call_id = %(model_call_id)s;

-- name: telemetry_usage_summary
SELECT
    count(*) AS total_calls,
    count(*) FILTER (WHERE status = 'completed') AS completed_calls,
    count(*) FILTER (WHERE status = 'failed') AS failed_calls,
    sum(prompt_tokens) AS total_prompt_tokens,
    sum(completion_tokens) AS total_completion_tokens,
    sum(cached_prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS total_cached_tokens,
    sum(prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS cache_observed_prompt_tokens,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens) AS calls_reporting_cache,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 1 AND prompt_tokens) AS calls_with_cache_hits,
    count(*) FILTER (WHERE prompt_tokens IS NOT NULL) AS calls_reporting_tokens,
    sum(estimated_cost) AS total_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'measured') AS measured_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'estimated') AS estimated_cost,
    count(*) FILTER (WHERE cost_status = 'unknown') AS unknown_cost_calls,
    array_agg(DISTINCT coalesce(s.currency, 'unknown'))
        FILTER (WHERE estimated_cost IS NOT NULL) AS cost_currencies
FROM model_calls m
LEFT JOIN model_price_schedules s ON s.schedule_id = m.schedule_id
WHERE (%(since)s::timestamptz IS NULL OR m.created_at >= %(since)s::timestamptz)
  AND (%(until)s::timestamptz IS NULL OR m.created_at <= %(until)s::timestamptz)
  AND (%(provider)s = '' OR m.provider = %(provider)s)
  AND (%(model_id)s = '' OR m.model_id = %(model_id)s);

-- name: telemetry_usage_by_date
SELECT
    date_trunc('day', m.created_at) AS bucket,
    count(*) AS total_calls,
    count(*) FILTER (WHERE status = 'completed') AS completed_calls,
    count(*) FILTER (WHERE status = 'failed') AS failed_calls,
    sum(prompt_tokens) AS total_prompt_tokens,
    sum(completion_tokens) AS total_completion_tokens,
    sum(cached_prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS total_cached_tokens,
    sum(prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS cache_observed_prompt_tokens,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens) AS calls_reporting_cache,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 1 AND prompt_tokens) AS calls_with_cache_hits,
    sum(estimated_cost) AS total_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'measured') AS measured_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'estimated') AS estimated_cost,
    count(*) FILTER (WHERE cost_status = 'unknown') AS unknown_cost_calls,
    array_agg(DISTINCT coalesce(s.currency, 'unknown'))
        FILTER (WHERE estimated_cost IS NOT NULL) AS cost_currencies
FROM model_calls m
LEFT JOIN model_price_schedules s ON s.schedule_id = m.schedule_id
WHERE (%(since)s::timestamptz IS NULL OR m.created_at >= %(since)s::timestamptz)
  AND (%(until)s::timestamptz IS NULL OR m.created_at <= %(until)s::timestamptz)
  AND (%(provider)s = '' OR m.provider = %(provider)s)
  AND (%(model_id)s = '' OR m.model_id = %(model_id)s)
GROUP BY date_trunc('day', m.created_at)
ORDER BY bucket DESC
LIMIT %(limit)s;

-- name: telemetry_usage_by_model
SELECT
    coalesce(m.provider, 'unknown') || ':' || coalesce(m.model_id, 'unknown') AS bucket,
    m.provider,
    m.model_id,
    count(*) AS total_calls,
    count(*) FILTER (WHERE status = 'completed') AS completed_calls,
    count(*) FILTER (WHERE status = 'failed') AS failed_calls,
    sum(prompt_tokens) AS total_prompt_tokens,
    sum(completion_tokens) AS total_completion_tokens,
    sum(cached_prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS total_cached_tokens,
    sum(prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS cache_observed_prompt_tokens,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens) AS calls_reporting_cache,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 1 AND prompt_tokens) AS calls_with_cache_hits,
    sum(estimated_cost) AS total_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'measured') AS measured_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'estimated') AS estimated_cost,
    count(*) FILTER (WHERE cost_status = 'unknown') AS unknown_cost_calls,
    array_agg(DISTINCT coalesce(s.currency, 'unknown'))
        FILTER (WHERE estimated_cost IS NOT NULL) AS cost_currencies
FROM model_calls m
LEFT JOIN model_price_schedules s ON s.schedule_id = m.schedule_id
WHERE (%(since)s::timestamptz IS NULL OR m.created_at >= %(since)s::timestamptz)
  AND (%(until)s::timestamptz IS NULL OR m.created_at <= %(until)s::timestamptz)
AND (%(provider)s = '' OR m.provider = %(provider)s)
  AND (%(model_id)s = '' OR m.model_id = %(model_id)s)
GROUP BY m.provider, m.model_id
ORDER BY total_calls DESC
LIMIT %(limit)s;

-- name: telemetry_usage_by_operation
SELECT
    coalesce(m.operation_id::text, 'no_operation') AS bucket,
    count(*) AS total_calls,
    count(*) FILTER (WHERE status = 'completed') AS completed_calls,
    count(*) FILTER (WHERE status = 'failed') AS failed_calls,
    sum(prompt_tokens) AS total_prompt_tokens,
    sum(completion_tokens) AS total_completion_tokens,
    sum(cached_prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS total_cached_tokens,
    sum(prompt_tokens) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens)
        AS cache_observed_prompt_tokens,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 0 AND prompt_tokens) AS calls_reporting_cache,
    count(*) FILTER (WHERE cached_prompt_tokens BETWEEN 1 AND prompt_tokens) AS calls_with_cache_hits,
    sum(estimated_cost) AS total_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'measured') AS measured_cost,
    sum(estimated_cost) FILTER (WHERE cost_status = 'estimated') AS estimated_cost,
    count(*) FILTER (WHERE cost_status = 'unknown') AS unknown_cost_calls,
    array_agg(DISTINCT coalesce(s.currency, 'unknown'))
        FILTER (WHERE estimated_cost IS NOT NULL) AS cost_currencies
FROM model_calls m
LEFT JOIN model_price_schedules s ON s.schedule_id = m.schedule_id
WHERE (%(since)s::timestamptz IS NULL OR m.created_at >= %(since)s::timestamptz)
  AND (%(until)s::timestamptz IS NULL OR m.created_at <= %(until)s::timestamptz)
AND (%(provider)s = '' OR m.provider = %(provider)s)
  AND (%(model_id)s = '' OR m.model_id = %(model_id)s)
GROUP BY m.operation_id
ORDER BY total_calls DESC
LIMIT %(limit)s;

