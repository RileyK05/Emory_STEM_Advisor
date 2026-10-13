ALTER TABLE conversation_requests
    ADD COLUMN job_id uuid UNIQUE REFERENCES jobs(job_id);

CREATE INDEX conversation_requests_job_idx
    ON conversation_requests(job_id)
    WHERE job_id IS NOT NULL;
