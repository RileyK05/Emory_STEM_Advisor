-- Model pricing schedules, cache accounting, and call costs.
--
-- model_price_schedules defines versioned, dated pricing per provider/model.
-- model_calls persists which schedule priced each attempt, the estimated/measured
-- cost, and the cost status ('measured', 'estimated', 'unknown').

CREATE TABLE public.model_price_schedules (
    schedule_id uuid NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    currency text DEFAULT 'USD' NOT NULL,
    input_rate_per_million numeric(12, 6) NOT NULL,
    cached_read_rate_per_million numeric(12, 6) DEFAULT 0 NOT NULL,
    output_rate_per_million numeric(12, 6) NOT NULL,
    effective_from timestamp with time zone NOT NULL,
    effective_to timestamp with time zone,
    provenance text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_price_schedules_pkey PRIMARY KEY (schedule_id),
    CONSTRAINT model_price_schedules_currency_check CHECK (length(currency) BETWEEN 1 AND 10),
    CONSTRAINT model_price_schedules_rates_check CHECK (
        input_rate_per_million >= 0 AND
        cached_read_rate_per_million >= 0 AND
        output_rate_per_million >= 0
    ),
    CONSTRAINT model_price_schedules_dates_check CHECK (
        effective_to IS NULL OR effective_to > effective_from
    )
);

CREATE INDEX IF NOT EXISTS model_price_schedules_lookup_idx
    ON public.model_price_schedules (provider, model_id, effective_from DESC);

ALTER TABLE public.model_calls
    ADD COLUMN IF NOT EXISTS schedule_id uuid REFERENCES public.model_price_schedules (schedule_id),
    ADD COLUMN IF NOT EXISTS estimated_cost numeric(14, 8),
    ADD COLUMN IF NOT EXISTS cost_status text DEFAULT 'unknown' NOT NULL
        CHECK (cost_status IN ('measured', 'estimated', 'unknown'));

CREATE INDEX IF NOT EXISTS model_calls_created_cost_idx
    ON public.model_calls (created_at DESC, cost_status);

CREATE INDEX IF NOT EXISTS model_calls_model_created_idx
    ON public.model_calls (provider, model_id, created_at DESC);

